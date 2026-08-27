"""GET /reports/summary (and /reports/debug/purchase) backs almost every
report in the Reports module sidebar (P&L, Balance Sheet, GL, Ledgers,
Aging, VAT, Inventory, Bank Reconciliation, Fixed Assets, every BI/
Compliance report) with zero branch filtering — _build_summary() has no
branch_id parameter at all. "reports" is in BRANCH_ELIGIBLE_MODULES, so a
company could already grant a Branch Login access to this module and it
would see the ENTIRE company's financials, not just its own branch. This
covers the fix: blocked for a principal actually tied to one branch,
untouched for everyone else (admin, and an ordinary company-wide Employee
with no branch assignment)."""
from app.models import Employee, Role, RolePermission, Permission


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _create_branch_with_login(client, admin_headers, name, username, modules=None):
    payload = {"name": name, "username": username, "password": "branchlogin123"}
    if modules is not None:
        payload["modules_enabled"] = modules
    r = client.post("/api/v1/branches", headers=admin_headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _branch_login(client, username, password="branchlogin123"):
    r = client.post("/api/v1/branches/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _employee_with_reports_permission(client, db, admin_headers, employee_no):
    company_id = _company_id(client, admin_headers)
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name="Reports Test Employee")
    db.add(emp)
    db.flush()
    role = Role(company_id=company_id, role_name=f"Reports Viewer {employee_no}")
    db.add(role)
    db.flush()
    perm = db.query(Permission).filter(Permission.module == "reports", Permission.permission_name == "view").first()
    if not perm:
        perm = Permission(module="reports", permission_name="view")
        db.add(perm)
        db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    emp.role_id = role.id
    db.commit()
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers, json={
        "username": employee_no.lower(), "password": "employeepass123",
    })
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/hr/login", json={"username": employee_no.lower(), "password": "employeepass123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_branch_login_blocked_from_company_wide_reports(client, auth_headers):
    branch = _create_branch_with_login(
        client, auth_headers, "Reports Isolation Branch", "reports-isolation-branch-login",
        modules=["reports"],
    )
    branch_headers = _branch_login(client, branch["username"])

    resp = client.get("/api/v1/reports/summary", headers=branch_headers)
    assert resp.status_code == 403, resp.text

    debug_resp = client.get("/api/v1/reports/debug/purchase", headers=branch_headers)
    assert debug_resp.status_code == 403, debug_resp.text


def test_admin_still_sees_full_reports(client, auth_headers):
    resp = client.get("/api/v1/reports/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text


def test_company_wide_employee_not_wrongly_blocked(client, db, auth_headers):
    """The guard is gated on principal.branch_id being SET, not just
    can_cross_branch() alone — an ordinary Employee sub-user with reports
    access but no branch assignment (branch_id=None) must keep working
    exactly as before this fix."""
    headers = _employee_with_reports_permission(client, db, auth_headers, "REP-ISO-001")
    resp = client.get("/api/v1/reports/summary", headers=headers)
    assert resp.status_code == 200, resp.text


def test_branch_login_dashboard_and_trial_balance_still_work(client, auth_headers):
    """The two genuinely branch-aware endpoints must stay reachable — this
    fix should only block the unscoped bundle, not branch-scoped reporting
    that already exists."""
    branch = _create_branch_with_login(
        client, auth_headers, "Reports Isolation Branch 2", "reports-isolation-branch-login-2",
        modules=["reports"],
    )
    branch_headers = _branch_login(client, branch["username"])

    dash = client.get("/api/v1/reports/dashboard", headers=branch_headers)
    assert dash.status_code == 200, dash.text
    tb = client.get("/api/v1/reports/trial-balance", headers=branch_headers)
    assert tb.status_code == 200, tb.text
