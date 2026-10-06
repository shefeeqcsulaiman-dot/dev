"""Purchase lines only add stock when their category is Inventory or uncategorised;
a line categorised to another ledger (expense, service...) is not stock."""
from uuid import uuid4

from app.models import StockMovement


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _save_purchase(client, headers, ref, lines):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "purchaseRecords", "record": {
        "ref": ref, "supplier": "Category Supplier", "date": "2026-10-01", "subtotal": 100, "vat_amount": 5, "total": 105, "lines": lines}})
    assert r.status_code == 200, r.text


def _stocked(db, company_id, ref):
    db.expire_all()
    rows = db.query(StockMovement).filter(StockMovement.company_id == company_id, StockMovement.reference == ref).all()
    return sorted(float(m.quantity) for m in rows)


def _line(product, category, qty):
    return {"product": product, "sku": product.upper().replace(" ", "-"), "category": category, "quantity": qty, "unit_cost": 10, "line_total": qty * 10}


def test_only_inventory_and_uncategorised_lines_add_stock(client, db, auth_headers):
    tag = uuid4().hex[:6]
    ref = f"PSC-{tag}"
    _save_purchase(client, auth_headers, ref, [
        _line(f"Stock A {tag}", "Inventory", 1),
        _line(f"Stock B {tag}", "Uncategorized", 2),
        _line(f"Stock C {tag}", "", 3),
        _line(f"Stock D {tag}", "Samsung", 4),               # free text (e.g. a CSV brand column)
        _line(f"Service E {tag}", "Cost of Goods Sold", 5),      # another ledger -> not stock
        _line(f"Service F {tag}", "Purchases", 6),           # another ledger -> not stock
    ])
    assert _stocked(db, _company_id(client, auth_headers), ref) == [1.0, 2.0, 3.0, 4.0]


def test_changing_a_line_to_an_expense_ledger_removes_its_stock(client, db, auth_headers):
    tag = uuid4().hex[:6]
    ref = f"PSC-{tag}"
    company_id = _company_id(client, auth_headers)
    _save_purchase(client, auth_headers, ref, [_line(f"Item {tag}", "Inventory", 7)])
    assert _stocked(db, company_id, ref) == [7.0]
    _save_purchase(client, auth_headers, ref, [_line(f"Item {tag}", "Cost of Goods Sold", 7)])
    assert _stocked(db, company_id, ref) == []
