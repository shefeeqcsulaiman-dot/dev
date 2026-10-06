"""Main Dashboard Access for Branch Employees (Phase A of the "Main Dashboard
Access" plan, a follow-on to Branch Management).

A1: new non-HR module keys in _PERMISSION_CATALOG (sales, quotations, pos,
purchase, inventory, expense, bank, accounting, corporate, notifications,
expert) plus the corresponding entries in app_data.py's collections-by-
module map, so a Role can be granted view access to main-dashboard data —
previously impossible, that catalog was exhaustively HR-only.
A2: accounting.py/corporate_accounting.py/tax.py/reports.py GET (read-only)
endpoints widened from admin-only (get_current_user) to
require_principal_permission("module:view") — an Employee with the right
permission gets 200, one without gets 403 (not the 401 that used to force
them out of their whole session — see authenticatedFetch()'s 401-vs-403
handling in app.js), an admin is completely unaffected either way. Write
endpoints in these files are untouched, a deliberately separate later phase.
"""

from app.models import Company, Employee, User
from app.security import hash_password


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
    # Mirrors test_branch_isolation.py's helper of the same name — copied
    # rather than shared, per this test suite's existing per-file convention
    # (test_branch_isolation.py, test_branch_write_access.py both do this).
    existing = client.get("/api/v1/hr/admin/roles", headers=admin_headers).json()
    role = next((r for r in existing if r["role_name"] == role_name), None)
    if not role:
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
        json={"username": username, "password": "dashboardtest123", "role_id": role["id"], "is_active": True},
    )
    assert r.status_code == 200, r.text

    r = client.post("/api/v1/ess/login", json={"username": username, "password": "dashboardtest123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _new_employee(db, company_id, employee_no):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff")
    db.add(emp)
    db.commit()
    return emp


def test_permission_catalog_includes_main_dashboard_modules(client, auth_headers):
    listed = client.get("/api/v1/hr/admin/permissions", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    keys = {row["key"] for row in listed.json()}
    for expected in (
        "sales:view", "quotations:view", "pos:view", "purchase:view", "inventory:view",
        "expense:view", "bank:view", "accounting:view", "corporate:view",
        "notifications:view", "expert:view",
    ):
        assert expected in keys, f"{expected} missing from permission catalog"


def test_accounting_view_permission_grants_read_access(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-ACC-001")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.accview", ["accounting:view"], "Accounting Viewer")

    for path in ("/api/v1/accounts", "/api/v1/accounts/tree", "/api/v1/journal", "/api/v1/voucher-types",
                 "/api/v1/vouchers", "/api/v1/general-ledger", "/api/v1/vendors", "/api/v1/posting-jobs",
                 "/api/v1/payments", "/api/v1/receipts", "/api/v1/bank-accounts"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text}"

    # Admin is completely unaffected by the widening.
    r = client.get("/api/v1/accounts", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_accounting_endpoints_403_without_permission(client, db, auth_headers):
    # This is the regression that matters most: before this phase, an
    # Employee hitting these endpoints got a 401, which the frontend's
    # global session handler treats as "session invalid" and force-logs-out.
    # A permission-less Employee must now get 403 ("valid session, not
    # permitted") and stay logged in.
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-ACC-002")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.noaccview", ["employees:view"], "No Accounting Role")

    for path in ("/api/v1/accounts", "/api/v1/journal", "/api/v1/vouchers", "/api/v1/bank-accounts"):
        r = client.get(path, headers=headers)
        assert r.status_code == 403, f"{path} -> expected 403, got {r.status_code}: {r.text}"


def test_corporate_accounting_view_permission_grants_read_access(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-CORP-001")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.corpview", ["corporate:view"], "Corporate Viewer")

    for path in ("/api/v1/corporate-accounting/summary", "/api/v1/corporate-accounting/corporate-tax",
                 "/api/v1/corporate-accounting/fixed-assets", "/api/v1/corporate-accounting/budgets",
                 "/api/v1/corporate-accounting/approval-matrix"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text}"


def test_corporate_accounting_403_without_permission(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-CORP-002")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.nocorpview", ["employees:view"], "No Corporate Role")

    r = client.get("/api/v1/corporate-accounting/summary", headers=headers)
    assert r.status_code == 403, r.text


def test_tax_endpoints_view_permission(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-TAX-001")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.taxview", ["accounting:view", "corporate:view"], "Tax Viewer")

    for path in ("/api/v1/tax/codes", "/api/v1/tax/lines", "/api/v1/tax/vat-return", "/api/v1/tax/vat-returns"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text}"

    r = client.get("/api/v1/tax/corporate-tax-returns", headers=headers)
    assert r.status_code == 200, r.text


def test_tax_endpoints_403_without_permission(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-TAX-002")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.notaxview", ["employees:view"], "No Tax Role")

    r = client.get("/api/v1/tax/codes", headers=headers)
    assert r.status_code == 403, r.text
    r = client.get("/api/v1/tax/corporate-tax-returns", headers=headers)
    assert r.status_code == 403, r.text


def test_reports_dashboard_and_summary_view_permission(client, db, auth_headers):
    # Company-wide/unfiltered, same numbers an admin sees — this phase only
    # changes WHO can view them, not what they contain (see reports.py's
    # comment on dashboard()/report_summary()).
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-REP-001")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.repview", ["reports:view"], "Reports Viewer")

    r = client.get("/api/v1/reports/dashboard", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/reports/summary", headers=headers)
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/reports/dashboard", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_reports_dashboard_403_without_permission(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-REP-002")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.noreport", ["employees:view"], "No Reports Role")

    r = client.get("/api/v1/reports/dashboard", headers=headers)
    assert r.status_code == 403, r.text
    r = client.get("/api/v1/reports/summary", headers=headers)
    assert r.status_code == 403, r.text


def test_bootstrap_scopes_collections_to_granted_module(client, db, auth_headers):
    # bootstrap()'s collections dict only gets a key for a collection that
    # both (a) the principal's role allows and (b) has at least one actual
    # AppDataRecord row — an allowed-but-empty collection simply isn't a key
    # at all, so seed one real record to make the presence assertion mean
    # something.
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "customers", "record": {"name": "Bootstrap Test Customer"}},
    )
    assert saved.status_code == 200, saved.text

    emp = _new_employee(db, _company_id(client, auth_headers), "MD-BOOT-001")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.bootsales", ["sales:view"], "Sales Only")

    r = client.get("/api/v1/app-data", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "customers" in data
    # Sales invoices themselves are paged from /app-data/sales-invoices, not bootstrapped.
    assert client.get("/api/v1/app-data/sales-invoices", headers=headers).status_code == 200
    # No purchase/accounting/HR-payroll data leaks through for a sales-only role.
    assert "bills" not in data
    assert "ledger" not in data
    assert "payrollRuns" not in data


def test_bootstrap_with_zero_relevant_permissions_returns_no_new_collections(client, db, auth_headers):
    emp = _new_employee(db, _company_id(client, auth_headers), "MD-BOOT-002")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.bootnone", ["attendance:view_own_attendance"], "Attendance Only")

    r = client.get("/api/v1/app-data", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    for collection in ("salesInvoices", "bills", "ledger", "corporateTax", "bankAccounts"):
        assert collection not in data, f"{collection} should not be visible with no relevant permission"


def test_bootstrap_hr_only_role_unaffected_by_new_collections(client, db, auth_headers):
    # Regression: an HR-scoped role's bootstrap behavior is unchanged by the
    # new non-HR entries added to the collections-by-module map.
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "employees", "record": {"id": "MD-BOOT-EMP-001", "name": "Bootstrap Test Employee", "department": "Operations"}},
    )
    assert saved.status_code == 200, saved.text

    emp = _new_employee(db, _company_id(client, auth_headers), "MD-BOOT-003")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.boothr", ["employees:view"], "Employees Only")

    r = client.get("/api/v1/app-data", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "employees" in data
    assert "salesInvoices" not in data
    assert "ledger" not in data


def test_bootstrap_hrms_scope_hides_non_hr_data_even_for_admin(client, db, auth_headers):
    # An admin User's bootstrap is normally unrestricted (allowed_collections
    # is None) -- ?scope=hrms is the one thing that narrows it even for an
    # admin, so hrms.html's hydrateFromServer() doesn't pay to fetch/parse/
    # render the company's full Sales/Purchase/Accounting/Corporate history
    # on every HRMS page load.
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "customers", "record": {"name": "Scope Test Customer"}},
    )
    assert saved.status_code == 200, saved.text
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "employees", "record": {"id": "HRMS-SCOPE-EMP-001", "name": "Scope Test Employee", "department": "Operations"}},
    )
    assert saved.status_code == 200, saved.text

    r = client.get("/api/v1/app-data?scope=hrms", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "employees" in data
    for collection in ("customers", "bills", "ledger", "products", "quotations", "vendors"):
        assert collection not in data, f"{collection} should not be visible under scope=hrms"

    # The unscoped call for the SAME admin must still see everything --
    # scope=hrms narrows, it never widens or leaks into the normal path.
    r_full = client.get("/api/v1/app-data", headers=auth_headers)
    assert r_full.status_code == 200, r_full.text
    assert "customers" in r_full.json()["data"]


def test_bootstrap_hrms_scope_intersects_with_employee_permissions(client, db, auth_headers):
    # For a non-admin Employee principal, scope=hrms must never WIDEN what
    # their role already permits -- it only ever narrows further.
    emp = _new_employee(db, _company_id(client, auth_headers), "HRMS-SCOPE-004")
    headers = _grant_role_and_login(client, auth_headers, emp.id, "md.hrmsscope", ["sales:view"], "Sales Scope Test")

    r = client.get("/api/v1/app-data?scope=hrms", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # sales:view isn't in the HRMS scope module list, so intersecting with
    # it leaves nothing from that role visible.
    assert "salesInvoices" not in data


def test_bootstrap_leaves_rota_assignments_to_the_range_endpoint(client, db, auth_headers):
    # rotaAssignments previously had no cap at all -- on one live account it
    # had grown to 2,508 rows and dominated hrms.html's bootstrap payload
    # almost by itself. Rota screens now load the dates they show from
    # /records/rotaAssignments/range, so the bootstrap doesn't carry them.
    import json as _json

    from app.models import AppDataRecord

    company_id = _company_id(client, auth_headers)
    for i in range(510):
        db.add(AppDataRecord(
            company_id=company_id, collection="rotaAssignments", record_key=f"RA-{i}",
            payload=_json.dumps({"id": f"RA-{i}", "employee_id": "x", "date": "2026-08-01"}),
        ))
    db.commit()

    r = client.get("/api/v1/app-data", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "rotaAssignments" not in data["data"]
    assert "rotaAssignments" not in data["truncated_collections"]


def test_company_module_gate_and_role_permission_gate_both_enforce(client, db):
    # Two independent gates: require_module("accounting") (company-wide,
    # superadmin-controlled) and accounting:view (per-role). A branch
    # employee granted accounting:view still gets 403 on an accounting.py
    # endpoint if the company itself doesn't have the accounting module
    # enabled — proves the two gates compose, neither one alone is
    # sufficient, matching require_module's existing documented behavior
    # (it already ran for admins too; this just confirms it keeps working
    # once the per-endpoint auth is widened to accept an Employee token).
    #
    # Company starts WITH accounting enabled — Branch Login Phase 1 added a
    # company->role inheritance check (POST/PUT /hr/admin/roles reject a
    # permission key for a module the company hasn't enabled), so the role
    # below can only be created while accounting is still on. The company
    # is then restricted to simulate superadmin disabling accounting
    # *after* the role already has accounting:view granted — a still-valid,
    # still-important scenario the inheritance check doesn't (and shouldn't)
    # retroactively touch existing roles for.
    company = Company(name="Gate Test Co", trn="MODPERM-GATE-001", modules_enabled='["reports", "hrms", "ess", "accounting"]')
    db.add(company)
    db.flush()
    admin = User(company_id=company.id, email="gate-admin@example.com", full_name="Gate Admin",
                 role="admin", password_hash=hash_password("test12345"))
    db.add(admin)
    db.commit()
    login = client.post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert login.status_code == 200, login.text
    admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    emp = _new_employee(db, company.id, "MD-GATE-001")
    headers = _grant_role_and_login(client, admin_headers, emp.id, "md.gate", ["accounting:view"], "Accounting Only Gate Test")

    company.modules_enabled = '["reports", "hrms", "ess"]'
    db.commit()

    r = client.get("/api/v1/accounts", headers=headers)
    assert r.status_code == 403, f"expected 403 (company module disabled) even with accounting:view granted, got {r.status_code}: {r.text}"


def _company_id(client, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    return r.json()["company"]["id"]
