from decimal import Decimal

from app.models import (
    AppDataRecord,
    CorporateTaxRecord,
    InventoryValuationLayer,
    JournalEntry,
    SourceTransaction,
    StockMovement,
    StockProductMapping,
)


def test_inventory_unit_conversion_records(client, auth_headers):
    unit_box = client.post(
        "/api/v1/item-units",
        headers=auth_headers,
        json={
            "item_code": "COKE",
            "unit_code": "BOX",
            "unit_name": "Box",
            "conversion_factor": "12",
            "is_base_unit": False,
            "purchase_default": True,
            "sales_default": False,
        },
    )
    unit_pcs = client.post(
        "/api/v1/item-units",
        headers=auth_headers,
        json={
            "item_code": "COKE",
            "unit_code": "PCS",
            "unit_name": "Pieces",
            "conversion_factor": "1",
            "is_base_unit": True,
            "purchase_default": False,
            "sales_default": True,
        },
    )
    conversion = client.post(
        "/api/v1/item-unit-conversions",
        headers=auth_headers,
        json={"item_code": "COKE", "from_unit_code": "BOX", "to_unit_code": "PCS", "conversion_factor": "12"},
    )

    assert unit_box.status_code == 201
    assert unit_pcs.status_code == 201
    assert conversion.status_code == 201
    assert Decimal(conversion.json()["conversion_factor"]) == Decimal("12.0000")


def test_purchase_record_syncs_line_quantity_to_stock_tables(client, auth_headers, db):
    payload = {
        "collection": "purchaseRecords",
        "record": {
            "ref": "PUR-QTY-008",
            "supplier": "QA Supplier",
            "status": "Pending Payment",
            "net_amount": 1480,
            "tax_amount": 74,
            "total": 1554,
            "lines": [
                {
                    "sku": "PAPER-A4",
                    "product": "A4 Paper Box",
                    "unit_of_measure": "BOX",
                    "quantity": 8,
                    "unit_cost": 185,
                    "unit_cost_before_tax": 185,
                    "line_total": 1480,
                }
            ],
        },
    }

    response = client.post("/api/v1/app-data?action=save", headers=auth_headers, json=payload)

    assert response.status_code == 200
    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "PAPER-A4").one()
    movement = db.query(StockMovement).filter(StockMovement.mapping_id == mapping.id).one()
    layer = db.query(InventoryValuationLayer).filter(InventoryValuationLayer.item_code == "PAPER-A4").one()
    assert movement.reference == "PUR-QTY-008"
    assert movement.quantity == Decimal("8.00")
    assert layer.quantity_in == Decimal("8.00")
    assert layer.quantity_remaining == Decimal("8.00")
    source = (
        db.query(SourceTransaction)
        .filter(SourceTransaction.module == "purchase", SourceTransaction.reference == "PUR-QTY-008")
        .one()
    )
    assert source.status == "posted"
    assert source.subtotal == Decimal("1480.00")
    journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.source_module == "purchase", JournalEntry.source_id == source.id)
        .one()
    )
    assert journal.status == "posted"
    corporate_tax = db.query(CorporateTaxRecord).order_by(CorporateTaxRecord.created_at.desc()).first()
    assert corporate_tax is not None
    assert corporate_tax.status == "calculated"

    stock_levels = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    assert stock_levels.status_code == 200
    stock_level = next(row for row in stock_levels.json() if row["code"] == "PAPER-A4")
    assert Decimal(str(stock_level["current_stock"])) == Decimal("8.00")

    product = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "products",
            "record": {"code": "INV-CLEAR", "name": "Inventory Clear Item", "category": "QA", "unit": "PCS"},
        },
    )
    assert product.status_code == 200

    cleared = client.delete("/api/v1/inventory/stock-levels", headers=auth_headers)
    assert cleared.status_code == 200
    assert cleared.json()["deleted"]["stock_movements"] >= 1
    assert cleared.json()["deleted"]["products"] >= 1

    after_clear = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    assert after_clear.status_code == 200
    assert after_clear.json() == []
    assert db.query(AppDataRecord).filter(AppDataRecord.collection == "products").count() == 0


def test_pos_sale_syncs_negative_stock_movement(client, auth_headers, db):
    """Regression test: pos.html's completeSale() previously wrote its stock
    deduction to a generic 'stockMovements' AppDataRecord collection with no
    sync_domain_model branch behind it — the real StockMovement table (what
    GET /inventory/stock-levels actually sums) never reflected a single POS
    sale, so reported stock only ever went up (via purchases), never down."""
    purchase = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-POS-STOCK-001",
                "supplier": "QA Supplier",
                "status": "Paid",
                "net_amount": 500,
                "tax_amount": 25,
                "total": 525,
                "lines": [
                    {"sku": "POS-STOCK-SKU", "product": "POS Stock Test Item", "quantity": 10, "unit_cost": 50, "unit_cost_before_tax": 50, "line_total": 500}
                ],
            },
        },
    )
    assert purchase.status_code == 200, purchase.text

    stock_after_purchase = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row = next(r for r in stock_after_purchase.json() if r["code"] == "POS-STOCK-SKU")
    assert Decimal(str(row["current_stock"])) == Decimal("10.00")

    sale = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "posSales",
            "record": {
                "id": "POS-STOCK-RCPT-001",
                "receipt_no": "POS-STOCK-RCPT-001",
                "customer": "Walk-In Customer",
                "items": [{"code": "POS-STOCK-SKU", "name": "POS Stock Test Item", "qty": 3, "price": 80, "unit": "PCS"}],
                "subtotal": 240, "vat": 12, "total": 252,
                "payment_method": "cash", "status": "completed",
            },
        },
    )
    assert sale.status_code == 200, sale.text

    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "POS-STOCK-SKU").one()
    pos_movement = (
        db.query(StockMovement)
        .filter(StockMovement.mapping_id == mapping.id, StockMovement.movement_type == "pos_sale")
        .one()
    )
    assert pos_movement.reference == "POS-STOCK-RCPT-001"
    assert pos_movement.quantity == Decimal("-3.00")

    stock_after_sale = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row_after = next(r for r in stock_after_sale.json() if r["code"] == "POS-STOCK-SKU")
    assert Decimal(str(row_after["current_stock"])) == Decimal("7.00")

    # Re-saving the same sale (idempotent re-sync, e.g. an edit/retry) must
    # not double-deduct — delete-then-recreate-by-reference, same as
    # sync_purchase_stock()'s existing pattern.
    resave = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "posSales",
            "record": {
                "id": "POS-STOCK-RCPT-001",
                "receipt_no": "POS-STOCK-RCPT-001",
                "customer": "Walk-In Customer",
                "items": [{"code": "POS-STOCK-SKU", "name": "POS Stock Test Item", "qty": 3, "price": 80, "unit": "PCS"}],
                "subtotal": 240, "vat": 12, "total": 252,
                "payment_method": "cash", "status": "completed",
            },
        },
    )
    assert resave.status_code == 200, resave.text
    stock_after_resave = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row_after_resave = next(r for r in stock_after_resave.json() if r["code"] == "POS-STOCK-SKU")
    assert Decimal(str(row_after_resave["current_stock"])) == Decimal("7.00")


def test_exception_center_accepts_manual_exception(client, auth_headers):
    created = client.post(
        "/api/v1/exceptions",
        headers=auth_headers,
        json={
            "module": "Accounting",
            "category": "Failed posting",
            "severity": "high",
            "source_record": "QA-JOB-001",
            "message": "Posting failed during QA simulation",
        },
    )
    assert created.status_code == 201

    listed = client.get("/api/v1/exceptions", headers=auth_headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["summary"]["high"] >= 1
    assert any(row["source_record"] == "QA-JOB-001" for row in payload["exceptions"])


def test_exception_center_flags_unconfirmed_mapping_not_empty_account_codes(client, auth_headers, db):
    """exception_center.py used to flag a stock mapping only when its
    sales/purchase/inventory account codes were empty — but those columns
    all have non-empty SQLAlchemy defaults ("3000"/"4000"/"1200") applied at
    insert time for every mapping, including auto-created/unreviewed ones,
    so that check could effectively never fire. The real "needs review"
    signal is mapping_confirmed, already used by the Stock Mapping UI."""
    from app.models import User

    company_id = db.query(User).filter(User.email == "qa-admin@taxflowqa.com").first().company_id

    unconfirmed = StockProductMapping(
        company_id=company_id, sku="EXC-UNMAPPED-SKU", name="Exception QA Unmapped Item", mapping_confirmed=False,
    )
    confirmed = StockProductMapping(
        company_id=company_id, sku="EXC-MAPPED-SKU", name="Exception QA Mapped Item", mapping_confirmed=True,
    )
    db.add_all([unconfirmed, confirmed])
    db.commit()

    listed = client.get("/api/v1/exceptions", headers=auth_headers)
    assert listed.status_code == 200
    rows = listed.json()["exceptions"]
    unmapped_rows = [r for r in rows if r["category"] == "Unmapped stock item"]
    flagged_skus = {r["source_record"] for r in unmapped_rows}
    assert "EXC-UNMAPPED-SKU" in flagged_skus
    assert "EXC-MAPPED-SKU" not in flagged_skus


def test_stock_mapping_auto_created_from_purchase_is_unconfirmed(client, auth_headers, db):
    """A mapping silently auto-created as a side effect of saving a purchase
    record (no user ever visited Stock Mapping) must read as unconfirmed —
    the frontend shows this as "Review", never "Mapped" — until a user
    explicitly saves it via POST/PUT /inventory/mappings."""
    payload = {
        "collection": "purchaseRecords",
        "record": {
            "ref": "PUR-MAP-CONFIRM-001",
            "supplier": "Nova Pharma Trading",
            "net_amount": 100,
            "tax_amount": 5,
            "total": 105,
            "lines": [
                {
                    "sku": "MAP-CONFIRM-SKU",
                    "product": "Auto Mapped Item",
                    "quantity": 2,
                    "unit_cost": 50,
                    "unit_cost_before_tax": 50,
                    "line_total": 100,
                }
            ],
        },
    }
    r = client.post("/api/v1/app-data?action=save", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text

    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "MAP-CONFIRM-SKU").one()
    assert mapping.mapping_confirmed is False

    mappings = client.get("/api/v1/inventory/mappings", headers=auth_headers)
    assert mappings.status_code == 200
    row = next(m for m in mappings.json() if m["sku"] == "MAP-CONFIRM-SKU")
    assert row["mapping_confirmed"] is False

    # A second purchase referencing the same SKU with different product text
    # must still refresh the name (still unconfirmed) — auto-refresh only
    # stops once a user has explicitly confirmed it.
    payload2 = {
        "collection": "purchaseRecords",
        "record": {
            "ref": "PUR-MAP-CONFIRM-002",
            "supplier": "Nova Pharma Trading",
            "net_amount": 50,
            "tax_amount": 2.5,
            "total": 52.5,
            "lines": [{"sku": "MAP-CONFIRM-SKU", "product": "Renamed Before Confirmation", "quantity": 1, "unit_cost": 50, "line_total": 50}],
        },
    }
    r2 = client.post("/api/v1/app-data?action=save", headers=auth_headers, json=payload2)
    assert r2.status_code == 200, r2.text
    db.refresh(mapping)
    assert mapping.name == "Renamed Before Confirmation"
    assert mapping.mapping_confirmed is False

    # Explicit user save via PUT /inventory/mappings/{id} confirms it.
    confirm = client.put(
        f"/api/v1/inventory/mappings/{mapping.id}",
        headers=auth_headers,
        json={"sku": "MAP-CONFIRM-SKU", "name": "User Confirmed Name", "taxflow_name": "User Confirmed Name"},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["mapping_confirmed"] is True

    # A third purchase referencing the same SKU must NOT overwrite the name
    # a user just confirmed.
    payload3 = {
        "collection": "purchaseRecords",
        "record": {
            "ref": "PUR-MAP-CONFIRM-003",
            "supplier": "Nova Pharma Trading",
            "net_amount": 20,
            "tax_amount": 1,
            "total": 21,
            "lines": [{"sku": "MAP-CONFIRM-SKU", "product": "Should Not Overwrite", "quantity": 1, "unit_cost": 20, "line_total": 20}],
        },
    }
    r3 = client.post("/api/v1/app-data?action=save", headers=auth_headers, json=payload3)
    assert r3.status_code == 200, r3.text
    db.refresh(mapping)
    assert mapping.name == "User Confirmed Name"


def test_stock_tracking_no_skips_movements_but_keeps_item_visible(client, auth_headers, db):
    """Item Master's "Stock Tracking" field (Yes/No/Optional) previously did
    nothing — every code path that checked it only picked a badge color, and
    the backend never looked at it at all, so a "No"-tracked item was
    deducted/added to exactly like any other. "No"/"Optional" should mean:
    the item stays visible in Stock Levels (current_stock stays whatever it
    already was — 0 for a never-purchased item), but purchases/POS sales
    don't create StockMovement rows or move its quantity."""
    product = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "products",
            "record": {"code": "NO-TRACK-SKU", "name": "Non-Tracked Item", "category": "QA", "unit": "PCS", "tracking": "No"},
        },
    )
    assert product.status_code == 200, product.text
    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "NO-TRACK-SKU").one()
    assert mapping.tracking == "No"

    purchase = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-NOTRACK-001",
                "supplier": "QA Supplier",
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "lines": [{"sku": "NO-TRACK-SKU", "product": "Non-Tracked Item", "quantity": 10, "unit_cost": 10, "unit_cost_before_tax": 10, "line_total": 100}],
            },
        },
    )
    assert purchase.status_code == 200, purchase.text
    assert db.query(StockMovement).filter(StockMovement.mapping_id == mapping.id).count() == 0
    assert db.query(InventoryValuationLayer).filter(InventoryValuationLayer.item_code == "NO-TRACK-SKU").count() == 0

    # Item still appears in Stock Levels — just with a quantity of 0, not
    # hidden — proving the outer-join in list_stock_levels() still surfaces
    # a mapping with zero StockMovement rows.
    stock_levels = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row = next(r for r in stock_levels.json() if r["code"] == "NO-TRACK-SKU")
    assert Decimal(str(row["current_stock"])) == Decimal("0.00")

    sale = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "posSales",
            "record": {
                "id": "POS-NOTRACK-001",
                "receipt_no": "POS-NOTRACK-001",
                "customer": "Walk-In Customer",
                "items": [{"code": "NO-TRACK-SKU", "name": "Non-Tracked Item", "qty": 2, "price": 20, "unit": "PCS"}],
                "subtotal": 40, "vat": 2, "total": 42,
                "payment_method": "cash", "status": "completed",
            },
        },
    )
    assert sale.status_code == 200, sale.text
    assert db.query(StockMovement).filter(StockMovement.mapping_id == mapping.id).count() == 0

    stock_after_sale = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row_after = next(r for r in stock_after_sale.json() if r["code"] == "NO-TRACK-SKU")
    assert Decimal(str(row_after["current_stock"])) == Decimal("0.00")

    # A normal tracking="Yes" item (the default) must be completely
    # unaffected by this change — regression check.
    tracked_purchase = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-YESTRACK-001",
                "supplier": "QA Supplier",
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "lines": [{"sku": "YES-TRACK-SKU", "product": "Tracked Item", "quantity": 4, "unit_cost": 25, "unit_cost_before_tax": 25, "line_total": 100}],
            },
        },
    )
    assert tracked_purchase.status_code == 200, tracked_purchase.text
    stock_yes = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row_yes = next(r for r in stock_yes.json() if r["code"] == "YES-TRACK-SKU")
    assert Decimal(str(row_yes["current_stock"])) == Decimal("4.00")


def test_low_confidence_ai_extraction_saves_purchase_without_minting_product(client, auth_headers, db):
    """A low-confidence/error-flagged AI-extracted purchase (Purchases > AI
    Extraction) previously auto-created a brand-new, unconfirmed
    StockProductMapping from whatever (possibly wrong) product text was
    extracted — exactly like a clean extraction would. purchaseRecordFromExtractedInvoice()
    now stamps needs_product_review on the saved record when confidence is
    low or the row was error-flagged; sync_purchase_stock() must save the
    purchase itself (supplier/amount/date all real) but skip auto-creating
    a product for an SKU/name it doesn't already recognize."""
    purchase = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-LOWCONF-001",
                "supplier": "Uncertain Extraction Supplier",
                "net_amount": 200,
                "tax_amount": 10,
                "total": 210,
                "needs_product_review": True,
                "lines": [{"sku": "MAYBE-WRONG-SKU", "product": "Possibly Misread Item", "quantity": 5, "unit_cost": 40, "unit_cost_before_tax": 40, "line_total": 200}],
            },
        },
    )
    assert purchase.status_code == 200, purchase.text

    # The purchase record itself must still be a real, saved purchase.
    purchases = client.get("/api/v1/app-data/records/purchaseRecords", headers=auth_headers).json()
    saved = next(r for r in purchases["records"] if r.get("ref") == "PUR-LOWCONF-001")
    assert saved["supplier"] == "Uncertain Extraction Supplier"
    assert Decimal(str(saved["total"])) == Decimal("210")

    # No product/mapping/movement must have been minted from the uncertain line.
    assert db.query(StockProductMapping).filter(StockProductMapping.sku == "MAYBE-WRONG-SKU").count() == 0
    stock_levels = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    assert not any(r["code"] == "MAYBE-WRONG-SKU" for r in stock_levels.json())

    # If the SKU already exists (e.g. from a confident extraction/manual
    # entry earlier), a later low-confidence purchase referencing it must
    # still record a normal stock movement — allow_create only blocks
    # inventing a NEW item, not using an already-known one.
    existing_product = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "products", "record": {"code": "ALREADY-KNOWN-SKU", "name": "Already Known Item", "category": "QA", "unit": "PCS"}},
    )
    assert existing_product.status_code == 200, existing_product.text
    second_purchase = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-LOWCONF-002",
                "supplier": "Uncertain Extraction Supplier",
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "needs_product_review": True,
                "lines": [{"sku": "ALREADY-KNOWN-SKU", "product": "Already Known Item", "quantity": 3, "unit_cost": 33.33, "unit_cost_before_tax": 33.33, "line_total": 100}],
            },
        },
    )
    assert second_purchase.status_code == 200, second_purchase.text
    stock_known = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    row_known = next(r for r in stock_known.json() if r["code"] == "ALREADY-KNOWN-SKU")
    assert Decimal(str(row_known["current_stock"])) == Decimal("3.00")
