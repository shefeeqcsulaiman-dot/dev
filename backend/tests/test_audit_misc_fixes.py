"""Regression coverage for the live QA audit: users, corporate accounting validation, TRN clash."""
from tests.test_registration import _register


def test_company_admin_can_create_user_who_can_log_in(client):
    h = _register(client, "audit-adduser")
    r = client.post("/api/v1/companies/current/users", headers=h,
                    json={"email": "colleague@audit-adduser.com", "full_name": "Colleague", "password": "colleague1", "role": "accountant"})
    assert r.status_code == 201, r.text
    login = client.post("/api/v1/auth/login", json={"email": "colleague@audit-adduser.com", "password": "colleague1"})
    assert login.status_code == 200
    dup = client.post("/api/v1/companies/current/users", headers=h,
                      json={"email": "colleague@audit-adduser.com", "password": "colleague1"})
    assert dup.status_code == 409
    ch = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.post("/api/v1/companies/current/users", headers=ch,
                       json={"email": "third@audit-adduser.com", "password": "third123"}).status_code == 403


def test_corporate_accounting_rejects_invalid_records(client):
    h = _register(client, "audit-corp")
    base = "/api/v1/corporate-accounting"
    assert client.post(f"{base}/fixed-assets", headers=h, json={"asset_code": "", "asset_name": "x", "category": "c"}).status_code == 422
    assert client.post(f"{base}/fixed-assets", headers=h, json={"asset_code": "A1", "asset_name": "x", "category": "c", "purchase_cost": 100, "accumulated_depreciation": 500}).status_code == 422
    ok = {"asset_code": "A1", "asset_name": "Laptop", "category": "IT", "purchase_cost": 1000, "accumulated_depreciation": 100}
    assert client.post(f"{base}/fixed-assets", headers=h, json=ok).status_code == 201
    assert client.post(f"{base}/fixed-assets", headers=h, json=ok).status_code == 409
    assert client.post(f"{base}/approval-matrix", headers=h, json={"module": "Journal", "min_amount": 500, "max_amount": 10, "approver_role": "Manager"}).status_code == 422
    b = client.post(f"{base}/budgets", headers=h, json={"fiscal_year": "2026", "annual_budget": 1000, "actual_amount": 300, "variance_amount": 9999})
    assert b.status_code == 201 and float(b.json()["variance_amount"]) == 700
    c = client.post(f"{base}/cash-flow", headers=h, json={"forecast_date": "2026-10-01", "expected_receipts": 500, "expected_payments": 200, "net_cash_flow": 1})
    assert c.status_code == 201 and float(c.json()["net_cash_flow"]) == 300


def test_duplicate_trn_returns_409(client):
    h1 = _register(client, "audit-trn1")
    h2 = _register(client, "audit-trn2")
    assert client.put("/api/v1/companies/current", headers=h1, json={"name": "A", "trn": "100999999999991"}).status_code == 200
    assert client.put("/api/v1/companies/current", headers=h2, json={"name": "B", "trn": "100999999999991"}).status_code == 409


def test_ot_cooloff_setting_reduces_counted_overtime(client, db):
    from app.routers.attendance import _ot_after_cooloff, _ot_cooloff_seconds
    h = _register(client, "audit-cooloff")
    from app.models import Company
    cid = db.query(Company).filter(Company.name == "New Co audit-cooloff").one().id
    assert _ot_cooloff_seconds(db, cid) == 0
    r = client.post("/api/v1/app-data", headers=h, params={"action": "save"},
                    json={"collection": "hr_settings", "record": {"id": "ot-rules-config", "workHours": "8", "otCooloffMinutes": 30}})
    assert r.status_code == 200, r.text
    assert _ot_cooloff_seconds(db, cid) == 1800
    std = 8 * 3600
    assert _ot_after_cooloff(std + 1200, std, 1800) == 0      # 20 min extra: inside cool-off
    assert _ot_after_cooloff(std + 3600, std, 1800) == 1800   # 60 min extra: only 30 count


def test_related_party_records_save_and_reload(client):
    h = _register(client, "audit-relparty")
    r = client.post("/api/v1/app-data", headers=h, params={"action": "save"},
                    json={"collection": "relatedPartyTransactions", "record": {"party": "Group Co", "type": "Recharge", "amount": 500, "status": "Documented"}})
    assert r.status_code == 200, r.text
    rows = client.get("/api/v1/app-data/records/relatedPartyTransactions", headers=h).json()
    text = str(rows)
    assert "Group Co" in text
    scoped = client.get("/api/v1/app-data", headers=h, params={"scope": "main"}).json()
    assert "Group Co" in str(scoped["data"].get("relatedPartyTransactions"))
