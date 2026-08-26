"""Super Admin > impersonate a Branch Login identity (not just a company
admin user) — lands on the main dashboard exactly as that branch would see
it (Dashboard branch-scoping, reports.py). See superadmin.py's
impersonate_company() (branch_id path), auth_principal.py's
_principal_from_branch_token() revocation check, and auth.py's whoami()
impersonated_by field.
"""
from app.models import Branch, Company, ImpersonationSession, User
from app.security import hash_password


def _make_superadmin_headers(client, db):
    admin = db.query(User).filter(User.email == "impersonate-branch-superadmin@etaxflow.com").first()
    if not admin:
        company = Company(name="Impersonate Branch Superadmin Test", trn="IMPERSONATE-BRANCH-SUPERADMIN")
        db.add(company)
        db.flush()
        admin = User(
            company_id=company.id, email="impersonate-branch-superadmin@etaxflow.com",
            full_name="Impersonate Branch Super Admin", role="superadmin",
            password_hash=hash_password("test12345"),
        )
        db.add(admin)
        db.commit()
    r = client.post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _create_branch(client, admin_headers, name="Impersonation Test Branch"):
    r = client.post("/api/v1/branches", headers=admin_headers, json={
        "name": name, "username": name.lower().replace(" ", "-") + "-login", "password": "branchlogin123",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_impersonate_branch_returns_working_token(client, auth_headers, db):
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    branch = _create_branch(client, auth_headers)

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": branch["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"] is None
    assert body["branch"]["id"] == branch["id"]
    branch_headers = {"Authorization": f"Bearer {body['access_token']}"}

    who = client.get("/api/v1/auth/whoami", headers=branch_headers)
    assert who.status_code == 200, who.text
    who_data = who.json()
    assert who_data["kind"] == "branch"
    assert who_data["branch_id"] == branch["id"]
    assert who_data["impersonated_by"]["email"] == "impersonate-branch-superadmin@etaxflow.com"


def test_normal_branch_login_has_no_impersonated_by(client, auth_headers, db):
    branch = _create_branch(client, auth_headers, name="Normal Login Branch")
    login = client.post("/api/v1/branches/login", json={"username": branch["username"], "password": "branchlogin123"})
    assert login.status_code == 200, login.text
    who = client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert who.status_code == 200
    assert who.json()["impersonated_by"] is None


def test_end_impersonation_revokes_branch_token(client, auth_headers, db):
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    branch = _create_branch(client, auth_headers, name="Revoke Test Branch")

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": branch["id"]},
    )
    assert resp.status_code == 200, resp.text
    branch_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # Works before ending
    assert client.get("/api/v1/auth/whoami", headers=branch_headers).status_code == 200

    ended = client.post("/api/v1/superadmin/end-impersonation", headers=branch_headers)
    assert ended.status_code == 200, ended.text

    # The same (now-revoked) token must stop working immediately, not just
    # be logged as ended — this is exactly the gap _principal_from_branch_token()
    # previously had (no is_impersonation_token_revoked() check at all).
    after = client.get("/api/v1/auth/whoami", headers=branch_headers)
    assert after.status_code == 401


def test_impersonation_sessions_list_shows_branch_target(client, auth_headers, db):
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    branch = _create_branch(client, auth_headers, name="Listed Session Branch")

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": branch["id"]},
    )
    assert resp.status_code == 200, resp.text

    sessions = client.get("/api/v1/superadmin/impersonation-sessions?active_only=true", headers=superadmin_headers)
    assert sessions.status_code == 200, sessions.text
    rows = [r for r in sessions.json() if r["company_id"] == company_id]
    assert rows, "expected at least one active session for this company"
    row = rows[0]
    assert row["target_kind"] == "branch"
    assert row["target_user_name"] == branch["name"]
    assert row["target_user_email"] is None


def test_force_end_works_for_branch_session(client, auth_headers, db):
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    branch = _create_branch(client, auth_headers, name="Force End Branch")

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": branch["id"]},
    )
    assert resp.status_code == 200, resp.text
    branch_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    session_row = db.query(ImpersonationSession).filter(ImpersonationSession.target_branch_id == branch["id"]).first()
    assert session_row is not None

    force_end = client.post(
        f"/api/v1/superadmin/impersonation-sessions/{session_row.id}/force-end",
        headers=superadmin_headers,
    )
    assert force_end.status_code == 200, force_end.text

    after = client.get("/api/v1/auth/whoami", headers=branch_headers)
    assert after.status_code == 401


def test_impersonated_branch_can_reach_branch_scoped_dashboard(client, auth_headers, db):
    """End-to-end: the whole point of this feature is landing on the main
    dashboard exactly as the branch would see it (Dashboard branch-scoping,
    reports.py's dashboard() / resolve_active_branch())."""
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    branch = _create_branch(client, auth_headers, name="Dashboard Branch")

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": branch["id"]},
    )
    assert resp.status_code == 200, resp.text
    branch_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    dashboard = client.get("/api/v1/reports/dashboard", headers=branch_headers)
    assert dashboard.status_code == 200, dashboard.text


def test_impersonate_unknown_branch_id_rejected(client, auth_headers, db):
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": "no-such-branch"},
    )
    assert resp.status_code == 404


def test_impersonate_branch_from_different_company_rejected(client, auth_headers, second_tenant_headers, db):
    """A branch belonging to a DIFFERENT company than {company_id} in the URL
    must not be impersonable through it."""
    superadmin_headers = _make_superadmin_headers(client, db)
    company_id = _company_id(client, auth_headers)
    other_branch = _create_branch(client, second_tenant_headers, name="Other Tenant Branch")

    resp = client.post(
        f"/api/v1/superadmin/companies/{company_id}/impersonate",
        headers=superadmin_headers, json={"branch_id": other_branch["id"]},
    )
    assert resp.status_code == 404
