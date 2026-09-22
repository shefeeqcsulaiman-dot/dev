"""The built-in "Manager" role (hr_access.py::_DEFAULT_ROLES / _ensure_default_roles)
was never department-scoped -- every company's Manager role had department_scope=NULL,
so its holders saw every department's employees/leave/attendance/rota company-wide
regardless of their own department. Now defaults to "@own" (the existing "Only the
employee's own department" marker) for brand-new companies, and existing companies get
a one-time startup backfill (see ensure_schema_updates() in main.py).

Each test gets its own company (via _make_restricted_company, unrestricted modules) --
the shared auth_headers company is used by every other test file in the suite, and
several tests here deliberately mutate the Manager role's department_scope, which must
never leak into (or be polluted by) anything else running in the same session."""
import json

from sqlalchemy import text

from app.main import ensure_schema_updates
from app.models import Employee, Role
from tests.test_module_permissions import _make_restricted_company


def _co(client, db, tag):
    company_id, headers, _sa = _make_restricted_company(client, db, tag, None)  # None -> unrestricted (all modules)
    return company_id, headers


def _seed_default_roles(client, headers):
    r = client.get("/api/v1/hr/admin/roles", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _manager_role(db, company_id):
    return db.query(Role).filter(Role.company_id == company_id, Role.role_name == "Manager").one()


def _mk_employee(client, headers, db, emp_no, dept):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"}, json={
        "collection": "employees",
        "record": {"id": emp_no, "name": f"{emp_no} Person", "department": dept, "designation": "Staff", "status": "Active", "salary": 4000},
    })
    assert r.status_code == 200, r.text
    return db.query(Employee).filter(Employee.employee_no == emp_no).one()


def test_new_companys_manager_role_defaults_to_own_department(client, db):
    cid, headers = _co(client, db, "mgr-new")
    roles = _seed_default_roles(client, headers)
    assert any(r["role_name"] == "Manager" for r in roles)
    row = _manager_role(db, cid)
    assert json.loads(row.department_scope) == ["@own"]
    # every other preset stays company-wide, unchanged
    for name in ("Administrator", "HR Manager", "Payroll Officer", "Employee"):
        other = db.query(Role).filter(Role.company_id == cid, Role.role_name == name).one()
        assert not other.department_scope, name


def test_seeding_is_idempotent_and_does_not_reset_a_deliberately_reopened_manager_role(client, db):
    cid, headers = _co(client, db, "mgr-idem")
    _seed_default_roles(client, headers)
    row = _manager_role(db, cid)
    row.department_scope = None  # admin deliberately opened it back up company-wide
    db.commit()
    _seed_default_roles(client, headers)  # re-seed must not touch an already-existing role
    db.refresh(row)
    assert row.department_scope is None


def test_manager_login_with_own_department_only_sees_own_department(client, db):
    cid, headers = _co(client, db, "mgr-login")
    _seed_default_roles(client, headers)
    manager_role = _manager_role(db, cid)
    a = _mk_employee(client, headers, db, "MGR-A1", "DeptA")
    b = _mk_employee(client, headers, db, "MGR-B1", "DeptB")
    mgr = _mk_employee(client, headers, db, "MGR-VIEWER", "DeptA")
    r = client.put(f"/api/v1/hr/admin/employees/{mgr.id}/portal-access", headers=headers,
                   json={"username": f"mgr.viewer.{cid[:6]}", "password": "manager12345", "role_id": manager_role.id, "is_active": True})
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": f"mgr.viewer.{cid[:6]}", "password": "manager12345"})
    assert login.status_code == 200, login.text
    mgr_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    body = client.get("/api/v1/app-data/records/employees", headers=mgr_headers, params={"limit": 500}).json()
    ids = {r["id"] for r in body["records"]}
    assert a.employee_no in ids
    assert b.employee_no not in ids


def test_startup_backfill_scopes_existing_manager_roles_once(client, db):
    cid, headers = _co(client, db, "mgr-backfill")
    _seed_default_roles(client, headers)  # creates the role with department_scope already set
    row = _manager_role(db, cid)
    # simulate an "old" company: Manager role predates this fix, department_scope NULL
    row.department_scope = None
    db.commit()
    db.execute(text("DELETE FROM schema_flags WHERE name = 'manager_role_own_department_backfill'"))
    db.commit()

    ensure_schema_updates()
    db.refresh(row)
    assert json.loads(row.department_scope) == ["@own"]

    # a super admin/company admin deliberately reopens it after the backfill ran once
    row.department_scope = None
    db.commit()
    ensure_schema_updates()  # must NOT re-backfill (marker already recorded)
    db.refresh(row)
    assert row.department_scope is None


def test_startup_backfill_never_touches_a_custom_role_also_named_manager(client, db):
    cid, headers = _co(client, db, "mgr-custom")
    custom = Role(company_id=cid, role_name="Manager", is_system_role=False, department_scope=None)
    db.add(custom)
    db.commit()
    db.execute(text("DELETE FROM schema_flags WHERE name = 'manager_role_own_department_backfill'"))
    db.commit()

    ensure_schema_updates()
    db.refresh(custom)
    assert custom.department_scope is None
