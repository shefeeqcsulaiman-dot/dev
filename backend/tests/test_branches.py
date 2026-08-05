"""Branch Management, Phase 1: a real Branch entity (backend/app/models.py),
CRUD via /branches, migration of the old cosmetic Company.branches JSON list,
and Employee.branch_id / Principal.branch_id wiring. No data-isolation
filtering is added in this phase — see the plan file for later phases.
"""

import json

from app.models import Employee
from tests.conftest import ensure_user


def _fresh_tenant_headers(client, db, trn_suffix):
    # This test's migration assertion requires a company that has never had
    # any Branch rows created — auth_headers's tenant is shared across the
    # whole test session (other test files/tests create branches on it too),
    # so a dedicated fresh tenant is needed here rather than reusing it.
    ensure_user(db, f"branch-migrate-{trn_suffix}@taxflowqa.com", f"90000000009{trn_suffix}", role="admin")
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": f"branch-migrate-{trn_suffix}@taxflowqa.com", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_branch_crud(client, auth_headers):
    created = client.post(
        "/api/v1/branches",
        headers=auth_headers,
        json={"name": "Dubai Marina", "code": "DXM", "city": "Dubai"},
    )
    assert created.status_code == 201, created.text
    branch = created.json()
    assert branch["name"] == "Dubai Marina"
    assert branch["code"] == "DXM"
    assert branch["status"] == "Active"

    listed = client.get("/api/v1/branches", headers=auth_headers)
    assert listed.status_code == 200
    assert any(b["id"] == branch["id"] for b in listed.json())

    updated = client.put(
        f"/api/v1/branches/{branch['id']}",
        headers=auth_headers,
        json={"city": "Dubai Marina, JBR", "status": "Inactive"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["city"] == "Dubai Marina, JBR"
    assert updated.json()["status"] == "Inactive"

    deleted = client.delete(f"/api/v1/branches/{branch['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    listed_after = client.get("/api/v1/branches", headers=auth_headers)
    assert not any(b["id"] == branch["id"] for b in listed_after.json())


def test_branch_delete_unassigns_rather_than_orphans_employee(client, db, auth_headers):
    created = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Deira"})
    branch_id = created.json()["id"]

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-TEST-001", full_name="Branch Test Employee", branch_id=branch_id)
    db.add(emp)
    db.commit()

    deleted = client.delete(f"/api/v1/branches/{branch_id}", headers=auth_headers)
    assert deleted.status_code == 200

    db.refresh(emp)
    assert emp.branch_id is None


def test_legacy_company_branches_json_migrates_on_first_list(client, db):
    # Needs a tenant that has never had a Branch row created on it (the
    # migration is a permanent no-op once any Branch exists for the
    # company) — auth_headers's tenant is shared across the whole test
    # session and other tests do create branches on it, so use a fresh one.
    headers = _fresh_tenant_headers(client, db, "migrate01")
    updated = client.put(
        "/api/v1/companies/current",
        headers=headers,
        json={"branches": json.dumps([
            {"id": "br-1", "name": "Business Bay", "code": "BB", "city": "Dubai", "status": "Active"},
            {"id": "br-2", "name": "Sharjah", "code": "SHJ", "city": "Sharjah", "status": "Inactive"},
        ])},
    )
    assert updated.status_code == 200

    listed = client.get("/api/v1/branches", headers=headers)
    assert listed.status_code == 200
    names = {b["name"] for b in listed.json()}
    assert {"Business Bay", "Sharjah"}.issubset(names)
    sharjah = next(b for b in listed.json() if b["name"] == "Sharjah")
    assert sharjah["status"] == "Inactive"

    # Idempotent: calling again must not duplicate the migrated rows.
    listed_again = client.get("/api/v1/branches", headers=headers)
    assert len([b for b in listed_again.json() if b["name"] == "Business Bay"]) == 1


def test_employee_app_data_sync_sets_branch_id(client, db, auth_headers):
    created = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Ajman"})
    branch_id = created.json()["id"]

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "employees",
            "record": {
                "id": "EMP-BRANCH-001",
                "name": "Sync Test Employee",
                "branch_id": branch_id,
            },
        },
    )
    assert saved.status_code == 200, saved.text

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = db.query(Employee).filter(Employee.company_id == company_id, Employee.employee_no == "EMP-BRANCH-001").first()
    assert emp is not None
    assert emp.branch_id == branch_id


def test_employee_principal_carries_branch_id(client, db, auth_headers):
    created = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Fujairah HQ"})
    branch_id = created.json()["id"]

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-PRINCIPAL-001", full_name="Fujairah Staff", branch_id=branch_id)
    db.add(emp)
    db.commit()

    role_resp = client.post(
        "/api/v1/hr/admin/roles",
        headers=auth_headers,
        json={"role_name": "Fujairah Branch Staff", "description": "test role", "permission_keys": ["employees:view"]},
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()

    portal_resp = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=auth_headers,
        json={"username": "branchtest.fujairah", "password": "branch123", "role_id": role["id"], "is_active": True},
    )
    assert portal_resp.status_code == 200, portal_resp.text

    login_resp = client.post("/api/v1/ess/login", json={"username": "branchtest.fujairah", "password": "branch123"})
    assert login_resp.status_code == 200, login_resp.text
    emp_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    who = client.get("/api/v1/auth/whoami", headers=emp_headers)
    assert who.status_code == 200
    assert who.json()["branch_id"] == branch_id

    # The company admin (User token) is never branch-scoped — matches
    # today's "admin sees everything" behavior, unaffected by branches.
    who_admin = client.get("/api/v1/auth/whoami", headers=auth_headers)
    assert who_admin.json()["branch_id"] is None


def test_zero_branches_behaves_identically_to_today(client, second_tenant_headers):
    """Backward-compatibility guarantee: a company that never creates a
    branch must see no behavior change at all."""
    listed = client.get("/api/v1/branches", headers=second_tenant_headers)
    assert listed.status_code == 200
    assert listed.json() == []

    who = client.get("/api/v1/auth/whoami", headers=second_tenant_headers)
    assert who.status_code == 200
    assert who.json()["branch_id"] is None
