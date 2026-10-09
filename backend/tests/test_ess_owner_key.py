"""ESS tasks and requests are read by the indexed owner_key column (app.models
payload_owner_key()) instead of parsing every record the company has. Same answers:
own tasks by assignee id; own requests by employee_id, or by name for rows written
before employee_id existed; never another employee's."""
import json
from uuid import uuid4

from app.models import AppDataRecord, payload_owner_key
from tests.test_ess_requests import _company_id, _ess_login


def _add(db, company_id, collection, key, payload):
    db.add(AppDataRecord(company_id=company_id, collection=collection, record_key=key, payload=json.dumps(payload)))
    db.commit()


def test_owner_key_rules():
    assert payload_owner_key("tasks", json.dumps({"assigned_to": "emp-uuid"})) == "emp-uuid"
    assert payload_owner_key("tasks", json.dumps({"assigned_to": ""})) is None
    assert payload_owner_key("overtimeRequests", json.dumps({"employee_id": " E-7 ", "employee": "X"})) == "E-7"
    assert payload_owner_key("employeeLoans", json.dumps({"employee": "  Sara Ali "})) == "name:Sara Ali"
    assert payload_owner_key("salaryAdvances", "not json") is None


def test_ess_lists_only_this_employees_tasks_and_requests(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid4().hex[:6]
    me, headers = _ess_login(client, db, auth_headers, cid, f"OK-{tag}", f"ok{tag}")
    other, _ = _ess_login(client, db, auth_headers, cid, f"OO-{tag}", f"oo{tag}")

    _add(db, cid, "tasks", f"T-MINE-{tag}", {"id": f"T-MINE-{tag}", "title": "mine", "assigned_to": me.id, "due_date": "2026-10-01"})
    _add(db, cid, "tasks", f"T-OTHER-{tag}", {"id": f"T-OTHER-{tag}", "title": "theirs", "assigned_to": other.id})
    _add(db, cid, "overtimeRequests", f"OT-MINE-{tag}",
         {"id": f"OT-MINE-{tag}", "employee_id": me.employee_no, "employee": me.full_name, "status": "Pending", "date": "2026-09-30"})
    # Written before employee_id existed: only the display name says whose it is.
    _add(db, cid, "employeeLoans", f"LN-LEGACY-{tag}", {"id": f"LN-LEGACY-{tag}", "employee": me.full_name, "status": "Approved"})
    # employee_id wins over the name: same name, someone else's number -> not mine.
    _add(db, cid, "salaryAdvances", f"ADV-OTHER-{tag}",
         {"id": f"ADV-OTHER-{tag}", "employee_id": other.employee_no, "employee": me.full_name, "status": "Pending"})

    tasks = client.get("/api/v1/ess/tasks", headers=headers).json()
    assert [t["id"] for t in tasks] == [f"T-MINE-{tag}"]

    ids = {r["id"] for r in client.get("/api/v1/ess/requests", headers=headers).json()}
    assert {f"OT-MINE-{tag}", f"LN-LEGACY-{tag}"} <= ids
    assert f"ADV-OTHER-{tag}" not in ids and f"T-OTHER-{tag}" not in ids
