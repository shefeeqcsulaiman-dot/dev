"""The purchase stock backfill (inventory.backfill_purchase_stock_movements, run on stock
reads) follows the same rules as saving a purchase: no movements for a "without stock"
company or for a line categorised to a non-Inventory ledger."""
import json
from uuid import uuid4

from app.models import AppDataRecord, Company, StockMovement
from tests.conftest import ensure_user


def _tenant(client, db, stock_mode="with_stock"):
    tag = uuid4().hex[:8]
    email = f"bf-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"95{int(tag, 16) % 10**13:013d}")
    db.get(Company, user.company_id).stock_mode = stock_mode
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _legacy_purchase(db, company_id, ref, lines):
    """A purchase record stored without going through the save path (as legacy data was)."""
    db.add(AppDataRecord(company_id=company_id, collection="purchaseRecords", record_key=ref,
                         payload=json.dumps({"ref": ref, "supplier": "Supp", "date": "2026-09-01", "lines": lines})))
    db.commit()


def _movements(db, company_id):
    db.expire_all()
    return sorted(ref for (ref,) in db.query(StockMovement.reference).filter(
        StockMovement.company_id == company_id, StockMovement.movement_type == "purchase"))


def test_backfill_skips_lines_categorised_to_another_ledger(client, db):
    company_id, headers = _tenant(client, db)
    _legacy_purchase(db, company_id, "BF-STOCK", [{"sku": "W-1", "product": "Widget", "quantity": 5, "unit_cost": 10}])
    _legacy_purchase(db, company_id, "BF-COGS", [{"sku": "S-1", "product": "Cleaning service", "quantity": 1,
                                                 "unit_cost": 300, "category": "Cost of Goods Sold"}])
    assert client.get("/api/v1/inventory/stock-levels", headers=headers).status_code == 200
    assert _movements(db, company_id) == ["BF-STOCK"]


def test_backfill_does_nothing_for_a_without_stock_company(client, db):
    company_id, headers = _tenant(client, db, stock_mode="without_stock")
    _legacy_purchase(db, company_id, "BF-NS", [{"sku": "W-1", "product": "Widget", "quantity": 5, "unit_cost": 10}])
    assert client.get("/api/v1/inventory/stock-levels", headers=headers).status_code == 200
    assert _movements(db, company_id) == []
