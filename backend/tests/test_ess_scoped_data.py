"""GET/POST /ess/leave, GET /ess/tasks, GET /ess/rota -- the ESS portal's
new self-service features (leave requests, own tasks, own rota). The whole
point of these endpoints is that an employee can only ever see THEIR OWN
data through them, never a co-worker's -- every test here that seeds two
employees explicitly asserts on that isolation, not just on the happy path
for one employee."""
import json

from app.models import AppDataRecord, Employee, LeaveRequest


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": f"{username}pw123", "is_active": True},
    )
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return emp, headers


def _setup_two_employees(client, db, auth_headers, prefix):
    company_id = _company_id(client, auth_headers)
    emp_a, headers_a = _ess_login(client, db, auth_headers, company_id, f"{prefix}-A", f"{prefix.lower()}.a")
    emp_b, headers_b = _ess_login(client, db, auth_headers, company_id, f"{prefix}-B", f"{prefix.lower()}.b")
    return company_id, emp_a, headers_a, emp_b, headers_b


def test_ess_leave_create_and_list_scoped_to_self(client, db, auth_headers):
    company_id, emp_a, headers_a, emp_b, headers_b = _setup_two_employees(client, db, auth_headers, "ESSLEAVE")

    r = client.post("/api/v1/ess/leave", headers=headers_a, json={
        "leave_type": "Annual Leave", "start_date": "2026-10-05", "end_date": "2026-10-07", "reason": "Trip",
    })
    assert r.status_code == 201, r.text
    assert r.json()["days"] == 3
    assert r.json()["status"] == "pending"

    # A sees their own request.
    r = client.get("/api/v1/ess/leave", headers=headers_a)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["leave_type"] == "Annual Leave"

    # B must NOT see A's request through their own /ess/leave call.
    r = client.get("/api/v1/ess/leave", headers=headers_b)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_ess_leave_rejects_bad_type_and_overlap(client, db, auth_headers):
    _company_id, emp_a, headers_a, _emp_b, _headers_b = _setup_two_employees(client, db, auth_headers, "ESSLEAVE2")

    r = client.post("/api/v1/ess/leave", headers=headers_a, json={
        "leave_type": "Not A Real Type", "start_date": "2026-10-05", "end_date": "2026-10-07",
    })
    assert r.status_code == 400, r.text

    r = client.post("/api/v1/ess/leave", headers=headers_a, json={
        "leave_type": "Sick Leave", "start_date": "2026-11-01", "end_date": "2026-11-03",
    })
    assert r.status_code == 201, r.text

    # Overlapping the just-created pending request.
    r = client.post("/api/v1/ess/leave", headers=headers_a, json={
        "leave_type": "Casual Leave", "start_date": "2026-11-02", "end_date": "2026-11-04",
    })
    assert r.status_code == 409, r.text


def test_ess_tasks_scoped_to_assignee_only(client, db, auth_headers):
    company_id, emp_a, headers_a, emp_b, headers_b = _setup_two_employees(client, db, auth_headers, "ESSTASK")

    db.add(AppDataRecord(company_id=company_id, collection="tasks", record_key="T-A",
                          payload=json.dumps({"id": "T-A", "title": "Task for A", "assigned_to": emp_a.id, "due_date": "2026-09-10"})))
    db.add(AppDataRecord(company_id=company_id, collection="tasks", record_key="T-B",
                          payload=json.dumps({"id": "T-B", "title": "Task for B", "assigned_to": emp_b.id, "due_date": "2026-09-11"})))
    db.add(AppDataRecord(company_id=company_id, collection="tasks", record_key="T-UNASSIGNED",
                          payload=json.dumps({"id": "T-UNASSIGNED", "title": "Nobody's task", "assigned_to": ""})))
    db.commit()

    r = client.get("/api/v1/ess/tasks", headers=headers_a)
    assert r.status_code == 200, r.text
    titles = [t["title"] for t in r.json()]
    assert titles == ["Task for A"]

    r = client.get("/api/v1/ess/tasks", headers=headers_b)
    assert r.status_code == 200, r.text
    titles = [t["title"] for t in r.json()]
    assert titles == ["Task for B"]


def test_ess_rota_scoped_to_employee_and_date_window(client, db, auth_headers):
    company_id, emp_a, headers_a, emp_b, headers_b = _setup_two_employees(client, db, auth_headers, "ESSROTA")

    from datetime import date, timedelta
    today = date.today()
    in_window = (today + timedelta(days=3)).isoformat()
    out_of_window = (today + timedelta(days=90)).isoformat()

    # rotaAssignments.employee_id is keyed by employee_no, NOT Employee.id --
    # confirmed against live production data (Rota's own currentRotaStaff()/
    # employeeFromDirectoryRow() in app.js reads the Employee Directory
    # table's visible "ID" column, which is employee_no). A different
    # convention from "tasks".assigned_to (real Employee.id UUID) -- see
    # ess_rota()'s own docstring in ess.py for the full story.
    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key="RA-A-1",
                          payload=json.dumps({"id": "RA-A-1", "employee_id": emp_a.employee_no, "date": in_window, "code": "M", "start": "08:00", "end": "16:00"})))
    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key="RA-A-2",
                          payload=json.dumps({"id": "RA-A-2", "employee_id": emp_a.employee_no, "date": out_of_window, "code": "M", "start": "08:00", "end": "16:00"})))
    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key="RA-B-1",
                          payload=json.dumps({"id": "RA-B-1", "employee_id": emp_b.employee_no, "date": in_window, "code": "E", "start": "16:00", "end": "00:00"})))
    db.commit()

    r = client.get("/api/v1/ess/rota", headers=headers_a)
    assert r.status_code == 200, r.text
    dates = [a["date"] for a in r.json()]
    # A's own in-window shift shows; A's own out-of-window shift and B's
    # shift (right employee, but not A's) must not.
    assert dates == [in_window]

    r = client.get("/api/v1/ess/rota", headers=headers_b)
    assert r.status_code == 200, r.text
    assert [a["date"] for a in r.json()] == [in_window]
    assert r.json()[0]["code"] == "E"


def test_ess_rota_month_param_overrides_the_default_window(client, db, auth_headers):
    """The Rota tab's month picker -- checking a specific past/future month
    isn't asking for "recent", so the rolling 7-days-back/30-days-forward
    default must not apply when ?month= is given."""
    company_id, emp_a, headers_a, _emp_b, _headers_b = _setup_two_employees(client, db, auth_headers, "ESSROTAMONTH")

    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key="RAM-1",
                          payload=json.dumps({"id": "RAM-1", "employee_id": emp_a.employee_no, "date": "2026-12-15", "code": "M", "start": "08:00", "end": "16:00"})))
    # Outside the requested month but well within the default rolling window.
    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key="RAM-2",
                          payload=json.dumps({"id": "RAM-2", "employee_id": emp_a.employee_no, "date": "2026-09-20", "code": "E", "start": "16:00", "end": "00:00"})))
    db.commit()

    r = client.get("/api/v1/ess/rota?month=2026-12", headers=headers_a)
    assert r.status_code == 200, r.text
    assert [a["date"] for a in r.json()] == ["2026-12-15"]

    r = client.get("/api/v1/ess/rota?month=2026-11", headers=headers_a)
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = client.get("/api/v1/ess/rota?month=not-a-month", headers=headers_a)
    assert r.status_code == 400, r.text
