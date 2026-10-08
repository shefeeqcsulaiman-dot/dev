"""Super Admin company list, overview, users and CSV are paged, searched and counted in
the database (GET /superadmin/companies used to return every company with all its users
and employees in one response)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Company, Employee, User
from tests.test_module_permissions import _make_restricted_company


def _today():
    return datetime.now(timezone.utc).date()


def _set(db, company_id, **fields):
    c = db.query(Company).filter(Company.id == company_id).one()
    for k, v in fields.items():
        setattr(c, k, v)
    db.commit()


def test_company_list_pages_and_counts(client, db):
    tag = uuid4().hex[:4]
    ids = [_make_restricted_company(client, db, f"pg-{tag}-{i}", ["sales"])[0] for i in range(3)]
    sa = _make_restricted_company(client, db, f"pg-{tag}-sa", ["sales"])[2]
    q = f"co pg-{tag}-"
    first = client.get(f"/api/v1/superadmin/companies?q={q}&limit=2&sort=name&dir=asc", headers=sa).json()
    second = client.get(f"/api/v1/superadmin/companies?q={q}&limit=2&offset=2&sort=name&dir=asc", headers=sa).json()
    assert first["total"] == 4 and second["total"] == 4
    assert len(first["items"]) == 2 and len(second["items"]) == 2
    names = [c["name"] for c in first["items"] + second["items"]]
    assert names == sorted(names, key=str.lower)
    row = next(c for c in first["items"] + second["items"] if c["id"] == ids[0])
    assert row["user_count"] == 1 and row["sub_user_count"] == 0
    assert "users" not in row and "employees" not in row  # those are in GET /companies/{id}


def test_company_search_matches_a_users_email(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"pgmail-{tag}", ["sales"])
    body = client.get(f"/api/v1/superadmin/companies?q=restricted-pgmail-{tag}@", headers=sa).json()
    assert [c["id"] for c in body["items"]] == [cid]


def test_status_filters(client, db):
    tag = uuid4().hex[:4]
    expired, _, sa = _make_restricted_company(client, db, f"st-{tag}-ex", ["sales"])
    soon, _, _ = _make_restricted_company(client, db, f"st-{tag}-sn", ["sales"])
    quiet, _, _ = _make_restricted_company(client, db, f"st-{tag}-qt", ["sales"])
    open_ended, _, _ = _make_restricted_company(client, db, f"st-{tag}-op", ["sales"])
    _set(db, expired, subscription_expires_at=(_today() - timedelta(days=2)).isoformat())
    _set(db, soon, subscription_expires_at=(_today() + timedelta(days=3)).isoformat())
    _set(db, quiet, subscription_expires_at=(_today() + timedelta(days=90)).isoformat())
    _set(db, open_ended, subscription_expires_at=None)
    db.query(User).filter(User.company_id == quiet).update({"last_login": datetime.now(timezone.utc) - timedelta(days=40)})
    db.commit()

    def ids(status):
        body = client.get(f"/api/v1/superadmin/companies?q=co st-{tag}-&status={status}&limit=200", headers=sa).json()
        return {c["id"] for c in body["items"]}

    assert ids("expired") == {expired}
    assert ids("expiring") == {soon}
    assert ids("inactive") == {quiet}
    assert ids("no_expiry") == {open_ended}
    assert ids("active") == {soon, quiet, open_ended}
    assert client.get("/api/v1/superadmin/companies?status=bogus", headers=sa).status_code == 422


def test_expiry_sort_puts_open_ended_last_when_ascending(client, db):
    tag = uuid4().hex[:4]
    a, _, sa = _make_restricted_company(client, db, f"so-{tag}-a", ["sales"])
    b, _, _ = _make_restricted_company(client, db, f"so-{tag}-b", ["sales"])
    c, _, _ = _make_restricted_company(client, db, f"so-{tag}-c", ["sales"])
    _set(db, a, subscription_expires_at=(_today() + timedelta(days=20)).isoformat())
    _set(db, b, subscription_expires_at=None)
    _set(db, c, subscription_expires_at=(_today() + timedelta(days=5)).isoformat())
    body = client.get(f"/api/v1/superadmin/companies?q=co so-{tag}-&sort=expiry&dir=asc", headers=sa).json()
    assert [x["id"] for x in body["items"]] == [c, a, b]


def test_company_detail_has_users_branches_and_employees(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"det-{tag}", ["sales"])
    db.add(Employee(company_id=cid, employee_no=f"E-{tag}", full_name="Detail Employee", status="active"))
    db.commit()
    r = client.get(f"/api/v1/superadmin/companies/{cid}", headers=sa)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [u["email"] for u in body["users"]] == [f"restricted-det-{tag}@example.com"]
    assert body["employee_count"] == 1 and body["employees"][0]["employee_no"] == f"E-{tag}"
    assert isinstance(body["branches"], list)
    assert client.get("/api/v1/superadmin/companies/nope", headers=sa).status_code == 404


def test_overview_counts_match_the_filtered_lists(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"ov-{tag}", ["sales"])
    _set(db, cid, subscription_expires_at=(_today() + timedelta(days=2)).isoformat())
    body = client.get("/api/v1/superadmin/companies/overview", headers=sa).json()
    counts = body["counts"]
    for status in ("expired", "expiring", "inactive", "no_expiry", "no_users", "active", "new_month"):
        listed = client.get(f"/api/v1/superadmin/companies?status={status}&limit=1", headers=sa).json()["total"]
        assert counts[status] == listed, status
    assert counts["companies"] == client.get("/api/v1/superadmin/companies?limit=1", headers=sa).json()["total"]
    assert cid in [c["id"] for c in body["samples"]["expiring"]]
    assert counts["users"] >= 1


def test_users_page_and_search(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"us-{tag}", ["sales"])
    body = client.get(f"/api/v1/superadmin/users?q=us-{tag}", headers=sa).json()
    assert body["total"] == 1
    assert body["items"][0]["company_id"] == cid and body["items"][0]["company_name"].endswith(f"us-{tag}")
    assert all(u["role"] != "superadmin" for u in client.get("/api/v1/superadmin/users?limit=200", headers=sa).json()["items"])


def test_csv_export_streams_matching_companies(client, db):
    tag = uuid4().hex[:4]
    _make_restricted_company(client, db, f"csv-{tag}-a", ["sales"])
    sa = _make_restricted_company(client, db, f"csv-{tag}-b", ["sales"])[2]
    r = client.get(f"/api/v1/superadmin/companies/export.csv?q=co csv-{tag}-", headers=sa)
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("Name,TRN,Country")
    assert len(lines) == 3


def test_users_csv_export(client, db):
    tag = uuid4().hex[:4]
    _, _, sa = _make_restricted_company(client, db, f"ucsv-{tag}", ["sales"])
    r = client.get(f"/api/v1/superadmin/users/export.csv?q=ucsv-{tag}", headers=sa)
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0] == "Name,Email,Company,Role,Status,Joined"
    assert len(lines) == 2 and f"restricted-ucsv-{tag}@example.com" in lines[1]


def test_paging_endpoints_require_superadmin(client, auth_headers):
    for path in ("/companies", "/companies/overview", "/companies/export.csv", "/users", "/users/export.csv"):
        assert client.get(f"/api/v1/superadmin{path}", headers=auth_headers).status_code in (401, 403), path
