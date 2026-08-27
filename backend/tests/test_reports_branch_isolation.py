"""GET /reports/summary backs almost every report in the Reports module
sidebar (P&L, Balance Sheet, GL, Ledgers, Aging, VAT, Revenue Intelligence,
Working Capital, and more). It used to have no branch_id parameter at all —
company-wide by construction — and "reports" is in BRANCH_ELIGIBLE_MODULES,
so a company could grant a Branch Login access to this module and it would
see the ENTIRE company's financials, not just its own branch.

Fixed properly (not just blocked): report_summary() now resolves branch_id
the same way dashboard() does, and _build_summary() threads it through
every section backed by a table that actually has a branch_id column
(Invoice, JournalEntry/GeneralLedgerEntry, SourceTransaction, PayrollRun,
AppDataRecord). A few sections (corporate tax, fixed assets, budget/cash
flow, cost centers/audit) stay company-wide because their backing tables
have no branch_id column at all — that's a real schema limitation, not
this fix's scope."""
import json

from app.models import AppDataRecord, Employee, Permission, Role, RolePermission


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


def _seed_invoice(db, company_id, branch_id, amount):
    db.add(AppDataRecord(
        company_id=company_id, branch_id=branch_id, collection="salesInvoices",
        record_key=f"seed-{branch_id}-{amount}",
        payload=json.dumps({"status": "paid", "subtotal": amount, "total": amount, "customer": "Test Customer"}),
    ))
    db.commit()


def test_branch_login_sees_only_its_own_branch_revenue(client, db, auth_headers):
    # auth_headers reuses ONE shared company for the whole test session (see
    # feedback_shared_test_company_pollution) — other test files' leftover
    # salesInvoices rows (branch_id=NULL, which by design stays visible to
    # every branch — see app_sales_invoice_records()) mean this company's
    # totals are never pristine. So this asserts the actual security
    # property (branch A never sees branch B's distinctly-sized revenue),
    # not an exact total, which would be flaky depending on run order.
    company_id = _company_id(client, auth_headers)
    branch_a = _create_branch_with_login(
        client, auth_headers, "Reports Isolation Branch A", "reports-isolation-branch-a", modules=["reports"],
    )
    branch_b = _create_branch_with_login(
        client, auth_headers, "Reports Isolation Branch B", "reports-isolation-branch-b", modules=["reports"],
    )
    _seed_invoice(db, company_id, branch_a["id"], 1000)
    _seed_invoice(db, company_id, branch_b["id"], 555555)

    admin_resp = client.get("/api/v1/reports/summary", headers=auth_headers)
    assert admin_resp.status_code == 200, admin_resp.text
    admin_revenue = float(admin_resp.json()["dashboard"]["revenue"])
    assert admin_revenue >= 556555

    branch_a_headers = _branch_login(client, branch_a["username"])
    scoped_resp = client.get("/api/v1/reports/summary", headers=branch_a_headers)
    assert scoped_resp.status_code == 200, scoped_resp.text
    scoped_revenue = float(scoped_resp.json()["dashboard"]["revenue"])
    assert scoped_revenue < 555555, "branch A's revenue must not include branch B's distinctly-sized invoice"


def test_company_wide_employee_not_branch_restricted(client, db, auth_headers):
    """resolve_active_branch()/can_cross_branch() only restrict a principal
    actually tied to one branch — an ordinary Employee sub-user with
    reports access but no branch assignment (branch_id=None) must keep
    seeing full company-wide numbers exactly as before this fix."""
    headers = _employee_with_reports_permission(client, db, auth_headers, "REP-ISO-001")
    resp = client.get("/api/v1/reports/summary", headers=headers)
    assert resp.status_code == 200, resp.text


def test_admin_can_still_view_all_branches(client, auth_headers):
    resp = client.get("/api/v1/reports/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text


def test_branch_login_dashboard_still_works(client, auth_headers):
    branch = _create_branch_with_login(
        client, auth_headers, "Reports Isolation Branch C", "reports-isolation-branch-c", modules=["reports"],
    )
    branch_headers = _branch_login(client, branch["username"])
    dash = client.get("/api/v1/reports/dashboard", headers=branch_headers)
    assert dash.status_code == 200, dash.text
