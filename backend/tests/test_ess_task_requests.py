"""ESS "Request extra task": an employee asks for extra work, a manager approves it in
Task Management (POST /app-data/task-requests/{id}/decide) and the task appears in the
employee's ESS task list. Approval creates exactly one task; a decided request is final."""
import json
from datetime import date, timedelta

from app.models import AppDataRecord
from tests.test_ess_requests import _company_id, _ess_login


def _tasks_for(db, company_id, emp_id):
    rows = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "tasks").all()
    return [t for t in (json.loads(r.payload) for r in rows) if t.get("assigned_to") == emp_id]


def _ask(client, h, **body):
    return client.post("/api/v1/ess/task-requests", headers=h, json={"title": "Help with month-end stock count", **body})


def test_employee_asks_and_manager_approves_into_a_task(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSTR-1", "esstr.one")
    due = (date.today() + timedelta(days=7)).isoformat()
    r = _ask(client, h, description="I have spare time on Thursdays", priority="high", due_date=due, reason="Want more experience")
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "Pending" and req["employee_id"] == "ESSTR-1" and req["priority"] == "High"

    listed = [q for q in client.get("/api/v1/ess/requests", headers=h).json() if q["kind"] == "task"]
    assert listed and listed[0]["title"] == "Help with month-end stock count" and listed[0]["status"] == "pending"

    r = client.post(f"/api/v1/app-data/task-requests/{req['id']}/decide", headers=auth_headers,
                    json={"decision": "approve", "note": "Thanks, go ahead"})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["assigned_to"] == emp.id and task["status"] == "todo" and task["due_date"] == due
    assert task["task_request_id"] == req["id"]

    mine = client.get("/api/v1/ess/tasks", headers=h).json()
    assert [t["id"] for t in mine] == [task["id"]]
    q = [q for q in client.get("/api/v1/ess/requests", headers=h).json() if q["kind"] == "task"][0]
    assert q["status"] == "approved" and q["task_id"] == task["id"] and q["decision_note"] == "Thanks, go ahead"

    # A decided request is final: no second task.
    again = client.post(f"/api/v1/app-data/task-requests/{req['id']}/decide", headers=auth_headers, json={"decision": "approve"})
    assert again.status_code == 409
    assert len(_tasks_for(db, cid, emp.id)) == 1


def test_rejecting_creates_no_task(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSTR-2", "esstr.two")
    req = _ask(client, h).json()
    r = client.post(f"/api/v1/app-data/task-requests/{req['id']}/decide", headers=auth_headers,
                    json={"decision": "reject", "note": "Not this month"})
    assert r.status_code == 200 and r.json()["task"] is None
    assert _tasks_for(db, cid, emp.id) == []
    q = [q for q in client.get("/api/v1/ess/requests", headers=h).json() if q["kind"] == "task"][0]
    assert q["status"] == "rejected" and q["decision_note"] == "Not this month"


def test_request_is_validated(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSTR-3", "esstr.three")
    assert _ask(client, h, title="ab").status_code == 400
    assert _ask(client, h, priority="Urgent!!").status_code == 400
    assert _ask(client, h, due_date=(date.today() - timedelta(days=1)).isoformat()).status_code == 400
    assert _ask(client, h, due_date=(date.today() + timedelta(days=400)).isoformat()).status_code == 400
    # The employee comes from the token, never the body.
    r = _ask(client, h, employee_id="SOMEONE-ELSE")
    assert r.status_code == 201 and r.json()["employee_id"] == "ESSTR-3"


def test_employee_cannot_approve_own_request(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSTR-4", "esstr.four")
    req = _ask(client, h).json()
    r = client.post(f"/api/v1/app-data/task-requests/{req['id']}/decide", headers=h, json={"decision": "approve"})
    assert r.status_code in (401, 403)
    assert client.post("/api/v1/app-data/task-requests/TR-missing/decide", headers=auth_headers,
                       json={"decision": "approve"}).status_code == 404
    assert client.post(f"/api/v1/app-data/task-requests/{req['id']}/decide", headers=auth_headers,
                       json={"decision": "maybe"}).status_code == 400
    assert client.post("/api/v1/ess/task-requests", json={"title": "No login"}).status_code == 401


def test_bad_decision_unknown_request_and_departed_employee(client, db, auth_headers):
    from app.models import Employee

    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSTR-9", "esstr.nine")
    req = _ask(client, h).json()
    url = f"/api/v1/app-data/task-requests/{req['id']}/decide"

    assert client.post(url, headers=auth_headers, json={"decision": "maybe"}).status_code == 400
    assert client.post("/api/v1/app-data/task-requests/TR-nope/decide", headers=auth_headers,
                       json={"decision": "approve"}).status_code == 404

    # Employee left before the manager approved: refused, request stays Pending, no task.
    db.query(Employee).filter(Employee.id == emp.id).update({Employee.employee_no: "ESSTR-9-left"})
    db.commit()
    r = client.post(url, headers=auth_headers, json={"decision": "approve"})
    assert r.status_code == 409
    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "taskRequests",
                                         AppDataRecord.record_key == req["id"]).one()
    db.refresh(row)
    assert json.loads(row.payload)["status"] == "Pending"
    assert _tasks_for(db, cid, emp.id) == []
