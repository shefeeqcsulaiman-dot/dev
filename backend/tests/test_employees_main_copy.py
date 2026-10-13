"""The SQL employees table is the main copy of HRMS employee records (step C): the JSON is
regenerated from the row after each save, the record's other fields live in employees.extra,
and a role without salary access can no longer leave a 0 salary in the JSON."""
import json
import uuid

from sqlalchemy import text

from app import employee_records
from app.models import AppDataRecord, Employee
from tests.test_employees_salary_permission import _company_id, _seed_subject_employee, _viewer_with_permissions


def _stored(db, company_id, emp_no):
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "employees",
                                         AppDataRecord.record_key == emp_no).one()
    return json.loads(rec.payload)


def test_json_follows_the_row_and_extra_holds_the_rest(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp_no = f"EMC-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={
        "collection": "employees", "record": {
            "id": emp_no, "name": "  Sara Ali ", "department": "Sales", "designation": "Cashier", "status": "On Leave",
            "salary": "6,500", "housing_allowance": 1000, "iban": "ae07 0331 2345 6789 0123 456", "passport_no": "P123",
            "visa_expiry": "2027-01-31", "documents": [{"name": "passport.pdf"}]}})
    assert r.status_code == 200, r.text
    stored = _stored(db, cid, emp_no)
    emp = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == emp_no).one()
    # Row values written back: trimmed name, numeric salary, normalised IBAN; status keeps HRMS wording.
    assert (stored["name"], stored["salary"], stored["housing_allowance"], stored["iban"], stored["status"]) == \
           ("Sara Ali", 6500, 1000, "AE070331234567890123456", "On Leave")
    assert emp.status == "inactive"
    extra = json.loads(emp.extra)
    assert (extra["passport_no"], extra["visa_expiry"], extra["documents"]) == ("P123", "2027-01-31", [{"name": "passport.pdf"}])
    assert "salary" not in extra and "name" not in extra
    assert employee_records.stale_json(db, cid) == []


def test_role_without_salary_access_no_longer_zeroes_the_json(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    emp_no = f"EMS-{tag}"
    _seed_subject_employee(client, auth_headers, emp_no, "Omar", 8000, 1500)
    viewer = _viewer_with_permissions(client, db, auth_headers, cid, tag, ["employees:view", "employees:edit"])
    # The viewer's edit form never sees salary, so it sends 0s with a department change.
    r = client.post("/api/v1/app-data", headers=viewer, params={"action": "save"}, json={
        "collection": "employees", "record": {"id": emp_no, "name": "Omar", "department": "Logistics", "designation": "Staff",
                                              "status": "Active", "salary": 0, "housing_allowance": 0}})
    assert r.status_code == 200, r.text
    stored = _stored(db, cid, emp_no)
    assert (stored["department"], stored["salary"], stored["housing_allowance"]) == ("Logistics", 8000, 1500)


def test_json_not_stored_and_rebuilt_from_the_row(client, db, auth_headers):
    """Step D: the stored JSON is emptied; reads rebuild exactly the saved record from the row
    (the field names it used, its status wording, a second name for a column, its photo)."""
    cid = _company_id(client, auth_headers)
    emp_no = f"EMD-{uuid.uuid4().hex[:6]}"
    record = {"id": emp_no, "full_name": "Huda K", "department": "Ops", "status": "On Leave", "basic_salary": 7000,
              "salary": 7000, "photo": "", "notes": "night shifts"}
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"},
                    json={"collection": "employees", "record": record})
    assert r.status_code == 200, r.text
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "employees",
                                         AppDataRecord.record_key == emp_no).one()
    assert db.execute(text("SELECT payload FROM app_data_records WHERE id = :i"), {"i": rec.id}).scalar() == ""
    assert _stored(db, cid, emp_no) == record
    listed = client.get("/api/v1/app-data/records/employees", headers=auth_headers, params={"limit": 500}).json()["records"]
    assert [{k: v for k, v in x.items() if k in record} for x in listed if x.get("id") == emp_no] == [record]
    assert employee_records.stale_json(db, cid) == []

    # An edit stores the new values in the row, still not as JSON.
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"},
                    json={"collection": "employees", "record": {**record, "department": "Stores", "notes": "days"}})
    assert r.status_code == 200, r.text
    assert _stored(db, cid, emp_no) == {**record, "department": "Stores", "notes": "days"}
    assert db.execute(text("SELECT payload FROM app_data_records WHERE id = :i"), {"i": rec.id}).scalar() == ""
