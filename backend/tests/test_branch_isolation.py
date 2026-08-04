"""Branch Management: data isolation tests.

Phase 2: HR/Attendance — the first module scoped by branch_id, proving out
the principal.branch_id filter pattern.
Phase 3: Accounting (trial balance) — a purely ORM-derived report (posted
JournalLine/JournalEntry only, no AppDataRecord JSON dependency), so it can
be fully and correctly branch-filtered today. The Dashboard/Summary reports
mix in AppDataRecord-JSON-derived sales/purchases data, which has no
branch_id yet (that lands in a later phase alongside POS/Sales) — widening
those to accept branch-scoped principals now would silently under-filter
and leak company-wide data, so they're deliberately left User-only until
AppDataRecord itself is branch-aware.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import AttendanceSession, Employee


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
    # auth_headers reuses the same tenant across tests in this session-scoped
    # test DB, so role NAMES (unique per company) can't be minted fresh per
    # call the way test_hrms_rbac_login.py does — but hr_dashboard() branches
    # on an EXACT role_name match ("Administrator"/"HR Manager"), so this
    # helper reuses an existing role of that name across tests instead of
    # suffixing it into uniqueness.
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


def _open_session(db, company_id, employee_id, branch_id):
    session = AttendanceSession(
        company_id=company_id, employee_id=employee_id, branch_id=branch_id,
        check_in=datetime.now(UTC), status="open",
    )
    db.add(session)
    db.commit()
    return session


def test_branch_scoped_administrator_only_sees_own_branch_attendance(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Dubai Marina"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Deira"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-A", full_name="Marina Admin", branch_id=branch_a["id"])
    emp_b = Employee(company_id=company_id, employee_no="BR-ISO-B", full_name="Deira Admin", branch_id=branch_b["id"])
    db.add_all([emp_a, emp_b])
    db.commit()

    # One open session per branch.
    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_b.id, branch_b["id"])

    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.marina", ["employees:view"], "Administrator")

    dash = client.get("/api/v1/hr/dashboard", headers=headers_a)
    assert dash.status_code == 200, dash.text
    # Branch A's Administrator sees only Branch A's open session, not Branch B's.
    assert dash.json()["active_sessions_now"] == 1


def test_branch_scoped_employee_still_sees_branch_less_legacy_sessions(client, db, auth_headers):
    """NULL branch_id means "predates Branch Management" — must stay
    visible to a branch-scoped viewer, not silently vanish."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Business Bay"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-C", full_name="Business Bay Admin", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()

    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.bizbay", ["employees:view"], "Administrator")
    # Delta, not absolute count — a NULL branch_id is visible to every
    # branch-scoped viewer, so other tests' legacy sessions on this shared
    # tenant could otherwise make an exact count flaky.
    before = client.get("/api/v1/hr/dashboard", headers=headers_a).json()["active_sessions_now"]

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_a.id, None)  # legacy, pre-branch session

    dash = client.get("/api/v1/hr/dashboard", headers=headers_a)
    assert dash.status_code == 200
    assert dash.json()["active_sessions_now"] == before + 2


def test_unassigned_employee_sees_all_branches_unchanged(client, db, auth_headers):
    """Backward compatibility: an Employee with no branch_id (the only
    possibility before this feature, and for companies that never set up
    branches) sees company-wide data exactly like today."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Sharjah"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Ajman"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_unassigned = Employee(company_id=company_id, employee_no="BR-ISO-D", full_name="HQ Admin")
    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-E", full_name="Sharjah Staff", branch_id=branch_a["id"])
    db.add_all([emp_unassigned, emp_a])
    db.commit()

    headers_unassigned = _grant_role_and_login(
        client, auth_headers, emp_unassigned.id, "branchtest.hq", ["employees:view"], "Administrator"
    )
    # This test tenant is shared across other tests in this file/session, so
    # assert a DELTA rather than an absolute count — robust regardless of
    # sessions other tests already left open on the same company.
    before = client.get("/api/v1/hr/dashboard", headers=headers_unassigned).json()["active_sessions_now"]

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_a.id, branch_b["id"])

    dash = client.get("/api/v1/hr/dashboard", headers=headers_unassigned)
    assert dash.status_code == 200
    assert dash.json()["active_sessions_now"] == before + 2


def test_live_locations_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Al Ain"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Fujairah"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-F", full_name="Al Ain Viewer", branch_id=branch_a["id"])
    emp_b = Employee(company_id=company_id, employee_no="BR-ISO-G", full_name="Fujairah Staff", branch_id=branch_b["id"])
    db.add_all([emp_a, emp_b])
    db.commit()

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_b.id, branch_b["id"])

    headers_a = _grant_role_and_login(
        client, auth_headers, emp_a.id, "branchtest.alain", ["hr:view_all_attendance"], "Attendance Viewer"
    )
    live = client.get("/api/v1/hr/live-locations", headers=headers_a)
    assert live.status_code == 200, live.text
    employee_ids = {row["employee_id"] for row in live.json()}
    # A NULL branch_id (legacy data, possibly left by other tests sharing
    # this tenant) is visible too, so assert membership, not an exact set.
    assert emp_a.id in employee_ids
    assert emp_b.id not in employee_ids


def _post_and_approve_source(client, headers, reference, branch_id, account_code="3000"):
    created = client.post(
        "/api/v1/source-transactions",
        headers=headers,
        json={
            "module": "sales",
            "reference": reference,
            "party_name": "Branch Trial Balance Test",
            "branch_id": branch_id,
            "lines": [{"description": "Sale", "account_code": account_code, "quantity": "1", "unit_price": "1000.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["branch_id"] == branch_id
    approved = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=headers)
    assert approved.status_code == 202, approved.text
    return source


def _trial_balance_account_row(client, headers, code):
    rows = client.get("/api/v1/reports/trial-balance", headers=headers).json()["rows"]
    return next((r for r in rows if r["code"] == code), None)


def test_trial_balance_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-TB-A", full_name="TB Branch A Admin", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.tba", ["employees:view"], "Administrator")

    # Baseline before either branch's postings, so the assertion is a delta
    # (shared tenant, other tests may have already posted to account 3000).
    before_a = _trial_balance_account_row(client, headers_a, "3000")
    before_credit_a = Decimal(before_a["credit"]) if before_a else Decimal("0")

    _post_and_approve_source(client, auth_headers, "SRC-TB-A-001", branch_a["id"])
    _post_and_approve_source(client, auth_headers, "SRC-TB-B-001", branch_b["id"])

    # Branch A's viewer sees only Branch A's 1000.00 sale on account 3000,
    # not Branch B's — a company-wide trial balance would show both.
    row_a = _trial_balance_account_row(client, headers_a, "3000")
    assert row_a is not None
    assert Decimal(row_a["credit"]) - before_credit_a == Decimal("1000.00")


def test_trial_balance_unassigned_employee_sees_all_branches(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch C"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch D"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_unassigned = Employee(company_id=company_id, employee_no="BR-TB-U", full_name="TB HQ Admin")
    db.add(emp_unassigned)
    db.commit()
    headers_u = _grant_role_and_login(client, auth_headers, emp_unassigned.id, "branchtest.tbu", ["employees:view"], "Administrator")

    before = _trial_balance_account_row(client, headers_u, "3000")
    before_credit = Decimal(before["credit"]) if before else Decimal("0")

    _post_and_approve_source(client, auth_headers, "SRC-TB-C-001", branch_a["id"])
    _post_and_approve_source(client, auth_headers, "SRC-TB-D-001", branch_b["id"])

    # Unassigned (no branch_id) employee sees company-wide totals, both
    # branches' 1000.00 sales included — unchanged from pre-feature behavior.
    row = _trial_balance_account_row(client, headers_u, "3000")
    assert Decimal(row["credit"]) - before_credit == Decimal("2000.00")


def test_trial_balance_requires_reports_view_permission(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch E"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-TB-NOPERM", full_name="No Reports Access", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()
    # Deliberately no "reports:view" in the granted permissions.
    headers_no_perm = _grant_role_and_login(
        client, auth_headers, emp.id, "branchtest.tbnoperm", ["employees:view"], "Branch Staff No Reports"
    )
    resp = client.get("/api/v1/reports/trial-balance", headers=headers_no_perm)
    assert resp.status_code == 403
