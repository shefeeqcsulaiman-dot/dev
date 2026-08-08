"""Branch Login Phase 2: the Branch entity's own shared login — a third
identity alongside User (admin) and Employee (RBAC sub-user), reached via
the same /login page and POST /branches/login. No Role of its own: per the
"module toggle alone = full access" design decision, a Branch principal's
permissions are synthesized directly from the intersection of company- and
branch-enabled modules (see auth_principal.py::_principal_from_branch_token).
Branches are completely isolated — a single accessible_branch_id, no
switching, matching the existing branch data-isolation machinery already
proven in test_branch_isolation.py.
"""

from datetime import UTC, datetime, timedelta

from app.models import AuditLog, Branch, Company, Employee
from app.security import hash_password


def _yesterday():
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _create_branch_with_login(client, admin_headers, name, username, password="branchlogin123", modules=None):
    payload = {"name": name, "username": username, "password": password}
    if modules is not None:
        payload["modules_enabled"] = modules
    r = client.post("/api/v1/branches", headers=admin_headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _branch_login(client, username, password="branchlogin123"):
    r = client.post("/api/v1/branches/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _save_purchase_record(client, headers, ref, branch_id, sku, quantity):
    r = client.post(
        "/api/v1/app-data?action=save", headers=headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref,
                "supplier": "Branch Login Test Supplier",
                "branch_id": branch_id,
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "lines": [{"sku": sku, "product": sku, "quantity": quantity, "unit_cost": 10}],
            },
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── Login flow ────────────────────────────────────────────────────────────


def test_branch_login_succeeds_and_whoami_reports_branch_kind(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Login Test Branch A", "loginbranch.a")
    headers = _branch_login(client, "loginbranch.a")

    who = client.get("/api/v1/auth/whoami", headers=headers)
    assert who.status_code == 200, who.text
    body = who.json()
    assert body["kind"] == "branch"
    assert body["id"] == branch["id"]
    assert body["is_admin"] is False
    assert body["branch_id"] == branch["id"]
    # Completely isolated — no switching, so the >1-accessible-branches
    # gate on accessible_branches never fires for a Branch identity.
    assert body["accessible_branches"] == []


def test_branch_login_case_insensitive_username(client, auth_headers):
    _create_branch_with_login(client, auth_headers, "Case Test Branch", "CaseTest.Branch")
    headers = _branch_login(client, "casetest.branch")
    who = client.get("/api/v1/auth/whoami", headers=headers)
    assert who.status_code == 200, who.text
    assert who.json()["kind"] == "branch"


def test_branch_login_wrong_password_rejected(client, auth_headers):
    _create_branch_with_login(client, auth_headers, "Wrong Pw Branch", "wrongpw.branch")
    r = client.post("/api/v1/branches/login", json={"username": "wrongpw.branch", "password": "not-the-password"})
    assert r.status_code == 401, r.text


def test_branch_login_unknown_username_rejected(client, auth_headers):
    r = client.post("/api/v1/branches/login", json={"username": "no-such-branch-username", "password": "whatever123"})
    assert r.status_code == 401, r.text


def test_branch_with_no_password_set_cannot_login(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    branch = Branch(company_id=company_id, name="No Password Branch", username="nopassword.branch")
    db.add(branch)
    db.commit()
    r = client.post("/api/v1/branches/login", json={"username": "nopassword.branch", "password": "anything123"})
    assert r.status_code == 401, r.text


def test_branch_login_rejects_disabled_branch(client, db, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Disabled Login Branch", "disabled.branch")
    row = db.query(Branch).filter(Branch.id == branch["id"]).first()
    row.status = "Inactive"
    db.commit()
    r = client.post("/api/v1/branches/login", json={"username": "disabled.branch", "password": "branchlogin123"})
    assert r.status_code == 403, r.text


def test_branch_login_rejects_suspended_company(client, db, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Suspended Co Branch", "suspended.branch")
    company_id = _company_id(client, auth_headers)
    company = db.query(Company).filter(Company.id == company_id).first()
    company.subscription_expires_at = _yesterday()
    db.commit()
    try:
        r = client.post("/api/v1/branches/login", json={"username": "suspended.branch", "password": "branchlogin123"})
        assert r.status_code == 403, r.text
    finally:
        company.subscription_expires_at = None
        db.commit()


# ── Credential management (POST/PUT /branches) ───────────────────────────


def test_create_branch_password_too_short_rejected(client, auth_headers):
    r = client.post(
        "/api/v1/branches", headers=auth_headers,
        json={"name": "Short Pw Branch", "username": "shortpw.branch", "password": "abc"},
    )
    assert r.status_code == 400, r.text


def test_duplicate_branch_username_rejected(client, auth_headers):
    _create_branch_with_login(client, auth_headers, "Dup Username Branch 1", "dupname.branch")
    r = client.post(
        "/api/v1/branches", headers=auth_headers,
        json={"name": "Dup Username Branch 2", "username": "dupname.branch", "password": "branchlogin123"},
    )
    assert r.status_code == 409, r.text


def test_update_branch_omits_blank_password_keeps_existing(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Keep Password Branch", "keeppw.branch")
    # Edit save with username set but password field left blank (omitted key) — mirrors
    # saveBranchModal()'s own omit-if-blank behavior.
    r = client.put(f"/api/v1/branches/{branch['id']}", headers=auth_headers, json={"username": "keeppw.branch"})
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is True
    headers = _branch_login(client, "keeppw.branch")  # original password still works
    assert client.get("/api/v1/auth/whoami", headers=headers).status_code == 200


def test_branch_out_never_exposes_password_hash(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "No Hash Leak Branch", "nohashleak.branch")
    assert "password_hash" not in branch
    assert "password" not in branch
    assert branch["has_password"] is True


# ── Module-toggle enforcement for a Branch principal ─────────────────────


def test_branch_principal_blocked_by_own_module_toggle(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Branch Module Gate A", "modgate.a", modules=["sales"])
    headers = _branch_login(client, "modgate.a")
    r = client.get("/api/v1/inventory/stock-levels", headers=headers)
    assert r.status_code == 403, r.text
    assert "branch" in r.json()["detail"].lower()


def test_branch_principal_allowed_for_own_enabled_module(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Branch Module Gate B", "modgate.b", modules=["sales", "inventory"])
    headers = _branch_login(client, "modgate.b")
    r = client.get("/api/v1/inventory/stock-levels", headers=headers)
    assert r.status_code == 200, r.text


def test_branch_principal_synthesized_permission_grants_view_access(client, auth_headers):
    """Proves the module-toggle -> permissions synthesis mechanism itself
    (require_principal_permission("accounting:view")-gated endpoints, not
    just require_module()-gated routers) — a Branch principal has no Role,
    so this only works if _principal_from_branch_token's synthesized
    "<module>:view" keys actually satisfy the check."""
    branch = _create_branch_with_login(client, auth_headers, "Branch Perm Synth", "permsynth.branch", modules=["accounting"])
    headers = _branch_login(client, "permsynth.branch")
    r = client.get("/api/v1/accounts", headers=headers)
    assert r.status_code == 200, r.text


def test_branch_principal_no_permission_for_disabled_module(client, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Branch Perm Synth No Acct", "permsynth.noacct", modules=["sales"])
    headers = _branch_login(client, "permsynth.noacct")
    r = client.get("/api/v1/accounts", headers=headers)
    assert r.status_code == 403, r.text


# ── Data isolation ─────────────────────────────────────────────────────


def test_branch_login_sees_only_own_branch_data(client, auth_headers):
    branch_a = _create_branch_with_login(client, auth_headers, "Isolation Branch A", "isolation.a", modules=["purchase"])
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Isolation Branch B"}).json()
    headers_a = _branch_login(client, "isolation.a")

    _save_purchase_record(client, auth_headers, "PUR-BRLOGIN-A-001", branch_a["id"], "BRLOGIN-SKU-A", 3)
    _save_purchase_record(client, auth_headers, "PUR-BRLOGIN-B-001", branch_b["id"], "BRLOGIN-SKU-B", 5)

    listed = client.get("/api/v1/app-data/records/purchaseRecords", headers=headers_a)
    assert listed.status_code == 200, listed.text
    refs = {rec["ref"] for rec in listed.json()["records"]}
    assert "PUR-BRLOGIN-A-001" in refs
    assert "PUR-BRLOGIN-B-001" not in refs

    # No branch-switching escapes the isolation — a Branch identity's
    # accessible_branch_ids is exactly {self.id}, so resolve_active_branch()
    # ignores any ?branch_id= override, same guarantee already proven for a
    # single-branch Employee.
    listed_override = client.get(f"/api/v1/app-data/records/purchaseRecords?branch_id={branch_b['id']}", headers=headers_a)
    assert listed_override.status_code == 200, listed_override.text
    refs_override = {rec["ref"] for rec in listed_override.json()["records"]}
    assert "PUR-BRLOGIN-B-001" not in refs_override


def test_company_admin_still_sees_all_branches_data(client, auth_headers):
    branch_a = _create_branch_with_login(client, auth_headers, "Admin View Branch A", "adminview.a", modules=["purchase"])
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Admin View Branch B"}).json()

    _save_purchase_record(client, auth_headers, "PUR-ADMINVIEW-A-001", branch_a["id"], "ADMINVIEW-SKU-A", 1)
    _save_purchase_record(client, auth_headers, "PUR-ADMINVIEW-B-001", branch_b["id"], "ADMINVIEW-SKU-B", 1)

    listed = client.get("/api/v1/app-data/records/purchaseRecords", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    refs = {rec["ref"] for rec in listed.json()["records"]}
    assert {"PUR-ADMINVIEW-A-001", "PUR-ADMINVIEW-B-001"}.issubset(refs)


# ── Audit trail ───────────────────────────────────────────────────────


def test_branch_actions_recorded_with_branch_actor_id(client, db, auth_headers):
    branch = _create_branch_with_login(client, auth_headers, "Audit Branch", "auditbranch.a", modules=["purchase"])
    headers = _branch_login(client, "auditbranch.a")
    _save_purchase_record(client, headers, "PUR-AUDITBRANCH-001", branch["id"], "AUDIT-SKU", 1)

    logged = (
        db.query(AuditLog)
        .filter(AuditLog.branch_actor_id == branch["id"])
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert logged is not None, "expected an AuditLog row with branch_actor_id set"
    assert logged.user_id is None
    assert logged.employee_id is None
