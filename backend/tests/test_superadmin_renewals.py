"""Super Admin Renewals centre + last-login tracking."""
from datetime import date, datetime, timedelta, timezone

from app.models import Company, User
from tests.test_module_permissions import _make_restricted_company


def _today():
    # The server dates renewals in UTC; the local date differs around midnight.
    return datetime.now(timezone.utc).date()


def _set_expiry(db, company_id, days_from_today):
    c = db.query(Company).filter(Company.id == company_id).one()
    c.subscription_expires_at = None if days_from_today is None else (_today() + timedelta(days=days_from_today)).isoformat()
    db.commit()


def test_login_records_last_login_and_company_list_exposes_it(client, db):
    cid, headers, sa = _make_restricted_company(client, db, "rn-login", ["sales"])
    row = client.get(f"/api/v1/superadmin/companies/{cid}", headers=sa).json()
    # _make_restricted_company already logs the admin in once
    assert row["last_login_at"] is not None
    assert row["inactive_days"] == 0
    admin = db.query(User).filter(User.company_id == cid, User.role == "admin").one()
    admin.last_login = datetime.now(timezone.utc) - timedelta(days=45)
    db.commit()
    row = client.get(f"/api/v1/superadmin/companies/{cid}", headers=sa).json()
    assert row["inactive_days"] in (44, 45)


def test_company_with_no_recorded_login_reports_none(client, db):
    cid, _, sa = _make_restricted_company(client, db, "rn-nologin", ["sales"])
    db.query(User).filter(User.company_id == cid).update({"last_login": None})
    db.commit()
    row = client.get(f"/api/v1/superadmin/companies/{cid}", headers=sa).json()
    assert row["last_login_at"] is None and row["inactive_days"] is None


def test_renewals_lists_expired_and_expiring_soonest_first(client, db):
    a, _, sa = _make_restricted_company(client, db, "rn-a", ["sales"])
    b, _, _ = _make_restricted_company(client, db, "rn-b", ["sales"])
    c, _, _ = _make_restricted_company(client, db, "rn-c", ["sales"])
    d, _, _ = _make_restricted_company(client, db, "rn-d", ["sales"])
    _set_expiry(db, a, -3)      # expired
    _set_expiry(db, b, 5)       # within 7
    _set_expiry(db, c, 25)      # within 30
    _set_expiry(db, d, 200)     # outside window
    r = client.get("/api/v1/superadmin/renewals?days=30", headers=sa)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [x["id"] for x in body["companies"]]
    assert a in ids and b in ids and c in ids and d not in ids
    assert ids.index(a) < ids.index(b) < ids.index(c)
    row = {x["id"]: x for x in body["companies"]}
    assert row[a]["status"] == "expired" and row[a]["days_left"] == -3
    assert row[b]["status"] == "expiring" and row[b]["days_left"] == 5
    assert row[b]["admin_email"] == "restricted-rn-b@example.com"
    assert body["counts"]["expired"] >= 1 and body["counts"]["within_7"] >= 1


def test_renewals_ignores_companies_without_expiry(client, db):
    a, _, sa = _make_restricted_company(client, db, "rn-none", ["sales"])
    _set_expiry(db, a, None)
    body = client.get("/api/v1/superadmin/renewals?days=365", headers=sa).json()
    assert a not in [x["id"] for x in body["companies"]]
    assert body["counts"]["no_expiry"] >= 1


def test_bulk_extend_uses_future_expiry_or_today(client, db):
    a, _, sa = _make_restricted_company(client, db, "rn-ext-a", ["sales"])
    b, _, _ = _make_restricted_company(client, db, "rn-ext-b", ["sales"])
    _set_expiry(db, a, 10)      # future: extends from its own expiry
    _set_expiry(db, b, -20)     # lapsed: extends from today
    r = client.post("/api/v1/superadmin/renewals/extend", json={"company_ids": [a, b], "days": 30}, headers=sa)
    assert r.status_code == 200, r.text
    assert r.json()["extended"] == 2
    db.expire_all()
    got = {c.id: c.subscription_expires_at for c in db.query(Company).filter(Company.id.in_([a, b])).all()}
    assert got[a] == (_today() + timedelta(days=40)).isoformat()
    assert got[b] == (_today() + timedelta(days=30)).isoformat()


def test_bulk_extend_validates_and_requires_superadmin(client, db, auth_headers):
    a, _, sa = _make_restricted_company(client, db, "rn-val", ["sales"])
    assert client.post("/api/v1/superadmin/renewals/extend", json={"company_ids": [], "days": 30}, headers=sa).status_code == 422
    assert client.post("/api/v1/superadmin/renewals/extend", json={"company_ids": [a], "days": 0}, headers=sa).status_code == 422
    assert client.post("/api/v1/superadmin/renewals/extend", json={"company_ids": ["nope"], "days": 5}, headers=sa).status_code == 404
    assert client.post("/api/v1/superadmin/renewals/extend", json={"company_ids": [a], "days": 5}, headers=auth_headers).status_code in (401, 403)
    assert client.get("/api/v1/superadmin/renewals", headers=auth_headers).status_code in (401, 403)
