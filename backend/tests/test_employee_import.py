"""Employee Directory > Import sends rows through bulk-save with skip_existing: new
employees are created (JSON record + SQL row), an Employee ID already saved is left
untouched rather than overwritten by the sparser imported row."""
import uuid

from app.models import Employee
from tests.test_ess_requests import _company_id


def _bulk(client, headers, records, **extra):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "bulk-save"},
                       json={"collection": "employees", "records": records, **extra})


def _row(no, name, **kw):
    return {"id": no, "name": name, "department": "Clinic", "designation": "Employee", "status": "Active",
            "contract": "Full-Time", "salary": 0, **kw}


def test_import_creates_new_and_leaves_existing_untouched(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:5]
    existing_no, new_no = f"IMP{tag}-1", f"IMP{tag}-2"
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={
        "collection": "employees", "record": _row(existing_no, "Original Name", designation="Vet", salary=9000, mobile="0501234567")})
    assert r.status_code == 200, r.text

    r = _bulk(client, auth_headers, [_row(existing_no, "Imported Name"), _row(new_no, "Brand New")], skip_existing=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["created"], body["updated"], body["skipped"]) == (1, 0, 1)
    assert body["skipped_keys"] == [existing_no]

    db.expire_all()
    old = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == existing_no).one()
    assert (old.full_name, old.designation, float(old.basic_salary)) == ("Original Name", "Vet", 9000.0)
    new = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == new_no).one()
    assert (new.full_name, new.department, new.status) == ("Brand New", "Clinic", "active")
    assert new.password_hash is None   # no login until HR sets a password

    listed = client.get("/api/v1/app-data/records/employees", headers=auth_headers, params={"limit": 500}).json()["records"]
    assert any(e.get("id") == new_no and e.get("name") == "Brand New" for e in listed)


def test_without_skip_existing_bulk_save_still_updates(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    no = f"IMPU{uuid.uuid4().hex[:5]}"
    assert _bulk(client, auth_headers, [_row(no, "First")]).json()["created"] == 1
    body = _bulk(client, auth_headers, [_row(no, "Second")]).json()
    assert (body["created"], body["updated"], body["skipped"]) == (0, 1, 0)
    db.expire_all()
    assert db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).one().full_name == "Second"


def test_import_needs_employee_edit_permission(client, db, auth_headers):
    from tests.test_hr_ess_permission_gaps import _role_holder
    cid = _company_id(client, auth_headers)
    _v, view_h, _ = _role_holder(client, db, auth_headers, cid, ["employees:view"], [], "Ops")
    r = _bulk(client, view_h, [_row(f"IMPV{uuid.uuid4().hex[:5]}", "Should Not Exist")], skip_existing=True)
    assert r.status_code == 403, r.text
