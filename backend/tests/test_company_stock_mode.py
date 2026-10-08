"""Super Admin "with stock / without stock" company setting: a without-stock company never
moves stock on purchases, sales invoices or POS (so selling never needs stock on hand)."""
from uuid import uuid4

from app.models import Company, StockMovement, StockProductMapping
from tests.test_module_permissions import _make_superadmin


def _save(client, headers, collection, record):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                       json={"collection": collection, "record": record})


def _create_company(client, sa_headers, tag, **extra):
    email = f"stockmode-{tag}@example.com"
    r = client.post("/api/v1/superadmin/companies", headers=sa_headers, json={
        "name": f"Stock Mode Co {tag}", "email": email, "password": "admin12345", "full_name": "Admin", **extra})
    assert r.status_code in (200, 201), r.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "admin12345"})
    assert login.status_code == 200, login.text
    return r.json()["company_id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


def _trade(client, headers, tag):
    """A purchase, an issued sales invoice that oversells, and a POS sale — with negative stock blocked."""
    assert _save(client, headers, "inventorySettings", {"key": "config", "allow_negative_stock": False}).status_code == 200
    assert _save(client, headers, "purchaseRecords", {"ref": f"SM-PUR-{tag}", "supplier": "S", "date": "2026-10-01",
        "subtotal": 100, "vat_amount": 5, "total": 105,
        "lines": [{"sku": f"SM-{tag}", "product": f"Widget {tag}", "quantity": 2, "unit_cost": 50}]}).status_code == 200
    sale = _save(client, headers, "salesInvoices", {"invoice_no": f"SM-INV-{tag}", "customer": "C", "date": "2026-10-02",
        "status": "Issued", "subtotal": 500, "vat_amount": 25, "total": 525,
        "lines": [{"description": f"Widget {tag}", "product_code": f"SM-{tag}", "qty": 5, "unit_price": 100, "vat_rate": 5}]})
    pos = _save(client, headers, "posSales", {"id": f"SM-POS-{tag}", "receipt_no": f"SM-POS-{tag}", "status": "completed",
        "subtotal": 300, "vat": 15, "total": 315, "payment_method": "cash",
        "items": [{"code": f"SM-{tag}", "name": f"Widget {tag}", "qty": 3, "price": 100}]})
    return sale, pos


def test_without_stock_company_never_moves_stock(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"sm-{tag}")
    company_id, headers = _create_company(client, sa, tag, stock_mode="without_stock")
    assert db.get(Company, company_id).stock_mode == "without_stock"
    assert client.get("/api/v1/companies/current", headers=headers).json()["stock_mode"] == "without_stock"

    sale, pos = _trade(client, headers, tag)
    assert sale.status_code == 200, sale.text   # overselling isn't blocked: no stock needed
    assert pos.status_code == 200, pos.text
    db.expire_all()
    assert db.query(StockMovement).filter(StockMovement.company_id == company_id).count() == 0
    assert db.query(StockProductMapping).filter(StockProductMapping.company_id == company_id).count() == 0


def test_with_stock_is_the_default_and_still_tracks(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"sm-{tag}")
    company_id, headers = _create_company(client, sa, tag)
    assert db.get(Company, company_id).stock_mode == "with_stock"

    sale, _ = _trade(client, headers, tag)
    assert sale.status_code == 409, sale.text   # 5 sold, 2 bought, negative stock blocked
    db.expire_all()
    assert db.query(StockMovement).filter(StockMovement.company_id == company_id, StockMovement.movement_type == "purchase").count() == 1


def test_super_admin_can_switch_mode_but_company_admin_cannot(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"sm-{tag}")
    company_id, headers = _create_company(client, sa, tag)

    r = client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa, json={"stock_mode": "without_stock"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa).json()["stock_mode"] == "without_stock"

    assert client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa, json={"stock_mode": "bogus"}).status_code == 422
    client.put("/api/v1/companies/current", headers=headers, json={"stock_mode": "with_stock"})
    db.expire_all()
    assert db.get(Company, company_id).stock_mode == "without_stock"
