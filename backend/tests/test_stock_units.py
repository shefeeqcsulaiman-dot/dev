"""Purchases of a mapped outer pack count stock in the item's own units (app/stock_units.py)."""
from decimal import Decimal

from app.models import InventoryValuationLayer, StockMovement, StockProductMapping


def _save_purchase(client, auth_headers, ref, sku, product, unit, quantity, unit_cost):
    total = quantity * unit_cost
    response = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref,
                "supplier": "Pack Supplier",
                "date": "2026-10-10",
                "status": "Pending Payment",
                "net_amount": total,
                "tax_amount": 0,
                "total": total,
                "lines": [{
                    "sku": sku,
                    "product": product,
                    "unit_of_measure": unit,
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                    "unit_cost_before_tax": unit_cost,
                    "line_total": total,
                }],
            },
        },
    )
    assert response.status_code == 200, response.text


def _map(client, auth_headers, sku, name, units_per_outer):
    response = client.post(
        "/api/v1/inventory/mappings",
        headers=auth_headers,
        json={"sku": sku, "name": name, "taxflow_name": name, "units_per_outer": units_per_outer},
    )
    assert response.status_code == 201, response.text


def test_purchase_of_mapped_outer_counts_stock_in_units(client, auth_headers, db):
    _map(client, auth_headers, "COLA-CTN", "Cola Can 330ml", "24")
    _save_purchase(client, auth_headers, "PUR-PACK-001", "COLA-CTN", "Cola Can 330ml", "CTN", 2, 120)

    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "COLA-CTN").one()
    movement = db.query(StockMovement).filter(StockMovement.mapping_id == mapping.id).one()
    assert movement.quantity == Decimal("48.00")
    assert movement.unit_cost == Decimal("5.0000")
    assert movement.source_quantity == Decimal("2.00")
    assert movement.source_unit == "CTN"
    assert movement.units_per_outer == Decimal("24.0000")
    layer = db.query(InventoryValuationLayer).filter(InventoryValuationLayer.item_code == "COLA-CTN").one()
    assert layer.quantity_in == Decimal("48.00")
    assert layer.quantity_remaining == Decimal("48.00")
    assert layer.unit_cost == Decimal("5.0000")

    levels = client.get("/api/v1/inventory/stock-levels", headers=auth_headers)
    assert levels.status_code == 200
    row = next(r for r in levels.json() if r["code"] == "COLA-CTN")
    assert Decimal(str(row["current_stock"])) == Decimal("48.00")
    # The mapping keeps the pack cost (120 per CTN); stock is valued per piece.
    assert Decimal(str(row["cost"])) == Decimal("5")

    feed = client.get("/api/v1/app-data/stock-movements", headers=auth_headers,
                      params={"item": "Cola Can 330ml"})
    assert feed.status_code == 200
    [purchase] = [m for m in feed.json()["movements"] if m["reference"] == "PUR-PACK-001"]
    assert purchase["quantity"] == 48
    assert purchase["source_quantity"] == 2
    assert purchase["source_unit"] == "CTN"
    assert purchase["units_per_outer"] == 24
    assert purchase["balance"] == 48


def test_purchase_without_outer_is_unchanged(client, auth_headers, db):
    _map(client, auth_headers, "SOAP-1", "Hand Soap", "1")
    _save_purchase(client, auth_headers, "PUR-PACK-002", "SOAP-1", "Hand Soap", "PCS", 8, 3)

    mapping = db.query(StockProductMapping).filter(StockProductMapping.sku == "SOAP-1").one()
    movement = db.query(StockMovement).filter(StockMovement.mapping_id == mapping.id).one()
    assert movement.quantity == Decimal("8.00")
    assert movement.unit_cost == Decimal("3.0000")
    assert movement.source_quantity == Decimal("8.00")
    assert movement.units_per_outer == Decimal("1.0000")


def test_sale_by_mapped_name_takes_pieces_and_levels_list_a_to_z(client, db):
    from tests.test_perpetual_inventory import _company, _save

    tag, _, headers, _ = _company(client, db)
    for sku, name, taxflow_name, outer in (("COLA-CTN", "COLA 330ML X24 CTN", "Cola Can 330ml", "24"),
                                           ("ZZ-APPLE", "APPLE JCE 1L", "Apple Juice 1L", "1")):
        r = client.post("/api/v1/inventory/mappings", headers=headers,
                        json={"sku": sku, "name": name, "taxflow_name": taxflow_name, "units_per_outer": outer})
        assert r.status_code == 201, r.text
    _save_purchase(client, headers, f"PUR-{tag}", "COLA-CTN", "COLA 330ML X24 CTN", "CTN", 2, 120)
    _save_purchase(client, headers, f"PUR2-{tag}", "ZZ-APPLE", "APPLE JCE 1L", "PCS", 5, 4)
    # The sales picker gives the mapped name; a line without a code still finds the item.
    _save(client, headers, "salesInvoices", {"invoice_no": f"S-{tag}", "customer": "Cust", "date": "2026-10-11",
          "status": "Issued", "lines": [{"description": "Cola Can 330ml", "qty": 10, "unit_price": 8}]})

    levels = client.get("/api/v1/inventory/stock-levels", headers=headers).json()
    assert [row["name"] for row in levels] == ["Apple Juice 1L", "Cola Can 330ml"]
    assert levels[1]["current_stock"] == 38
