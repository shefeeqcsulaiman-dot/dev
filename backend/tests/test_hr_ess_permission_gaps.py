"""HRMS/ESS permission gaps found in the 2026-10-15 review:

1. An employee with no password set used to sign in with their employee_no as the password.
2. An employee deactivated in HRMS (status "inactive") kept their ESS/HRMS sign-in and tokens.
3. /app-data/records/rotaAssignments/range and /by-ids skipped the Rota role check.
4. A department-scoped HR editor could re-scope or delete another department's role.
5. A role with only "Employees: view" could move an employee to another department."""
import json
import uuid

from app.models import Employee, Role
from tests.test_ess_requests import _company_id, _ess_login


def _uid():
    return uuid.uuid4().hex[:6]


def _employee(db, cid, no, dept="Ops"):
    emp = Employee(company_id=cid, employee_no=no, full_name=f"{no} Staff", basic_salary=5000, status="active", department=dept)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def test_employee_without_password_cannot_sign_in_with_employee_no(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    no = f"NOPW-{_uid()}"
    _employee(db, cid, no)
    for url in ("/api/v1/ess/login", "/api/v1/hr/login"):
        r = client.post(url, json={"username": no, "password": no, "company_id": cid})
        assert r.status_code == 401, (url, r.text)


def test_once_hr_sets_a_password_the_employee_can_sign_in(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, f"PW-{_uid()}", f"pw.{_uid()}")
    assert client.get("/api/v1/ess/me", headers=h).status_code == 200


def test_deactivated_employee_loses_sign_in_and_existing_tokens(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    username = f"inact.{_uid()}"
    emp, ess_h = _ess_login(client, db, auth_headers, cid, f"INACT-{_uid()}", username)
    hr = client.post("/api/v1/hr/login", json={"username": username, "password": f"{username}pw123"})
    assert hr.status_code == 200, hr.text
    hr_h = {"Authorization": f"Bearer {hr.json()['access_token']}"}

    emp.status = "inactive"   # what HRMS "Deactivate" stores on the SQL row
    db.commit()

    assert client.get("/api/v1/ess/me", headers=ess_h).status_code == 403
    assert client.get("/api/v1/hr/me", headers=hr_h).status_code == 403
    for url in ("/api/v1/ess/login", "/api/v1/hr/login"):
        r = client.post(url, json={"username": username, "password": f"{username}pw123"})
        assert r.status_code == 403, (url, r.text)

    emp.status = "active"     # reactivated: works again
    db.commit()
    assert client.get("/api/v1/ess/me", headers=ess_h).status_code == 200


def _save_shift(client, headers, emp_no, day):
    rec = {"id": f"{emp_no}-{day}", "employee_id": emp_no, "employee_name": "Someone", "date": day,
           "type": "Morning", "code": "M", "mark": "Shift", "status": "Published"}
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "rotaAssignments", "record": rec})
    assert r.status_code == 200, r.text
    return rec["id"]


def _role_holder(client, db, auth_headers, cid, perms, scope, dept):
    r = client.post("/api/v1/hr/admin/roles", headers=auth_headers,
                    json={"role_name": f"R-{_uid()}", "permission_keys": perms, "department_scope": scope})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    no = f"RH-{_uid()}"
    emp, h = _ess_login(client, db, auth_headers, cid, no, f"{no.lower()}.u")
    emp.department = dept
    db.commit()
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers, json={"role_id": role_id})
    assert r.status_code == 200, r.text
    return emp, h, role_id


def test_rota_range_and_by_ids_need_rota_access(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    other = f"ROTA-{_uid()}"
    shift_id = _save_shift(client, auth_headers, other, "2031-03-03")
    params = {"from": "2031-03-01", "to": "2031-03-31"}

    _plain, plain_h = _ess_login(client, db, auth_headers, cid, f"PLAIN-{_uid()}", f"plain.{_uid()}")
    assert client.get("/api/v1/app-data/records/rotaAssignments/range", headers=plain_h, params=params).status_code == 403
    assert client.get("/api/v1/app-data/records/rotaAssignments/by-ids", headers=plain_h, params={"ids": shift_id}).status_code == 403

    _r, rota_h, _ = _role_holder(client, db, auth_headers, cid, ["rota:view"], [], "Ops")
    r = client.get("/api/v1/app-data/records/rotaAssignments/range", headers=rota_h, params=params)
    assert r.status_code == 200 and any(x["employee_id"] == other for x in r.json()["records"])
    r = client.get("/api/v1/app-data/records/rotaAssignments/by-ids", headers=rota_h, params={"ids": shift_id})
    assert r.status_code == 200 and [x["id"] for x in r.json()["records"]] == [shift_id]


_HR_EDITOR = ["hr_settings:view", "hr_settings:edit", "hr_settings:delete", "employees:view"]


def test_scoped_hr_editor_cannot_rescope_or_delete_another_departments_role(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _w, _wh, warehouse_role = _role_holder(client, db, auth_headers, cid, _HR_EDITOR, ["Warehouse"], "Warehouse")
    _s, sales_h, _ = _role_holder(client, db, auth_headers, cid, _HR_EDITOR, ["Sales"], "Sales")

    r = client.put(f"/api/v1/hr/admin/roles/{warehouse_role}", headers=sales_h,
                   json={"role_name": "Hijacked", "permission_keys": _HR_EDITOR, "department_scope": ["Sales"]})
    assert r.status_code == 403, r.text
    db.expire_all()
    assert json.loads(db.get(Role, warehouse_role).department_scope) == ["Warehouse"]

    # an unassigned role of another department can't be deleted either
    r = client.post("/api/v1/hr/admin/roles", headers=auth_headers,
                    json={"role_name": f"WH-spare-{_uid()}", "permission_keys": ["employees:view"], "department_scope": ["Warehouse"]})
    spare = r.json()["id"]
    assert client.delete(f"/api/v1/hr/admin/roles/{spare}", headers=sales_h).status_code == 403
    assert db.get(Role, spare) is not None


def test_scoped_hr_editor_can_still_manage_its_own_departments_roles(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _s, sales_h, _ = _role_holder(client, db, auth_headers, cid, _HR_EDITOR, ["Sales"], "Sales")
    r = client.post("/api/v1/hr/admin/roles", headers=sales_h,
                    json={"role_name": f"Sales-staff-{_uid()}", "permission_keys": ["employees:view"], "department_scope": ["Sales"]})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    r = client.put(f"/api/v1/hr/admin/roles/{role_id}", headers=sales_h,
                   json={"role_name": r.json()["role_name"], "description": "edited", "permission_keys": ["employees:view"], "department_scope": ["Sales"]})
    assert r.status_code == 200, r.text
    assert client.delete(f"/api/v1/hr/admin/roles/{role_id}", headers=sales_h).status_code == 204


def _save_employee(client, headers, rec):
    return client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "employees", "record": rec})


def test_department_change_needs_employees_edit(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    no = f"DEPT-{_uid()}"
    rec = {"id": no, "employee_no": no, "name": "Dept Target", "department": "Ops", "designation": "Clerk", "status": "Active"}
    assert _save_employee(client, auth_headers, rec).status_code == 200

    _v, view_h, _ = _role_holder(client, db, auth_headers, cid, ["employees:view"], [], "Ops")
    r = _save_employee(client, view_h, {**rec, "department": "Finance", "designation": "Senior Clerk"})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).one()
    assert row.department == "Ops"              # department kept
    assert row.designation == "Senior Clerk"    # ordinary profile field still editable

    _e, edit_h, _ = _role_holder(client, db, auth_headers, cid, ["employees:view", "employees:edit"], [], "Ops")
    assert _save_employee(client, edit_h, {**rec, "department": "Finance"}).status_code == 200
    db.expire_all()
    assert db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).one().department == "Finance"
