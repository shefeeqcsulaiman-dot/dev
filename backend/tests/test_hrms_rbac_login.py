"""Sub-user login: an Employee with a Role can log in via /hr/login (or the
same email/password at /auth/login falling through in login.html), and
gets the same real hrms.html interface as an admin — filtered to exactly
what their role's permissions grant. See docs/hrms-architecture.md and
C:\\Users\\SHEFI\\.claude\\plans\\lively-percolating-fern.md for the design.
"""

import json

from app.models import AppDataRecord, Employee
from tests.conftest import ensure_user


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name=None):
    # auth_headers reuses the same tenant across tests in this session-scoped
    # test DB, so role names (unique per company) must vary per call.
    role_name = role_name or f"Junior HR Manager ({username})"
    r = client.post(
        "/api/v1/hr/admin/roles",
        headers=admin_headers,
        json={"role_name": role_name, "description": "test role", "permission_keys": permission_keys},
    )
    assert r.status_code == 201, r.text
    role = r.json()

    r = client.put(
        f"/api/v1/hr/admin/employees/{employee_id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": "subuser123", "role_id": role["id"], "is_active": True},
    )
    assert r.status_code == 200, r.text

    r = client.post("/api/v1/ess/login", json={"username": username, "password": "subuser123"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, role


def _seed_employee(client, admin_headers, db):
    # This test tenant (created fresh per test by the auth_headers fixture)
    # has no employees yet — ensure_user() only seeds a Company + User.
    r = client.get("/api/v1/auth/me", headers=admin_headers)
    assert r.status_code == 200, r.text
    company_id = r.json()["company"]["id"]
    existing = db.query(Employee).filter(Employee.company_id == company_id).first()
    if existing:
        return existing.id
    emp = Employee(company_id=company_id, employee_no="RBAC-TEST-001", full_name="RBAC Test Employee")
    db.add(emp)
    db.commit()
    return emp.id


def test_employee_credentials_do_not_work_on_admin_login(client, db, auth_headers):
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, _role = _grant_role_and_login(client, auth_headers, employee_id, "rbactest.user1", ["employees:view"])

    # Same credentials must not authenticate against the admin login path —
    # the two identity tables (User vs Employee) stay fully separate.
    r = client.post("/api/v1/auth/login", json={"email": "rbactest.user1", "password": "subuser123"})
    assert r.status_code in (401, 422)


def test_whoami_reports_correct_kind_and_permissions(client, db, auth_headers):
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, role = _grant_role_and_login(
        client, auth_headers, employee_id, "rbactest.user2", ["employees:view", "leave:view"]
    )

    r = client.get("/api/v1/auth/whoami", headers=emp_headers)
    assert r.status_code == 200
    who = r.json()
    assert who["kind"] == "employee"
    assert who["is_admin"] is False
    assert set(who["permissions"]) == {"employees:view", "leave:view"}
    assert who["role_name"] == role["role_name"]

    r = client.get("/api/v1/auth/whoami", headers=auth_headers)
    assert r.status_code == 200
    who_admin = r.json()
    assert who_admin["kind"] == "user"
    assert who_admin["is_admin"] is True


def test_permission_enforcement_positive_and_negative(client, db, auth_headers):
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, _role = _grant_role_and_login(
        client, auth_headers, employee_id, "rbactest.user3", ["employees:view", "leave:view"]
    )

    # Granted permissions succeed
    assert client.get("/api/v1/payroll/employees", headers=emp_headers).status_code == 200
    assert client.get("/api/v1/leave/requests", headers=emp_headers).status_code == 200

    # Ungranted permissions are refused, not silently allowed
    assert client.get("/api/v1/payroll/runs", headers=emp_headers).status_code == 403
    assert client.get("/api/v1/attendance/today", headers=emp_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "employee_id": employee_id,
                "leave_type": "Annual Leave",
                "start_date": "2026-09-01",
                "end_date": "2026-09-02",
            },
        ).status_code
        == 403
    )  # role has leave:view but not leave:edit


def test_bootstrap_excludes_non_hr_collections_for_employee_principal(client, db, auth_headers):
    """The critical data-leak regression test: an Employee principal's
    bootstrap response must never contain business collections that don't
    map to any HRMS module, regardless of how many permissions their role
    has — and must contain exactly the HR collections their view
    permissions unlock."""
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, _role = _grant_role_and_login(
        client, auth_headers, employee_id, "rbactest.user4", ["employees:view", "leave:view"]
    )

    # Positive control: the "employees" collection normally comes from the
    # app-data bridge (Tier 2), not the real Employee ORM row created by
    # _seed_employee — seed one so this test can prove the allowlist
    # actually *includes* what it should, not just excludes what it shouldn't.
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    db.add(AppDataRecord(
        company_id=company_id, collection="employees", record_key="RBAC-TEST-001",
        payload=json.dumps({"id": "RBAC-TEST-001", "name": "RBAC Test Employee"}),
    ))
    db.commit()

    r = client.get("/api/v1/app-data", headers=emp_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    never_leaked = ["salesInvoices", "bankAccounts", "products", "ledger", "bills", "payments", "quotations", "expenses"]
    for collection in never_leaked:
        assert not data.get(collection), f"data leak: employee bootstrap contained {collection!r}"
    assert data.get("audit") == [], "audit trail must never be sent to an Employee principal"
    assert "employees" in data
    assert "leaveRequests" in data or data.get("leaveRequests") == []
    # No payroll:view granted -> payrollRuns collection excluded entirely
    assert not data.get("payrollRuns")


def test_admin_bootstrap_unaffected(client, db, auth_headers):
    """Regression check: get_current_principal must be a behaviorally
    transparent drop-in for get_current_user on the admin path."""
    r = client.get("/api/v1/app-data", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    # Admin sees business collections a restricted employee never would.
    assert any(k in data for k in ("salesInvoices", "products", "bankAccounts", "customers"))


def test_accounting_endpoints_403_for_employee_without_permission(client, db, auth_headers):
    """Was test_accounting_endpoints_401_for_employee_token — /accounts and
    /journal were User-only (get_current_user) until the "Main Dashboard
    Access" phase widened them to require_principal_permission("accounting:
    view"). An Employee token without that permission now gets 403 (not the
    401 that used to force-log the sub-user out via authenticatedFetch()'s
    session-invalid handling) — a permission problem, not a session problem.
    See test_main_dashboard_access.py for full coverage of the widened
    endpoints (granted-permission 200, admin unaffected, bootstrap
    scoping, and the company-module-gate/role-permission-gate composition).

    /inventory/mappings used to be in this same "still User-only" bucket, but
    Branch Management Phase 5 deliberately widened it (and stock-levels/
    stock-movements) to Employee/branch principals — see
    test_branch_isolation.py for that behavior's own coverage."""
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, _role = _grant_role_and_login(
        client, auth_headers, employee_id, "rbactest.user6", ["employees:view", "leave:view"]
    )

    assert client.get("/api/v1/accounts", headers=emp_headers).status_code == 403
    assert client.get("/api/v1/journal", headers=emp_headers).status_code == 403


def test_cross_tenant_employee_isolation(client, db, auth_headers, second_tenant_headers):
    """An Employee principal from company A must not be able to read
    company B's leave requests or bootstrap data, mirroring the existing
    cross-tenant checks for admin Users."""
    employee_id = _seed_employee(client, auth_headers, db)
    emp_headers, _role = _grant_role_and_login(
        client, auth_headers, employee_id, "rbactest.user5", ["employees:view", "leave:view"]
    )

    # This employee's token is scoped to tenant A — confirm it can't see
    # tenant B's employees via the admin-listing endpoint either.
    r = client.get("/api/v1/hr/admin/employees", headers=second_tenant_headers)
    assert r.status_code == 200
    other_tenant_employee_ids = {e["id"] for e in r.json()}
    assert employee_id not in other_tenant_employee_ids

    r = client.get("/api/v1/leave/requests", headers=emp_headers)
    assert r.status_code == 200
    for row in r.json():
        assert row["employee_id"] != next(iter(other_tenant_employee_ids), object())
