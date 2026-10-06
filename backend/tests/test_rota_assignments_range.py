"""GET /app-data/records/rotaAssignments/range -- Monthly/Weekly/Department Rota's fix
for the 500-row bootstrap cap on rotaAssignments (a company with more saved shifts than
that silently lost older/future weeks from the capped bootstrap blob). Reuses the
department-scope test world from test_hrms_department_scope.py."""
from app.models import AppDataRecord
from tests.test_hrms_department_scope import DEPT_A, DEPT_B, _company_id, _login_as, _save, _world


def _range(client, headers, date_from, date_to, **params):
    return client.get("/api/v1/app-data/records/rotaAssignments/range", headers=headers,
                       params={"from": date_from, "to": date_to, **params})


def test_only_rows_in_the_date_range_are_returned(client, db, auth_headers):
    a, b, _run = _world(client, db, auth_headers, "RG1")
    r = _range(client, auth_headers, "2026-09-02", "2026-09-02")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {x["id"] for x in body["records"]}
    assert {"RA-A-RG1", "RA-B-RG1"} <= ids
    assert body["from"] == "2026-09-02" and body["to"] == "2026-09-02"

    r2 = _range(client, auth_headers, "2026-09-03", "2026-09-30")
    assert {"RA-A-RG1", "RA-B-RG1"}.isdisjoint({x["id"] for x in r2.json()["records"]})


def test_bypasses_the_500_row_bootstrap_cap(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    # Push well past _BOOTSTRAP_COLLECTION_CAPS["rotaAssignments"] = 500 with rows dated
    # BEFORE (older, so they'd be pushed off the "created_at desc" cap first) our target
    # date, then confirm the target still comes back in full via the range endpoint.
    for i in range(520):
        db.add(AppDataRecord(company_id=cid, collection="rotaAssignments", record_key=f"filler-{i}",
                              payload=f'{{"id":"filler-{i}","date":"2026-01-01","employee_id":"E-filler"}}'))
    db.commit()
    assert _save(client, auth_headers, "rotaAssignments", {"id": "RA-CAP-1", "employee_id": "E-cap", "date": "2026-09-10"}).status_code == 200
    assert _save(client, auth_headers, "rotaAssignments", {"id": "RA-CAP-2", "employee_id": "E-cap", "date": "2026-09-11"}).status_code == 200

    # The generic capped endpoint can legitimately omit these two now that 520+ newer... wait,
    # these two ARE newer (created after the fillers), so the cap wouldn't drop them --
    # the cap drops the OLDEST-created rows first, and the fillers were created first.
    # What actually gets silently dropped by the cap is the fillers (older uninteresting data);
    # what this endpoint guarantees is that a genuine target date's rows are found by DATE,
    # never by luck of creation order.
    r = _range(client, auth_headers, "2026-09-10", "2026-09-11")
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()["records"]}
    assert {"RA-CAP-1", "RA-CAP-2"} <= ids

    old = _range(client, auth_headers, "2026-01-01", "2026-01-01")
    assert len(old.json()["records"]) == 520


def test_department_scoped_login_only_sees_its_own_department(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "RG2")
    headers = _login_as(client, db, auth_headers, cid, "rg2", [DEPT_A])
    r = _range(client, headers, "2026-09-02", "2026-09-02")
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()["records"]}
    assert "RA-A-RG2" in ids and "RA-B-RG2" not in ids


def test_unscoped_role_and_admin_see_both_departments(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "RG3")
    headers = _login_as(client, db, auth_headers, cid, "rg3", [])  # not scoped
    ids = {x["id"] for x in _range(client, headers, "2026-09-02", "2026-09-02").json()["records"]}
    assert {"RA-A-RG3", "RA-B-RG3"} <= ids
    admin_ids = {x["id"] for x in _range(client, auth_headers, "2026-09-02", "2026-09-02").json()["records"]}
    assert {"RA-A-RG3", "RA-B-RG3"} <= admin_ids


def test_another_companys_rows_are_never_returned(client, db, auth_headers, second_tenant_headers):
    _world(client, db, auth_headers, "RG4")
    r = _range(client, second_tenant_headers, "2026-09-02", "2026-09-02")
    assert r.status_code == 200, r.text
    assert "RA-A-RG4" not in {x["id"] for x in r.json()["records"]}


def test_requires_auth_and_validates_the_range(client, auth_headers):
    assert client.get("/api/v1/app-data/records/rotaAssignments/range", params={"from": "2026-09-01", "to": "2026-09-30"}).status_code == 401
    r = client.get("/api/v1/app-data/records/rotaAssignments/range", headers=auth_headers,
                    params={"from": "2026-09-30", "to": "2026-09-01"})
    assert r.status_code == 400
    r2 = client.get("/api/v1/app-data/records/rotaAssignments/range", headers=auth_headers, params={"from": "2026-09-01"})
    assert r2.status_code == 422


def test_employee_filter_returns_only_that_employees_shifts(client, db, auth_headers):
    a, b, _run = _world(client, db, auth_headers, "RG5")
    records = _range(client, auth_headers, "2026-09-01", "2026-09-30", employee_id=a.employee_no).json()["records"]
    ids = {x["id"] for x in records}
    assert "RA-A-RG5" in ids and "RA-B-RG5" not in ids
    assert all(x["employee_id"] == a.employee_no for x in records)


def test_by_ids_returns_those_rows_whatever_their_date(client, db, auth_headers, second_tenant_headers):
    _world(client, db, auth_headers, "RG6")
    url = "/api/v1/app-data/records/rotaAssignments/by-ids"
    r = client.get(url, headers=auth_headers, params={"ids": "RA-A-RG6, RA-B-RG6,missing"})
    assert r.status_code == 200, r.text
    assert {x["id"] for x in r.json()["records"]} == {"RA-A-RG6", "RA-B-RG6"}
    other = client.get(url, headers=second_tenant_headers, params={"ids": "RA-A-RG6"})
    assert other.json()["records"] == []
