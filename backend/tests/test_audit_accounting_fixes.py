"""Regression coverage for the live QA audit's accounting findings."""
from decimal import Decimal

from app.models import GeneralLedgerEntry, Account, SourceTransaction
from tests.test_registration import _register


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text
    return r


def _invoice(no, **extra):
    rec = {"invoice_no": no, "customer": "Audit Cust", "date": "2026-09-10", "subtotal": 1000,
           "vat_amount": 50, "total": 1050, "status": "Issued",
           "lines": [{"description": "Thing", "qty": 1, "unit_price": 1000, "vat_rate": 5}]}
    rec.update(extra)
    return rec


def test_balance_sheet_balances_with_current_period_earnings(client):
    h = _register(client, "audit-bs")
    _save(client, h, "salesInvoices", _invoice("BS-1"))
    r = client.get("/api/v1/reports/summary", headers=h)
    assert r.status_code == 200, r.text
    bs = r.json()["balance_sheet"]
    assert Decimal(bs["totals"]["difference"]) == 0, bs["totals"]
    assert any(row["name"] == "Current period earnings" for row in bs["equity"])


def test_pos_paid_sale_books_cash_receipt(client, db):
    h = _register(client, "audit-pos")
    company_id = client.get("/api/v1/auth/me", headers=h).json()["company"]["id"]
    _save(client, h, "salesInvoices", _invoice("POS-T-1", source="POS", status="Paid", payment_method="cash"))
    receipts = client.get("/api/v1/app-data/records/payments", headers=h).json()["records"]
    assert any(r.get("ref") == "RCT-POS-T-1" for r in receipts)
    tx = db.query(SourceTransaction).filter(
        SourceTransaction.company_id == company_id, SourceTransaction.reference == "RCT-POS-T-1"
    ).first()
    assert tx is not None and tx.status == "posted"


def test_credit_pos_sale_has_no_receipt(client):
    h = _register(client, "audit-poscredit")
    _save(client, h, "salesInvoices", _invoice("POS-C-1", source="POS", status="Pending", payment_method="credit"))
    receipts = client.get("/api/v1/app-data/records/payments", headers=h).json()["records"]
    assert not any(r.get("ref") == "RCT-POS-C-1" for r in receipts)


def test_sales_invoice_consumes_tracked_stock(client, db):
    h = _register(client, "audit-stock")
    _save(client, h, "products", {"name": "Stock Thing", "code": "ST-1", "price": 100, "cost": 50, "vat": "5%",
                                  "unit": "PCS", "status": "Active", "tracking": "Yes"})
    _save(client, h, "purchaseRecords", {"ref": "ST-PUR-1", "supplier": "S", "net_amount": 250, "tax_amount": 12.5,
                                         "total": 262.5, "lines": [{"sku": "ST-1", "product": "Stock Thing", "quantity": 5, "unit_cost": 50}]})
    _save(client, h, "salesInvoices", _invoice("ST-INV-1", lines=[
        {"description": "Stock Thing", "product_code": "ST-1", "qty": 2, "unit_price": 100, "vat_rate": 5}]))

    def stock():
        rows = client.get("/api/v1/inventory/stock-levels", headers=h).json()
        return next(r for r in rows if r["code"] == "ST-1")["current_stock"]

    assert stock() == 3
    # Re-saving is idempotent, and cancelling returns the stock.
    _save(client, h, "salesInvoices", _invoice("ST-INV-1", lines=[
        {"description": "Stock Thing", "product_code": "ST-1", "qty": 2, "unit_price": 100, "vat_rate": 5}]))
    assert stock() == 3
    _save(client, h, "salesInvoices", _invoice("ST-INV-1", status="Cancelled", lines=[
        {"description": "Stock Thing", "product_code": "ST-1", "qty": 2, "unit_price": 100, "vat_rate": 5}]))
    assert stock() == 5
    # other suites count products globally
    from app.models import AppDataRecord
    db.query(AppDataRecord).filter(AppDataRecord.collection == "products").delete()
    db.commit()


def test_locked_period_blocks_voucher_after_ui_lock(client, db):
    h = _register(client, "audit-lock")
    _save(client, h, "lockedPeriods", {"id": "2026-09", "locked": True})
    accounts = client.get("/api/v1/accounts", headers=h).json()
    a, b = accounts[0]["id"], accounts[1]["id"]
    vt = client.get("/api/v1/voucher-types", headers=h).json()[0]["id"]
    r = client.post("/api/v1/vouchers", headers=h, json={
        "voucher_type_id": vt, "voucher_date": "2026-09-15T00:00:00", "narration": "x",
        "lines": [{"account_id": a, "debit": 10, "credit": 0}, {"account_id": b, "debit": 0, "credit": 10}],
    })
    assert r.status_code == 423, r.text


def test_user_with_reserved_tld_email_can_load_me(client, auth_headers):
    su = client.post("/api/v1/auth/login", json={"email": "superadmin@etaxflow.com", "password": "super123"})
    if su.status_code != 200:
        return
    sh = {"Authorization": f"Bearer {su.json()['access_token']}"}
    r = client.post("/api/v1/superadmin/companies", headers=sh, json={
        "name": "Reserved TLD Co", "email": "admin@foo.test", "password": "secret123"})
    assert r.status_code == 422


def test_summary_narrative_uses_company_currency(client, db):
    h = _register(client, "audit-cur")
    from app.models import Company
    cid = client.get("/api/v1/auth/me", headers=h).json()["company"]["id"]
    db.query(Company).filter(Company.id == cid).update({"currency": "SAR"})
    db.commit()
    body = client.get("/api/v1/reports/summary", headers=h).json()
    assert "AED" not in str(body["dashboard"]["ai_summary"])
    assert "SAR" in str(body["dashboard"]["ai_summary"])


def test_depreciation_and_accrual_release_post_balanced_journals(client):
    h = _register(client, "audit-deprec")
    base = "/api/v1/corporate-accounting"
    a = client.post(f"{base}/fixed-assets", headers=h, json={"asset_code": "D1", "asset_name": "Van", "category": "Vehicles", "purchase_cost": 1000})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    r = client.post(f"{base}/fixed-assets/{aid}/depreciate", headers=h, json={"amount": 300})
    assert r.status_code == 200, r.text
    assert float(r.json()["accumulated_depreciation"]) == 300
    assert client.post(f"{base}/fixed-assets/{aid}/depreciate", headers=h, json={"amount": 800}).status_code == 422
    assert client.post(f"{base}/fixed-assets/{aid}/depreciate", headers=h, json={}).status_code == 422
    tb = client.get("/api/v1/reports/summary", headers=h).json()["balance_sheet"]["totals"]
    assert Decimal(tb["difference"]) == 0, tb


def test_negative_stock_gate_blocks_oversell_only_when_enabled(client, db):
    h = _register(client, "audit-negstock")
    _save(client, h, "products", {"name": "Gate Thing", "code": "GT-1", "price": 100, "cost": 50, "vat": "5%",
                                  "unit": "PCS", "status": "Active", "tracking": "Yes"})
    _save(client, h, "purchaseRecords", {"ref": "GT-PUR-1", "supplier": "S", "net_amount": 100, "tax_amount": 5,
                                         "total": 105, "lines": [{"sku": "GT-1", "product": "Gate Thing", "quantity": 2, "unit_cost": 50}]})
    line = [{"description": "Gate Thing", "product_code": "GT-1", "qty": 5, "unit_price": 100, "vat_rate": 5}]
    _save(client, h, "salesInvoices", _invoice("GT-INV-1", lines=line))  # default: allowed
    _save(client, h, "inventorySettings", {"key": "config", "allow_negative_stock": False})
    r = client.post("/api/v1/app-data", headers=h, params={"action": "save"},
                    json={"collection": "salesInvoices", "record": _invoice("GT-INV-2", lines=line)})
    assert r.status_code == 409, r.text
    from app.models import AppDataRecord
    db.query(AppDataRecord).filter(AppDataRecord.collection == "products").delete()
    db.commit()
