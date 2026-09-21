"""Superadmin > Module Permissions gets a "backup" module: the company owner's
own Download Backup (GET /app-data/db-dump) is allowed only while the company
has it, it can be switched per company or for every company at once, and the
super admin's own backup downloads are never affected by it."""
import json

from sqlalchemy import text

from app.main import ensure_schema_updates
from app.models import Company
from app.module_catalog import ALL_MODULES
from tests.test_module_permissions import _make_restricted_company, _make_superadmin


def _modules_of(db, company_id):
    db.expire_all()
    raw = db.query(Company.modules_enabled).filter(Company.id == company_id).scalar()
    return json.loads(raw) if raw else None


def test_backup_is_a_known_module_and_in_the_superadmin_list(client, db):
    assert "backup" in ALL_MODULES
    company_id, _, sa = _make_restricted_company(client, db, "bk-list", ["sales", "backup"])
    r = client.get(f"/api/v1/superadmin/companies/{company_id}/modules", headers=sa)
    assert r.status_code == 200, r.text
    assert "backup" in r.json()["all_modules"]
    assert "backup" in r.json()["modules"]


def test_owner_db_dump_blocked_without_backup_module(client, db):
    _, headers, _ = _make_restricted_company(client, db, "bk-off", ["sales"])
    r = client.get("/api/v1/app-data/db-dump", headers=headers)
    assert r.status_code == 403, r.text
    assert "backup" in r.json()["detail"]


def test_owner_db_dump_allowed_with_backup_module(client, db):
    _, headers, _ = _make_restricted_company(client, db, "bk-on", ["sales", "backup"])
    r = client.get("/api/v1/app-data/db-dump", headers=headers)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]


def test_unrestricted_company_can_still_download(client, auth_headers):
    r = client.get("/api/v1/app-data/db-dump", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_per_company_toggle_round_trip(client, db):
    company_id, headers, sa = _make_restricted_company(client, db, "bk-toggle", ["sales", "backup"])
    assert client.get("/api/v1/app-data/db-dump", headers=headers).status_code == 200
    r = client.put(f"/api/v1/superadmin/companies/{company_id}/modules", json={"modules": ["sales"]}, headers=sa)
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/app-data/db-dump", headers=headers).status_code == 403
    r = client.put(f"/api/v1/superadmin/companies/{company_id}/modules", json={"modules": ["sales", "backup"]}, headers=sa)
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/app-data/db-dump", headers=headers).status_code == 200


def test_superadmin_backup_never_blocked_by_company_module(client, db):
    company_id, _, sa = _make_restricted_company(client, db, "bk-sa", ["sales"])
    r = client.get(f"/api/v1/superadmin/companies/{company_id}/db-dump", headers=sa)
    assert r.status_code == 200, r.text


def test_bulk_switch_off_and_on_touches_only_that_module(client, db):
    a, ha, sa = _make_restricted_company(client, db, "bk-bulk-a", ["sales", "purchase", "backup"])
    b, hb, _ = _make_restricted_company(client, db, "bk-bulk-b", ["inventory", "backup"])
    c, hc, _ = _make_restricted_company(client, db, "bk-bulk-c", ["sales"])  # already off

    r = client.put("/api/v1/superadmin/modules/bulk", json={"module": "backup", "enabled": False}, headers=sa)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] >= 2
    assert _modules_of(db, a) == ["sales", "purchase"]
    assert _modules_of(db, b) == ["inventory"]
    assert _modules_of(db, c) == ["sales"]
    for h in (ha, hb, hc):
        assert client.get("/api/v1/app-data/db-dump", headers=h).status_code == 403

    r = client.put("/api/v1/superadmin/modules/bulk", json={"module": "backup", "enabled": True}, headers=sa)
    assert r.status_code == 200, r.text
    for cid, expect in ((a, {"sales", "purchase", "backup"}), (b, {"inventory", "backup"}), (c, {"sales", "backup"})):
        assert set(_modules_of(db, cid)) == expect
    for h in (ha, hb, hc):
        assert client.get("/api/v1/app-data/db-dump", headers=h).status_code == 200


def test_bulk_can_target_chosen_companies_only(client, db):
    a, ha, sa = _make_restricted_company(client, db, "bk-sub-a", ["sales", "backup"])
    b, hb, _ = _make_restricted_company(client, db, "bk-sub-b", ["sales", "backup"])
    r = client.put("/api/v1/superadmin/modules/bulk",
                   json={"module": "backup", "enabled": False, "company_ids": [a]}, headers=sa)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1
    assert _modules_of(db, a) == ["sales"]
    assert "backup" in _modules_of(db, b)


def test_bulk_rejects_unknown_module_and_non_superadmin(client, db, auth_headers):
    sa = _make_superadmin(client, db, "bk-bad")
    r = client.put("/api/v1/superadmin/modules/bulk", json={"module": "nope", "enabled": True}, headers=sa)
    assert r.status_code == 400
    r = client.put("/api/v1/superadmin/modules/bulk", json={"module": "backup", "enabled": True}, headers=auth_headers)
    assert r.status_code in (401, 403)


def test_startup_backfill_adds_backup_once_and_respects_later_switch_off(client, db):
    # an "old" company: explicit list from before the backup module existed
    company_id, headers, sa = _make_restricted_company(client, db, "bk-old", ["sales", "purchase"])
    assert client.get("/api/v1/app-data/db-dump", headers=headers).status_code == 403

    # simulate the first deploy of this feature: marker not yet written
    db.execute(text("DELETE FROM schema_flags WHERE name = 'backup_module_backfill'"))
    db.commit()
    ensure_schema_updates()
    assert set(_modules_of(db, company_id)) == {"sales", "purchase", "backup"}
    assert client.get("/api/v1/app-data/db-dump", headers=headers).status_code == 200

    # super admin turns it off on purpose -> a later restart must NOT re-add it
    client.put(f"/api/v1/superadmin/companies/{company_id}/modules", json={"modules": ["sales", "purchase"]}, headers=sa)
    ensure_schema_updates()
    assert _modules_of(db, company_id) == ["sales", "purchase"]
