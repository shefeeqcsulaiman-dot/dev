"""Department scoping for HRMS logins.

A role with departments ticked (Role.department_scope) makes its holders see --
and change -- only those departments' Employees, Tasks, Leave, Attendance,
Rota, Overtime, Loans and Payroll. A role with NO departments, and the admin
User, must behave exactly as before.

Two throw-away departments ("ScopeDeptA", "ScopeDeptB") keep these tests
independent of whatever other test files left in the shared test company;
assertions look at this file's own SCP-* rows, never at counts."""
import json
from datetime import UTC, date, datetime

from app.models import (
    AppDataRecord, AttendanceDetail, AttendanceSession, CompanyLocation, Employee, EmployeeLocation, LeaveRequest,
    PayrollItem, PayrollRun, Role,
)

DEPT_A, DEPT_B = "ScopeDeptA", "ScopeDeptB"
PERMS = [
    "employees:view", "employees:edit", "employees:delete", "leave:view", "leave:edit", "leave:delete",
    "attendance:view", "attendance:edit", "overtime:view", "overtime:edit", "loans:view", "loans:edit",
    "rota:view", "rota:edit", "payroll:view", "hr_workflow:view", "hr_workflow:edit", "hr_workflow:delete",
    "hr_settings:view", "hr_settings:edit", "hr_settings:delete",
]


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"}, json={"collection": collection, "record": record})
    return r


def _delete(client, headers, collection, record):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "delete"}, json={"collection": collection, "record": record})


def _mk_employee(client, admin_headers, db, emp_no, dept):
    r = _save(client, admin_headers, "employees", {
        "id": emp_no, "name": f"{emp_no} Person", "department": dept, "designation": "Staff", "status": "Active", "salary": 4000,
    })
    assert r.status_code == 200, r.text
    return db.query(Employee).filter(Employee.employee_no == emp_no).one()


def _login_as(client, db, admin_headers, company_id, tag, departments, permission_keys=PERMS):
    """A viewer employee (in DEPT_A) whose role is scoped to `departments`."""
    role = client.post("/api/v1/hr/admin/roles", headers=admin_headers, json={
        "role_name": f"Scope Role {tag}", "description": "t", "permission_keys": permission_keys, "department_scope": departments,
    })
    assert role.status_code == 201, role.text
    emp = Employee(company_id=company_id, employee_no=f"SCP-VIEW-{tag}", full_name=f"Viewer {tag}", department=DEPT_A, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers,
                   json={"username": f"scp.{tag}", "password": "scope12345", "role_id": role.json()["id"], "is_active": True})
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": f"scp.{tag}", "password": "scope12345"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _world(client, db, auth_headers, tag):
    """Employees A1 (DEPT_A) / B1 (DEPT_B) with a task, leave, OT, loan, punch and payroll line each."""
    cid = _company_id(client, auth_headers)
    a = _mk_employee(client, auth_headers, db, f"SCP-A1-{tag}", DEPT_A)
    b = _mk_employee(client, auth_headers, db, f"SCP-B1-{tag}", DEPT_B)
    assert _save(client, auth_headers, "tasks", {"id": f"T-A-{tag}", "title": "A task", "assigned_to": a.id, "assigned_to_name": a.full_name, "status": "todo"}).status_code == 200
    assert _save(client, auth_headers, "tasks", {"id": f"T-B-{tag}", "title": "B task", "assigned_to": b.id, "assigned_to_name": b.full_name, "status": "todo"}).status_code == 200
    assert _save(client, auth_headers, "tasks", {"id": f"T-U-{tag}", "title": "Unassigned", "assigned_to": "", "status": "todo"}).status_code == 200
    for who, emp in (("A", a), ("B", b)):
        assert _save(client, auth_headers, "overtimeRequests", {"id": f"OT-{who}-{tag}", "employee": emp.full_name, "employee_id": emp.employee_no, "date": "2026-09-01", "ot_hours": "2", "status": "Pending"}).status_code == 200
        assert _save(client, auth_headers, "employeeLoans", {"id": f"LN-{who}-{tag}", "employee": emp.full_name, "employee_id": emp.employee_no, "amount": 100, "status": "Pending"}).status_code == 200
        assert _save(client, auth_headers, "rotaAssignments", {"id": f"RA-{who}-{tag}", "employee": emp.full_name, "employee_id": emp.employee_no, "date": "2026-09-02"}).status_code == 200
        db.add(LeaveRequest(company_id=cid, employee_id=emp.id, leave_type="Annual Leave", start_date="2026-10-01", end_date="2026-10-02", days=2, status="pending"))
        today = date.today().isoformat()
        now = datetime.now(UTC)
        db.add(AttendanceDetail(
            company_id=cid, employee_id=emp.employee_no, employee_name=emp.full_name, work_date=today, clock_in_1=now,
            raw_events=json.dumps([{"id": f"PUNCH-{who}-{tag}", "punch_time": now.isoformat(), "direction": "in", "source": "biometric"}]),
        ))
    # Keyed on the full tag, not just its first character — every other
    # piece of fixture data in this helper (employee numbers, task/OT/loan/
    # rota ids) already assumes `tag` is unique per call, but this used
    # `ord(tag[0]) % 12 + 1` alone, which collapses to the same "2099-MM"
    # period for any two tags sharing a first letter (e.g. "R1"/"R2"/"RG2").
    # auth_headers/db are reused across many test functions in the same
    # session against one company, so two _world() calls sharing a period
    # now hit payroll_runs' new (company_id, period) uniqueness constraint
    # (main.py's uq_payroll_runs_company_period_companywide — see
    # generate_payroll()'s duplicate-run bug this constraint fixes).
    run = PayrollRun(company_id=cid, period=f"2099-{tag}"[:20], status="draft", gross_total=8000, deductions_total=0, net_total=8000)
    db.add(run)
    db.flush()
    for emp in (a, b):
        db.add(PayrollItem(run_id=run.id, employee_id=emp.id, basic=4000, allowances=0, overtime=0, deductions=0, net_pay=4000))
    db.commit()
    return a, b, run


def _ids(records, key="id"):
    return {r.get(key) for r in records}


# ── reads ────────────────────────────────────────────────────────────────────

def test_bootstrap_and_records_only_show_the_scoped_departments(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "R1")
    headers = _login_as(client, db, auth_headers, cid, "r1", [DEPT_A])

    colls = client.get("/api/v1/app-data", headers=headers, params={"scope": "hrms"}).json()["data"]
    emp_ids = _ids(colls["employees"])
    assert a.employee_no in emp_ids and b.employee_no not in emp_ids
    assert {"OT-A-R1"} <= _ids(colls["overtimeRequests"]) and "OT-B-R1" not in _ids(colls["overtimeRequests"])
    assert "LN-A-R1" in _ids(colls["employeeLoans"]) and "LN-B-R1" not in _ids(colls["employeeLoans"])
    assert "RA-A-R1" in _ids(colls["rotaAssignments"]) and "RA-B-R1" not in _ids(colls["rotaAssignments"])
    # the Task module: now part of the hrms bootstrap for a role with hr_workflow:view
    assert "T-A-R1" in _ids(colls["tasks"]) and "T-U-R1" in _ids(colls["tasks"]) and "T-B-R1" not in _ids(colls["tasks"])

    for coll, mine, other in (
        ("employees", a.employee_no, b.employee_no), ("tasks", "T-A-R1", "T-B-R1"),
        ("overtimeRequests", "OT-A-R1", "OT-B-R1"), ("employeeLoans", "LN-A-R1", "LN-B-R1"), ("rotaAssignments", "RA-A-R1", "RA-B-R1"),
    ):
        body = client.get(f"/api/v1/app-data/records/{coll}", headers=headers, params={"limit": 500}).json()
        ids = _ids(body["records"])
        assert mine in ids and other not in ids, coll
        assert body["total"] == len(body["records"]), coll   # pagination totals are computed AFTER filtering


def test_leave_lists_are_scoped_and_actions_on_other_departments_are_404(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "L1")
    headers = _login_as(client, db, auth_headers, cid, "l1", [DEPT_A])

    rows = client.get("/api/v1/leave/requests", headers=headers).json()
    assert a.id in {r["employee_id"] for r in rows} and b.id not in {r["employee_id"] for r in rows}
    balance = client.get("/api/v1/leave/balance", headers=headers).json()
    assert a.id in {r["employee_id"] for r in balance} and b.id not in {r["employee_id"] for r in balance}

    b_req = db.query(LeaveRequest).filter(LeaveRequest.employee_id == b.id).first()
    a_req = db.query(LeaveRequest).filter(LeaveRequest.employee_id == a.id).first()
    assert client.post(f"/api/v1/leave/requests/{b_req.id}/approve", headers=headers).status_code == 404
    assert client.post(f"/api/v1/leave/requests/{b_req.id}/reject", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/leave/requests/{b_req.id}", headers=headers).status_code == 404
    assert client.post(f"/api/v1/leave/requests/{a_req.id}/reject", headers=headers).status_code == 200
    # cannot file leave for another department's employee either
    r = client.post("/api/v1/leave/requests", headers=headers, json={"employee_id": b.id, "leave_type": "Annual Leave", "start_date": "2027-01-04", "end_date": "2027-01-05"})
    assert r.status_code == 404


def test_attendance_endpoints_are_scoped(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "T1")
    headers = _login_as(client, db, auth_headers, cid, "t1", [DEPT_A])

    today = client.get("/api/v1/attendance/today", headers=headers).json()
    assert a.employee_no in today["employee_ids"] and b.employee_no not in today["employee_ids"]
    punches = client.get("/api/v1/attendance/punches", headers=headers, params={"limit": 200}).json()["punches"]
    assert "PUNCH-A-T1" in _ids(punches) and "PUNCH-B-T1" not in _ids(punches)
    assert client.get(f"/api/v1/attendance/employee-daily?employee_id={a.id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/attendance/employee-daily?employee_id={b.id}", headers=headers).status_code == 404
    # the monthly / late reports list one row per in-scope employee only
    for path in ("monthly-report", "late-report"):
        rows = client.get(f"/api/v1/attendance/{path}", headers=headers).json()["employees"]
        nos = {r.get("employee_no") or r.get("employee_id") for r in rows}
        assert b.employee_no not in nos and b.id not in nos, path
    # cannot delete another department's punch (404, doesn't reveal it exists) but can delete its own
    assert client.delete("/api/v1/attendance/punches/PUNCH-B-T1", headers=headers).status_code == 404
    assert client.delete("/api/v1/attendance/punches/PUNCH-A-T1", headers=headers).status_code == 204


def test_payroll_lists_and_portal_access_list_are_scoped(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, run = _world(client, db, auth_headers, "P1")
    headers = _login_as(client, db, auth_headers, cid, "p1", [DEPT_A])

    emps = client.get("/api/v1/payroll/employees", headers=headers).json()
    assert a.employee_no in {e["employee_no"] for e in emps} and b.employee_no not in {e["employee_no"] for e in emps}
    runs = client.get("/api/v1/payroll/runs", headers=headers).json()
    mine = next(r for r in runs if r["id"] == run.id)
    assert [i["employee_id"] for i in mine["items"]] == [a.id]
    assert float(mine["net_total"]) == 4000.0            # totals recomputed for the visible lines only
    # the ORM rows were never modified
    db.expire_all()
    assert float(db.get(PayrollRun, run.id).net_total) == 8000.0
    assert db.query(PayrollItem).filter(PayrollItem.run_id == run.id).count() == 2

    portal = client.get("/api/v1/hr/admin/employees", headers=headers).json()
    assert a.id in {e["id"] for e in portal} and b.id not in {e["id"] for e in portal}
    assert client.put(f"/api/v1/hr/admin/employees/{b.id}/portal-access", headers=headers, json={"is_active": False}).status_code == 404
    assert client.delete(f"/api/v1/hr/admin/employees/{b.id}/portal-access", headers=headers).status_code == 404


# ── writes ───────────────────────────────────────────────────────────────────

def test_writes_outside_the_scope_are_refused_on_the_server(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "W1")
    headers = _login_as(client, db, auth_headers, cid, "w1", [DEPT_A])

    # create data for / edit / delete another department's employee record
    assert _save(client, headers, "overtimeRequests", {"id": "OT-NEW-B", "employee": b.full_name, "employee_id": b.employee_no, "status": "Pending"}).status_code == 403
    assert _save(client, headers, "overtimeRequests", {"id": "OT-B-W1", "employee": a.full_name, "employee_id": a.employee_no, "status": "Approved"}).status_code == 403   # hijack B's record by re-pointing it at A
    assert _save(client, headers, "employeeLoans", {"id": "LN-B-W1", "employee": b.full_name, "employee_id": b.employee_no, "status": "Approved"}).status_code == 403
    assert _delete(client, headers, "employeeLoans", {"id": "LN-B-W1"}).status_code == 403
    # ...but the same operations inside the scope work
    assert _save(client, headers, "overtimeRequests", {"id": "OT-NEW-A", "employee": a.full_name, "employee_id": a.employee_no, "status": "Pending"}).status_code == 200
    assert _save(client, headers, "employeeLoans", {"id": "LN-A-W1", "employee": a.full_name, "employee_id": a.employee_no, "status": "Approved"}).status_code == 200
    assert _delete(client, headers, "employeeLoans", {"id": "LN-A-W1"}).status_code == 200
    # B's record is untouched
    stored = json.loads(db.query(AppDataRecord).filter(AppDataRecord.collection == "employeeLoans", AppDataRecord.record_key == "LN-B-W1").one().payload)
    assert stored["status"] == "Pending"

    # employees: cannot create/move someone into another department, nor edit one there
    assert _save(client, headers, "employees", {"id": "SCP-NEW-B", "name": "New B", "department": DEPT_B}).status_code == 403
    assert _save(client, headers, "employees", {"id": b.employee_no, "name": "Renamed", "department": DEPT_B}).status_code == 403
    assert _save(client, headers, "employees", {"id": a.employee_no, "name": "Moved", "department": DEPT_B}).status_code == 403
    assert _save(client, headers, "employees", {"id": "SCP-NEW-A", "name": "New A", "department": DEPT_A}).status_code == 200
    # bulk endpoints are covered too
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "bulk-save"}, json={"collection": "employeeLoans", "records": [
        {"id": "LN-BULK-A", "employee_id": a.employee_no, "employee": a.full_name}, {"id": "LN-BULK-B", "employee_id": b.employee_no, "employee": b.full_name}]})
    assert r.status_code == 403
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "bulk-delete"}, json={"collection": "employeeLoans", "records": [{"id": "LN-B-W1"}]})
    assert r.status_code == 403


def test_tasks_unassigned_visible_and_other_department_blocked(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "K1")
    headers = _login_as(client, db, auth_headers, cid, "k1", [DEPT_A])

    assert _save(client, headers, "tasks", {"id": "T-NEW-U", "title": "Nobody yet", "assigned_to": ""}).status_code == 200        # unassigned: allowed
    assert _save(client, headers, "tasks", {"id": "T-NEW-A", "title": "For A", "assigned_to": a.id, "assigned_to_name": a.full_name}).status_code == 200
    assert _save(client, headers, "tasks", {"id": "T-NEW-B", "title": "For B", "assigned_to": b.id, "assigned_to_name": b.full_name}).status_code == 403
    assert _save(client, headers, "tasks", {"id": "T-B-K1", "title": "steal", "assigned_to": a.id, "assigned_to_name": a.full_name}).status_code == 403   # cannot re-assign B's task to A
    assert _delete(client, headers, "tasks", {"id": "T-B-K1"}).status_code == 403
    # assign an unassigned task to A: fine; then it is A's
    assert _save(client, headers, "tasks", {"id": "T-U-K1", "title": "Unassigned", "assigned_to": a.id, "assigned_to_name": a.full_name}).status_code == 200


def test_company_wide_payroll_run_records_are_hidden_and_read_only_for_a_scoped_login(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    assert _save(client, auth_headers, "payrollRuns", {"id": "PAY-SCOPE-1", "period": "2099-01", "status": "Approved"}).status_code == 200
    headers = _login_as(client, db, auth_headers, cid, "c1", [DEPT_A])
    body = client.get("/api/v1/app-data/records/payrollRuns", headers=headers).json()
    assert body["records"] == [] and body["total"] == 0
    assert _save(client, headers, "payrollRuns", {"id": "PAY-SCOPE-2", "period": "2099-02", "status": "Approved"}).status_code == 403
    # payroll generation was already admin-User only: an employee login cannot run it at all
    assert client.post("/api/v1/payroll/generate", headers=headers, json={"period": "2099-03"}).status_code in (401, 403)


# ── not scoped: nothing changes ──────────────────────────────────────────────

def test_a_role_without_departments_and_the_admin_are_unchanged(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, run = _world(client, db, auth_headers, "N1")
    unscoped = _login_as(client, db, auth_headers, cid, "n1", [])

    for headers in (unscoped, auth_headers):
        emps = _ids(client.get("/api/v1/app-data/records/employees", headers=headers, params={"limit": 500}).json()["records"])
        assert a.employee_no in emps and b.employee_no in emps
        tasks = _ids(client.get("/api/v1/app-data/records/tasks", headers=headers, params={"limit": 500}).json()["records"])
        assert {"T-A-N1", "T-B-N1", "T-U-N1"} <= tasks
        leave = {r["employee_id"] for r in client.get("/api/v1/leave/requests", headers=headers).json()}
        assert {a.id, b.id} <= leave
        assert {a.employee_no, b.employee_no} <= set(client.get("/api/v1/attendance/today", headers=headers).json()["employee_ids"])
        mine = next(r for r in client.get("/api/v1/payroll/runs", headers=headers).json() if r["id"] == run.id)
        assert len(mine["items"]) == 2 and float(mine["net_total"]) == 8000.0
    assert _save(client, unscoped, "employeeLoans", {"id": "LN-N1-B", "employee": b.full_name, "employee_id": b.employee_no}).status_code == 200


def test_role_with_task_permission_receives_tasks_from_the_hrms_bootstrap(client, db, auth_headers):
    """The 'Task module missing' bug: tasks were never part of any module's bootstrap collections."""
    cid = _company_id(client, auth_headers)
    a, _b, _run = _world(client, db, auth_headers, "M1")
    with_perm = _login_as(client, db, auth_headers, cid, "m1a", [], ["hr_workflow:view", "employees:view"])
    without = _login_as(client, db, auth_headers, cid, "m1b", [], ["employees:view"])
    got = client.get("/api/v1/app-data", headers=with_perm, params={"scope": "hrms"}).json()["data"]
    assert "T-A-M1" in _ids(got.get("tasks", []))
    lacking = client.get("/api/v1/app-data", headers=without, params={"scope": "hrms"}).json()["data"]
    assert not lacking.get("tasks")


# ── GPS / locations / HR dashboard / reports summary (found by an all-endpoints crawl) ──

def test_gps_locations_hr_dashboard_and_report_audit_trail_are_scoped(client, db, auth_headers):
    from datetime import timedelta
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "G1")
    gps_perms = PERMS + ["hr:view_all_attendance", "hr:manage_locations", "hr:manage_employees", "reports:view"]
    headers = _login_as(client, db, auth_headers, cid, "g1", [DEPT_A], gps_perms)

    loc = CompanyLocation(company_id=cid, location_name="HQ G1", latitude=25.2, longitude=55.27)
    db.add(loc)
    db.flush()
    now = datetime.now(UTC)
    for emp in (a, b):
        db.add(EmployeeLocation(employee_id=emp.id, location_id=loc.id, is_primary=True))
        db.add(AttendanceSession(company_id=cid, employee_id=emp.id, location_id=loc.id, check_in=now - timedelta(hours=1),
                                 check_in_lat=25.2, check_in_lng=55.27, status="open"))
    db.commit()

    # live GPS positions: only the scoped department's people
    live = client.get("/api/v1/hr/live-locations", headers=headers).json()
    assert a.id in {x["employee_id"] for x in live} and b.id not in {x["employee_id"] for x in live}
    # which employee is assigned to which work location
    links = client.get("/api/v1/hr/employee-locations", headers=headers).json()
    assert a.id in {x["employee_id"] for x in links} and b.id not in {x["employee_id"] for x in links}
    # cannot assign / unassign another department's employee
    assert client.post("/api/v1/hr/employee-locations", headers=headers, json={"employee_id": b.id, "location_id": loc.id, "is_primary": False}).status_code == 404
    b_link = db.query(EmployeeLocation).filter(EmployeeLocation.employee_id == b.id).first()
    assert client.delete(f"/api/v1/hr/employee-locations/{b_link.id}", headers=headers).status_code == 404
    assert db.query(EmployeeLocation).filter(EmployeeLocation.id == b_link.id).count() == 1
    # an employee login with NO departments ticked still sees everyone (/hr/* endpoints are employee-only)
    unscoped = _login_as(client, db, auth_headers, cid, "g1u", [], gps_perms)
    everyone = client.get("/api/v1/hr/live-locations", headers=unscoped).json()
    assert {a.id, b.id} <= {x["employee_id"] for x in everyone}
    assert {a.id, b.id} <= {x["employee_id"] for x in client.get("/api/v1/hr/employee-locations", headers=unscoped).json()}

    # the reports summary's audit trail holds raw records of other departments' employees
    assert _save(client, auth_headers, "employees", {"id": "SCP-AUDIT-B", "name": "AuditMarkerB", "department": DEPT_B}).status_code == 200
    summary = client.get("/api/v1/reports/summary", headers=headers)
    assert summary.status_code == 200
    assert "AuditMarkerB" not in summary.text and summary.json()["control"]["audit"] == []
    assert "AuditMarkerB" in client.get("/api/v1/reports/summary", headers=auth_headers).text   # admin (and the shared cache) untouched


# ── Shift Setup: department-wise shifts (a shift can list several departments) ──

def _shift(code, departments=None, **extra):
    rec = {"id": code, "code": code, "name": f"Shift {code}", "start": "09:00", "end": "17:00", "break_minutes": 30, **extra}
    if departments is not None:
        rec["departments"] = departments
    return rec


def test_shifts_are_department_wise_multi_department_and_protected(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    for rec in (_shift("SH-ALL"), _shift("SH-A", [DEPT_A]), _shift("SH-B", [DEPT_B]), _shift("SH-AB", [DEPT_A, DEPT_B]), _shift("SH-CSV", f"{DEPT_A}, {DEPT_B}")):
        assert _save(client, auth_headers, "rotaShifts", rec).status_code == 200
    headers = _login_as(client, db, auth_headers, cid, "s1", [DEPT_A])

    # a department-limited login sees: all-department shifts + any shift that lists its department (alone or shared)
    boot = client.get("/api/v1/app-data", headers=headers, params={"scope": "hrms"}).json()["data"]
    seen = _ids(boot["rotaShifts"])
    assert {"SH-ALL", "SH-A", "SH-AB", "SH-CSV"} <= seen and "SH-B" not in seen
    listed = _ids(client.get("/api/v1/app-data/records/rotaShifts", headers=headers, params={"limit": 500}).json()["records"])
    assert {"SH-ALL", "SH-A", "SH-AB"} <= listed and "SH-B" not in listed

    # can create a shift only for its own department(s)
    assert _save(client, headers, "rotaShifts", _shift("SH-NEW-A", [DEPT_A])).status_code == 200
    assert _save(client, headers, "rotaShifts", _shift("SH-NEW-B", [DEPT_B])).status_code == 403          # someone else's department
    assert _save(client, headers, "rotaShifts", _shift("SH-NEW-AB", [DEPT_A, DEPT_B])).status_code == 403  # shares with another department
    assert _save(client, headers, "rotaShifts", _shift("SH-NEW-ALL", [])).status_code == 403              # would apply to every department
    assert _save(client, headers, "rotaShifts", _shift("SH-NEW-NONE")).status_code == 403
    # can edit / delete its OWN department's shift, but not a shared or all-department one, nor another department's
    assert _save(client, headers, "rotaShifts", _shift("SH-A", [DEPT_A], start="08:00")).status_code == 200
    assert _save(client, headers, "rotaShifts", _shift("SH-AB", [DEPT_A], start="08:00")).status_code == 403   # cannot take over a shared shift
    assert _save(client, headers, "rotaShifts", _shift("SH-ALL", [DEPT_A])).status_code == 403
    assert _save(client, headers, "rotaShifts", _shift("SH-B", [DEPT_A])).status_code == 403
    assert _delete(client, headers, "rotaShifts", {"id": "SH-AB", "code": "SH-AB"}).status_code == 403
    assert _delete(client, headers, "rotaShifts", {"id": "SH-B", "code": "SH-B"}).status_code == 403
    assert _delete(client, headers, "rotaShifts", {"id": "SH-NEW-A", "code": "SH-NEW-A"}).status_code == 200
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "bulk-save"},
                    json={"collection": "rotaShifts", "records": [_shift("SH-BULK-A", [DEPT_A]), _shift("SH-BULK-B", [DEPT_B])]})
    assert r.status_code == 403
    stored = json.loads(db.query(AppDataRecord).filter(AppDataRecord.collection == "rotaShifts", AppDataRecord.record_key == "SH-AB").one().payload)
    assert stored["departments"] == [DEPT_A, DEPT_B] and stored["start"] == "09:00"                        # untouched

    # unrestricted logins (no departments ticked) and the admin: everything, unchanged
    unscoped = _login_as(client, db, auth_headers, cid, "s1u", [])
    for h in (unscoped, auth_headers):
        all_ids = _ids(client.get("/api/v1/app-data/records/rotaShifts", headers=h, params={"limit": 500}).json()["records"])
        assert {"SH-ALL", "SH-A", "SH-B", "SH-AB", "SH-CSV"} <= all_ids
    assert _save(client, unscoped, "rotaShifts", _shift("SH-U-ALL", [])).status_code == 200
    assert _save(client, unscoped, "rotaShifts", _shift("SH-U-AB", [DEPT_A, DEPT_B])).status_code == 200


# ── Role option: "Only the employee's own department" ("@own") ──

def _role(client, admin_headers, name, departments, permission_keys=PERMS):
    r = client.post("/api/v1/hr/admin/roles", headers=admin_headers, json={
        "role_name": name, "description": "t", "permission_keys": permission_keys, "department_scope": departments})
    assert r.status_code == 201, r.text
    return r.json()


def _login_holder(client, db, admin_headers, company_id, tag, role_id, dept):
    emp = Employee(company_id=company_id, employee_no=f"SCP-OWN-{tag}", full_name=f"Holder {tag}", department=dept, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers,
                   json={"username": f"own.{tag}", "password": "scope12345", "role_id": role_id, "is_active": True})
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": f"own.{tag}", "password": "scope12345"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_own_department_role_limits_each_holder_to_their_own_department(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "O1")
    role = _role(client, auth_headers, "Own Dept Role O1", ["@own"])
    assert role["department_scope"] == ["@own"]                      # the raw marker is what the role editor round-trips
    in_a = _login_holder(client, db, auth_headers, cid, "o1a", role["id"], DEPT_A)
    in_b = _login_holder(client, db, auth_headers, cid, "o1b", role["id"], DEPT_B)

    # the SAME role: the DEPT_A holder sees only DEPT_A, the DEPT_B holder only DEPT_B
    for headers, mine, other, mine_task, other_task in ((in_a, a, b, "T-A-O1", "T-B-O1"), (in_b, b, a, "T-B-O1", "T-A-O1")):
        emps = _ids(client.get("/api/v1/app-data/records/employees", headers=headers, params={"limit": 500}).json()["records"])
        assert mine.employee_no in emps and other.employee_no not in emps
        tasks = _ids(client.get("/api/v1/app-data/records/tasks", headers=headers, params={"limit": 500}).json()["records"])
        assert mine_task in tasks and other_task not in tasks
        assert mine.id in {r["employee_id"] for r in client.get("/api/v1/leave/requests", headers=headers).json()}
        assert other.id not in {r["employee_id"] for r in client.get("/api/v1/leave/requests", headers=headers).json()}
        assert other.employee_no not in client.get("/api/v1/attendance/today", headers=headers).json()["employee_ids"]
        assert _save(client, headers, "overtimeRequests", {"id": f"OT-X-{mine.employee_no}", "employee": other.full_name, "employee_id": other.employee_no}).status_code == 403
        assert _save(client, headers, "overtimeRequests", {"id": f"OT-Y-{mine.employee_no}", "employee": mine.full_name, "employee_id": mine.employee_no}).status_code == 200
    # /auth/whoami reports the RESOLVED department (drives the "Viewing: ..." label), not the marker
    assert client.get("/api/v1/auth/whoami", headers=in_a).json()["department_scope"] == [DEPT_A]
    assert client.get("/api/v1/auth/whoami", headers=in_b).json()["department_scope"] == [DEPT_B]
    assert client.get("/api/v1/hr/me", headers=in_b).json()["department_scope"] == [DEPT_B]


def test_own_department_shifts_and_extra_departments_and_blank_department(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "O2")
    for rec in (_shift("OS-ALL"), _shift("OS-A", [DEPT_A]), _shift("OS-B", [DEPT_B])):
        assert _save(client, auth_headers, "rotaShifts", rec).status_code == 200
    own = _role(client, auth_headers, "Own Dept Role O2", ["@own"])
    holder = _login_holder(client, db, auth_headers, cid, "o2", own["id"], DEPT_A)
    shifts = _ids(client.get("/api/v1/app-data/records/rotaShifts", headers=holder, params={"limit": 500}).json()["records"])
    assert {"OS-ALL", "OS-A"} <= shifts and "OS-B" not in shifts
    assert _save(client, holder, "rotaShifts", _shift("OS-NEW", [DEPT_A])).status_code == 200
    assert _save(client, holder, "rotaShifts", _shift("OS-NEW2", [DEPT_B])).status_code == 403

    # "own department" PLUS an extra named department
    both = _role(client, auth_headers, "Own Plus B O2", ["@own", DEPT_B])
    two = _login_holder(client, db, auth_headers, cid, "o2b", both["id"], DEPT_A)
    emps = _ids(client.get("/api/v1/app-data/records/employees", headers=two, params={"limit": 500}).json()["records"])
    assert a.employee_no in emps and b.employee_no in emps
    assert sorted(client.get("/api/v1/auth/whoami", headers=two).json()["department_scope"]) == sorted([DEPT_A, DEPT_B])

    # a holder with NO department must see nothing -- never fall back to company-wide
    blank = _login_holder(client, db, auth_headers, cid, "o2c", own["id"], "")
    assert client.get("/api/v1/app-data/records/employees", headers=blank, params={"limit": 500}).json()["records"] == []
    assert client.get("/api/v1/leave/requests", headers=blank).json() == []
    assert client.get("/api/v1/attendance/today", headers=blank).json()["employee_ids"] == []


def test_own_department_marker_does_not_break_the_ess_team_roster(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    a, b, _run = _world(client, db, auth_headers, "O3")
    role = _role(client, auth_headers, "Own Dept Role O3", ["@own"])
    holder = _login_holder(client, db, auth_headers, cid, "o3", role["id"], DEPT_B)
    team = client.get("/api/v1/ess/team", headers=holder)
    assert team.status_code == 200, team.text
    names = {t["employee_no"] for t in team.json()}
    assert b.employee_no in names and a.employee_no not in names       # DEPT_B holder -> the DEPT_B roster

