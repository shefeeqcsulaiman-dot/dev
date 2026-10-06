"""Built-in HRMS roles: each can do its job out of the box, admins can edit them (except
Employee), and their edits survive later requests and restarts."""
import uuid

from sqlalchemy import text

from app.models import Employee, Role
from app.routers.hr_access import _DEFAULT_ROLES_FLAG, backfill_default_role_permissions
from tests.test_hrms_department_scope import _company_id

DEPT = "DefRoleDept"


def _roles(client, admin):
    return {r["role_name"]: r for r in client.get("/api/v1/hr/admin/roles", headers=admin).json()}


def _save(client, h, collection, record):
    return client.post("/api/v1/app-data?action=save", headers=h, json={"collection": collection, "record": record}).status_code


def _login_with_role(client, db, admin, role_id, tag):
    emp = Employee(company_id=_company_id(client, admin), employee_no=f"DR-{tag}", full_name=f"Role {tag}", department=DEPT, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin,
                   json={"username": f"dr.{tag}", "password": "RolePass123", "role_id": role_id, "is_active": True})
    assert r.status_code == 200, r.text
    tok = client.post("/api/v1/hr/login", json={"username": f"dr.{tag}", "password": "RolePass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _colleague(client, db, admin, tag):
    assert _save(client, admin, "employees", {"id": f"DRC-{tag}", "name": f"Colleague {tag}", "department": DEPT,
                                              "designation": "Staff", "status": "Active", "salary": 7100}) == 200
    return db.query(Employee).filter(Employee.employee_no == f"DRC-{tag}").one()


def _salary_visible(client, h, emp_no):
    rows = client.get("/api/v1/app-data/records/employees", headers=h).json()
    rows = rows if isinstance(rows, list) else rows.get("records", [])
    return any(r.get("id") == emp_no and r.get("salary") for r in rows)


def test_payroll_officer_can_prepare_payroll(client, db, auth_headers):
    tag = uuid.uuid4().hex[:6]
    col = _colleague(client, db, auth_headers, tag)
    h = _login_with_role(client, db, auth_headers, _roles(client, auth_headers)["Payroll Officer"]["id"], tag)
    assert client.get("/api/v1/payroll/employees", headers=h).status_code == 200
    assert _salary_visible(client, h, col.employee_no)
    for path in ("/api/v1/attendance/today", "/api/v1/app-data/records/overtimeRequests", "/api/v1/app-data/records/employeeLoans"):
        assert client.get(path, headers=h).status_code == 200, path
    assert _save(client, h, "employeeLoans", {"id": f"DRL-{tag}", "employee_id": col.employee_no, "amount": 100}) == 403


def test_hr_manager_covers_hr_but_cannot_run_payroll(client, db, auth_headers):
    tag = uuid.uuid4().hex[:6]
    col = _colleague(client, db, auth_headers, tag)
    h = _login_with_role(client, db, auth_headers, _roles(client, auth_headers)["HR Manager"]["id"], tag)
    assert _salary_visible(client, h, col.employee_no)
    assert client.get("/api/v1/payroll/runs", headers=h).status_code == 200
    assert client.post("/api/v1/payroll/generate", headers=h, json={"period": "2031-01"}).status_code == 403
    assert _save(client, h, "overtimeRequests", {"id": f"DRO-{tag}", "employee_id": col.employee_no, "department": DEPT, "hours": 2, "status": "Approved"}) == 200
    assert _save(client, h, "employeeLoans", {"id": f"DRL-{tag}", "employee_id": col.employee_no, "department": DEPT, "amount": 500}) == 200
    assert _save(client, h, "tasks", {"id": f"DRT-{tag}", "title": "t", "assigned_to": col.id, "department": DEPT, "status": "todo"}) == 200
    assert client.get("/api/v1/app-data/records/jobRequisitions", headers=h).status_code == 200


def test_manager_handles_team_overtime_and_tasks_only(client, db, auth_headers):
    tag = uuid.uuid4().hex[:6]
    col = _colleague(client, db, auth_headers, tag)
    h = _login_with_role(client, db, auth_headers, _roles(client, auth_headers)["Manager"]["id"], tag)
    assert _save(client, h, "overtimeRequests", {"id": f"DRO-{tag}", "employee_id": col.employee_no, "department": DEPT, "hours": 1, "status": "Approved"}) == 200
    assert _save(client, h, "tasks", {"id": f"DRT-{tag}", "title": "t", "assigned_to": col.id, "department": DEPT, "status": "todo"}) == 200
    assert not _salary_visible(client, h, col.employee_no)
    assert client.get("/api/v1/app-data/records/employeeLoans", headers=h).status_code == 403


def test_built_in_roles_editable_except_employee_and_edits_stick(client, db, auth_headers):
    roles = _roles(client, auth_headers)
    hr = roles["HR Manager"]
    original = hr["permissions"]
    trimmed = [k for k in original if k != "recruitment:delete"]
    try:
        r = client.put(f"/api/v1/hr/admin/roles/{hr['id']}", headers=auth_headers, json={
            "role_name": "Renamed", "description": "edited", "permission_keys": trimmed, "department_scope": []})
        assert r.status_code == 200, r.text
        assert r.json()["role_name"] == "HR Manager" and r.json()["is_system_role"] is True
        # not re-added by later requests or the startup upgrade
        backfill_default_role_permissions(db)
        after = _roles(client, auth_headers)["HR Manager"]
        assert "recruitment:delete" not in after["permissions"] and after["description"] == "edited"

        emp = roles["Employee"]
        r = client.put(f"/api/v1/hr/admin/roles/{emp['id']}", headers=auth_headers, json={
            "role_name": "Employee", "description": "x", "permission_keys": emp["permissions"], "department_scope": []})
        assert r.status_code == 400
        assert client.delete(f"/api/v1/hr/admin/roles/{hr['id']}", headers=auth_headers).status_code == 400
    finally:
        client.put(f"/api/v1/hr/admin/roles/{hr['id']}", headers=auth_headers, json={
            "role_name": "HR Manager", "description": hr["description"], "permission_keys": original, "department_scope": hr["department_scope"] or []})


def test_startup_upgrade_adds_new_defaults_once(client, db, auth_headers):
    roles = _roles(client, auth_headers)
    po = roles["Payroll Officer"]
    try:
        # an older install: Payroll Officer without employees:view
        client.put(f"/api/v1/hr/admin/roles/{po['id']}", headers=auth_headers, json={
            "role_name": "Payroll Officer", "description": po["description"],
            "permission_keys": [k for k in po["permissions"] if k != "employees:view"], "department_scope": []})
        db.execute(text("DELETE FROM schema_flags WHERE name = :n"), {"n": _DEFAULT_ROLES_FLAG})
        db.commit()
        backfill_default_role_permissions(db)
        assert "employees:view" in _roles(client, auth_headers)["Payroll Officer"]["permissions"]
        assert db.execute(text("SELECT 1 FROM schema_flags WHERE name = :n"), {"n": _DEFAULT_ROLES_FLAG}).first()
        assert db.query(Role).filter(Role.role_name == "Payroll Officer", Role.is_system_role.is_(True)).count() >= 1
    finally:
        client.put(f"/api/v1/hr/admin/roles/{po['id']}", headers=auth_headers, json={
            "role_name": "Payroll Officer", "description": po["description"], "permission_keys": po["permissions"], "department_scope": []})
