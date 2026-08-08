"""Branch Login Phase 1: module inheritance tests.

Two independent-but-related gaps closed together:
1. Company -> Role inheritance: the HR Role/Permission catalog previously
   offered every module's permission keys regardless of whether the company
   itself had that module enabled (Company.modules_enabled) — an admin could
   grant "accounting:view" on a role even with Accounting off company-wide.
2. Branch module toggle: Branch.modules_enabled is a new, additional
   restriction layer on top of a branch-assigned Employee's own Role — a
   company can now also enable/disable modules per branch (always a subset
   of what the company itself has enabled), enforced both by require_module()
   (dependencies.py) for router-gated endpoints and by
   assert_collection_module_enabled() (app_data.py) for the /app-data write
   choke point.
"""

import json

from app.models import Company, Employee


def _set_company_modules(db, company_id, modules):
    company = db.query(Company).filter(Company.id == company_id).first()
    company.modules_enabled = json.dumps(modules) if modules is not None else None
    db.commit()


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
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
        json={"username": username, "password": "branchtest123", "role_id": role["id"], "is_active": True},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/ess/login", json={"username": username, "password": "branchtest123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── Company -> Role inheritance ─────────────────────────────────────────


def test_permissions_catalog_excludes_company_disabled_modules(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _set_company_modules(db, company_id, ["sales", "hrms"])
    try:
        catalog = client.get("/api/v1/hr/admin/permissions", headers=auth_headers).json()
        modules = {p["module"] for p in catalog}
        assert "sales" in modules
        # accounting has an ALL_MODULES entry and isn't in the enabled set.
        assert "accounting" not in modules
        # HR-suite keys (no ALL_MODULES entry, collectively gated by "hrms"
        # alone) must stay offered regardless of the Main-Dashboard list.
        assert "hr_settings" in modules
        assert "payroll" in modules
    finally:
        _set_company_modules(db, company_id, None)


def test_permissions_catalog_unrestricted_company_offers_everything(client, auth_headers):
    """auth_headers' tenant defaults to modules_enabled = NULL — regression
    guard that the new filter doesn't accidentally restrict the common case."""
    catalog = client.get("/api/v1/hr/admin/permissions", headers=auth_headers).json()
    modules = {p["module"] for p in catalog}
    assert "accounting" in modules
    assert "sales" in modules


def test_create_role_rejects_permission_for_company_disabled_module(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    # "hrms" must stay enabled — /hr/admin/roles itself sits behind
    # require_module("hrms") at the router level (gated_router), separate
    # from the per-key validation this test is actually exercising.
    _set_company_modules(db, company_id, ["sales", "hrms"])
    try:
        r = client.post(
            "/api/v1/hr/admin/roles",
            headers=auth_headers,
            json={"role_name": "Bad Role Attempt", "description": None, "permission_keys": ["accounting:view"]},
        )
        assert r.status_code == 400, r.text
        roles = client.get("/api/v1/hr/admin/roles", headers=auth_headers).json()
        assert not any(role["role_name"] == "Bad Role Attempt" for role in roles)
    finally:
        _set_company_modules(db, company_id, None)


def test_create_role_accepts_permission_for_company_enabled_module(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _set_company_modules(db, company_id, ["sales", "hrms"])
    try:
        r = client.post(
            "/api/v1/hr/admin/roles",
            headers=auth_headers,
            json={"role_name": "Good Role Attempt", "description": None, "permission_keys": ["sales:view", "hr_settings:view"]},
        )
        assert r.status_code == 201, r.text
        assert set(r.json()["permissions"]) == {"sales:view", "hr_settings:view"}
    finally:
        _set_company_modules(db, company_id, None)


def test_update_role_rejects_permission_for_company_disabled_module(client, db, auth_headers):
    r = client.post(
        "/api/v1/hr/admin/roles",
        headers=auth_headers,
        json={"role_name": "Update Target Role", "description": None, "permission_keys": ["sales:view"]},
    )
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]

    company_id = _company_id(client, auth_headers)
    _set_company_modules(db, company_id, ["sales", "hrms"])
    try:
        r = client.put(
            f"/api/v1/hr/admin/roles/{role_id}",
            headers=auth_headers,
            json={"role_name": "Update Target Role", "description": None, "permission_keys": ["accounting:view"]},
        )
        assert r.status_code == 400, r.text
        roles = client.get("/api/v1/hr/admin/roles", headers=auth_headers).json()
        updated = next(role for role in roles if role["id"] == role_id)
        assert updated["permissions"] == ["sales:view"]
    finally:
        _set_company_modules(db, company_id, None)


# ── Company -> Branch module inheritance ─────────────────────────────────


def test_branch_modules_enabled_rejects_company_disabled_module(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _set_company_modules(db, company_id, ["sales"])
    try:
        branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inherit Test Branch A"}).json()
        r = client.put(
            f"/api/v1/branches/{branch['id']}", headers=auth_headers,
            json={"modules_enabled": ["accounting"]},
        )
        assert r.status_code == 400, r.text
    finally:
        _set_company_modules(db, company_id, None)


def test_branch_modules_enabled_accepts_subset_of_company(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _set_company_modules(db, company_id, ["sales", "inventory"])
    try:
        branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inherit Test Branch B"}).json()
        r = client.put(
            f"/api/v1/branches/{branch['id']}", headers=auth_headers,
            json={"modules_enabled": ["sales"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["modules_enabled"] == ["sales"]
    finally:
        _set_company_modules(db, company_id, None)


def test_branch_modules_enabled_rejects_non_branch_eligible_module(client, auth_headers):
    """"hrms"/"ess"/"settings" have ALL_MODULES entries but are deliberately
    excluded from BRANCH_ELIGIBLE_MODULES — HR access stays governed purely
    by an Employee's own Role, never by which branch they're assigned to."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Ineligible Module Branch"}).json()
    r = client.put(
        f"/api/v1/branches/{branch['id']}", headers=auth_headers,
        json={"modules_enabled": ["hrms"]},
    )
    assert r.status_code == 400, r.text


def test_branch_create_persists_modules_enabled(client, auth_headers):
    r = client.post(
        "/api/v1/branches", headers=auth_headers,
        json={"name": "Create With Modules Branch", "modules_enabled": ["sales", "pos"]},
    )
    assert r.status_code == 201, r.text
    assert set(r.json()["modules_enabled"]) == {"sales", "pos"}


# ── Branch-level require_module()/assert_collection_module_enabled() enforcement ──


def test_employee_blocked_by_branch_module_toggle_on_gated_router(client, db, auth_headers):
    """require_module()'s branch-level check — inventory.py's router is
    gated by require_module("inventory"); a branch that has NOT enabled
    inventory blocks its own employees from it, even though the company
    allows it and the employee's Role would otherwise permit it."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Module Toggle Branch A"}).json()
    r = client.put(f"/api/v1/branches/{branch['id']}", headers=auth_headers, json={"modules_enabled": ["sales"]})
    assert r.status_code == 200, r.text

    company_id = _company_id(client, auth_headers)
    emp = Employee(company_id=company_id, employee_no="BR-MOD-A", full_name="Module Toggle Staff A", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "modtest.brancha", ["employees:view"], "Module Toggle Role A")

    r = client.get("/api/v1/inventory/stock-levels", headers=headers)
    assert r.status_code == 403, r.text
    assert "branch" in r.json()["detail"].lower()


def test_employee_allowed_when_branch_module_enabled(client, db, auth_headers):
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Module Toggle Branch B"}).json()
    r = client.put(f"/api/v1/branches/{branch['id']}", headers=auth_headers, json={"modules_enabled": ["sales", "inventory"]})
    assert r.status_code == 200, r.text

    company_id = _company_id(client, auth_headers)
    emp = Employee(company_id=company_id, employee_no="BR-MOD-B", full_name="Module Toggle Staff B", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "modtest.branchb", ["employees:view"], "Module Toggle Role B")

    r = client.get("/api/v1/inventory/stock-levels", headers=headers)
    assert r.status_code == 200, r.text


def test_employee_unrestricted_branch_module_toggle_unaffected(client, db, auth_headers):
    """A branch with modules_enabled left NULL (never explicitly
    restricted — the default for every pre-existing branch) doesn't newly
    block its employees. Critical regression guard: this changes a code
    path every branch-assigned employee's request goes through."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Unrestricted Toggle Branch"}).json()
    # Deliberately not calling PUT .../modules_enabled — stays NULL.
    company_id = _company_id(client, auth_headers)
    emp = Employee(company_id=company_id, employee_no="BR-MOD-C", full_name="Unrestricted Staff", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "modtest.branchc", ["employees:view"], "Unrestricted Toggle Role")

    r = client.get("/api/v1/inventory/stock-levels", headers=headers)
    assert r.status_code == 200, r.text


def test_employee_blocked_by_branch_module_toggle_on_appdata_writes(client, db, auth_headers):
    """assert_collection_module_enabled() (app_data.py) is the actual write
    choke point for POS/Sales/Purchase collections, separate from
    require_module()-decorated routers — needs the identical branch check."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Module Toggle Branch D"}).json()
    r = client.put(f"/api/v1/branches/{branch['id']}", headers=auth_headers, json={"modules_enabled": ["sales"]})
    assert r.status_code == 200, r.text

    company_id = _company_id(client, auth_headers)
    emp = Employee(company_id=company_id, employee_no="BR-MOD-D", full_name="Module Toggle Staff D", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "modtest.branchd", ["employees:view"], "Module Toggle Role D")

    r = client.post(
        "/api/v1/app-data?action=save", headers=headers,
        json={"collection": "bills", "record": {"ref": "BILL-MODTEST-D-001", "amount": 100}},
    )
    assert r.status_code == 403, r.text
    # salesInvoices IS enabled for this branch — must still work.
    r2 = client.post(
        "/api/v1/app-data?action=save", headers=headers,
        json={"collection": "salesInvoices", "record": {"invoice_no": "INV-MODTEST-D-001", "amount": 100}},
    )
    assert r2.status_code == 200, r2.text
