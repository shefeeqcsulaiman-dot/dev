"""Branch Security Layer Phase 3: multi-branch users.

Employee.branch_id stays the primary/default branch, unchanged. A new
EmployeeBranchAccess join table (same shape as the existing EmployeeLocation
precedent) grants ADDITIONAL branches an employee may switch into via
?branch_id=, resolved server-side by resolve_active_branch() (auth_principal.py)
and never trusted from the client beyond the employee's own accessible set —
same "cannot escalate" guarantee proven for the single-branch case in
test_branch_isolation.py, now proven for a genuinely multi-branch employee too.
"""
from app.models import Employee, EmployeeBranchAccess


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
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
        json={"username": username, "password": "branchtest123", "role_id": role["id"], "is_active": True},
    )
    assert r.status_code == 200, r.text

    r = client.post("/api/v1/ess/login", json={"username": username, "password": "branchtest123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _save_purchase_record(client, headers, ref, branch_id, sku, quantity):
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref, "supplier": "MB Test Supplier", "branch_id": branch_id,
                "net_amount": 100, "tax_amount": 5, "total": 105,
                "lines": [{"sku": sku, "product": sku, "quantity": quantity, "unit_cost": 10}],
            },
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_branch_access_endpoint_grants_and_lists_extra_branches(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Grant A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Grant B"}).json()
    branch_c = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Grant C"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-GRANT", full_name="MB Grant Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()

    granted = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/branch-access",
        headers=auth_headers, json={"branch_ids": [branch_b["id"], branch_c["id"]]},
    )
    assert granted.status_code == 200, granted.text
    assert set(granted.json()["branch_ids"]) == {branch_b["id"], branch_c["id"]}

    listed = client.get(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    ids_by_primary = {row["id"]: row["is_primary"] for row in listed.json()}
    assert ids_by_primary == {branch_a["id"]: True, branch_b["id"]: False, branch_c["id"]: False}


def test_branch_access_rejects_unknown_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Reject A"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-REJECT", full_name="MB Reject Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()

    resp = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/branch-access",
        headers=auth_headers, json={"branch_ids": ["not-a-real-branch-id"]},
    )
    assert resp.status_code == 404, resp.text


def test_branch_access_replace_all_semantics(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Replace A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Replace B"}).json()
    branch_c = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Replace C"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-REPLACE", full_name="MB Replace Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()

    client.put(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers, json={"branch_ids": [branch_b["id"]]})
    # Second call REPLACES, doesn't add to, the first.
    second = client.put(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers, json={"branch_ids": [branch_c["id"]]})
    assert second.status_code == 200, second.text
    assert second.json()["branch_ids"] == [branch_c["id"]]
    listed = client.get(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers)
    extra_ids = {row["id"] for row in listed.json() if not row["is_primary"]}
    assert extra_ids == {branch_c["id"]}


def test_whoami_accessible_branches_empty_for_single_branch_employee(client, db, auth_headers):
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Whoami Single"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-WHOAMI-1", full_name="MB Whoami Single Staff", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "mbtest.whoami1", ["employees:view"], "Administrator")

    who = client.get("/api/v1/auth/whoami", headers=headers)
    assert who.status_code == 200, who.text
    assert who.json()["accessible_branches"] == []


def test_whoami_accessible_branches_populated_for_multi_branch_employee(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Whoami A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Whoami B"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-WHOAMI-2", full_name="MB Whoami Multi Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()
    client.put(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers, json={"branch_ids": [branch_b["id"]]})
    headers = _grant_role_and_login(client, auth_headers, emp.id, "mbtest.whoami2", ["employees:view"], "Administrator")

    who = client.get("/api/v1/auth/whoami", headers=headers)
    assert who.status_code == 200, who.text
    names = {b["name"] for b in who.json()["accessible_branches"]}
    assert names == {"MB Whoami A", "MB Whoami B"}


def test_multi_branch_employee_can_switch_via_query_param(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Switch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Switch B"}).json()
    branch_c = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Switch C"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-SWITCH", full_name="MB Switch Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()
    client.put(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers, json={"branch_ids": [branch_b["id"]]})
    headers = _grant_role_and_login(client, auth_headers, emp.id, "mbtest.switch", ["employees:view"], "Administrator")

    _save_purchase_record(client, auth_headers, "PUR-MB-SWITCH-A", branch_a["id"], "MB-SWITCH-SKU-A", 1)
    _save_purchase_record(client, auth_headers, "PUR-MB-SWITCH-B", branch_b["id"], "MB-SWITCH-SKU-B", 1)
    _save_purchase_record(client, auth_headers, "PUR-MB-SWITCH-C", branch_c["id"], "MB-SWITCH-SKU-C", 1)

    # Membership checks, not exact-set equality — this is a shared,
    # session-scoped test tenant, so other tests' branch-less (NULL
    # branch_id) purchase records legitimately also show up via the
    # "OR NULL" legacy-data clause every branch filter in this app applies.

    # Default (no ?branch_id=) — falls back to their primary branch, A.
    default_view = client.get("/api/v1/app-data/records/purchaseRecords", headers=headers)
    assert default_view.status_code == 200, default_view.text
    refs = {rec["ref"] for rec in default_view.json()["records"]}
    assert "PUR-MB-SWITCH-A" in refs
    assert "PUR-MB-SWITCH-B" not in refs and "PUR-MB-SWITCH-C" not in refs

    # Explicit switch to branch B (one of their OWN assigned branches).
    switched = client.get(f"/api/v1/app-data/records/purchaseRecords?branch_id={branch_b['id']}", headers=headers)
    assert switched.status_code == 200, switched.text
    refs_b = {rec["ref"] for rec in switched.json()["records"]}
    assert "PUR-MB-SWITCH-B" in refs_b
    assert "PUR-MB-SWITCH-A" not in refs_b and "PUR-MB-SWITCH-C" not in refs_b

    # Cannot escalate to branch C — not one of their assigned branches, so
    # the resolver silently falls back to their primary branch A instead.
    escalate_attempt = client.get(f"/api/v1/app-data/records/purchaseRecords?branch_id={branch_c['id']}", headers=headers)
    assert escalate_attempt.status_code == 200, escalate_attempt.text
    refs_escalate = {rec["ref"] for rec in escalate_attempt.json()["records"]}
    assert "PUR-MB-SWITCH-A" in refs_escalate
    assert "PUR-MB-SWITCH-C" not in refs_escalate


def test_multi_branch_employee_can_switch_invoices_endpoint(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Inv Switch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Inv Switch B"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-INV-SWITCH", full_name="MB Inv Switch Staff", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()
    client.put(f"/api/v1/hr/admin/employees/{emp.id}/branch-access", headers=auth_headers, json={"branch_ids": [branch_b["id"]]})
    headers = _grant_role_and_login(client, auth_headers, emp.id, "mbtest.invswitch", ["employees:view"], "Administrator")

    for ref, branch_id in (("INV-MB-A-001", branch_a["id"]), ("INV-MB-B-001", branch_b["id"])):
        created = client.post(
            "/api/v1/invoices", headers=auth_headers,
            json={"customer_name": "MB Customer", "invoice_number": ref, "branch_id": branch_id, "lines": [{"description": "Item", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}]},
        )
        assert created.status_code == 201, created.text

    # Membership checks, not exact-set equality — this is a shared,
    # session-scoped test tenant, so other tests' branch-less (NULL
    # branch_id) invoices legitimately also show up via the "OR NULL"
    # legacy-data clause every branch filter in this app applies.
    default_view = client.get("/api/v1/invoices", headers=headers)
    numbers = {inv["invoice_number"] for inv in default_view.json()}
    assert "INV-MB-A-001" in numbers
    assert "INV-MB-B-001" not in numbers

    switched = client.get(f"/api/v1/invoices?branch_id={branch_b['id']}", headers=headers)
    numbers_b = {inv["invoice_number"] for inv in switched.json()}
    assert "INV-MB-B-001" in numbers_b
    assert "INV-MB-A-001" not in numbers_b


def test_unassigned_employee_branch_access_row_does_not_apply(client, db, auth_headers):
    """Backward compatibility: an employee with NO branch_id (unrestricted,
    sees everything today) stays unrestricted even if an EmployeeBranchAccess
    row somehow exists for them — the whole multi-branch feature is purely
    additive on top of Employee.branch_id, not a replacement for it."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "MB Unassigned A"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="MB-UNASSIGNED", full_name="MB Unassigned Staff")
    db.add(emp)
    db.commit()
    db.add(EmployeeBranchAccess(employee_id=emp.id, branch_id=branch_a["id"]))
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "mbtest.unassigned", ["employees:view"], "Administrator")

    who = client.get("/api/v1/auth/whoami", headers=headers)
    assert who.status_code == 200, who.text
    assert who.json()["branch_id"] is None
