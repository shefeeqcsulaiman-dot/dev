"""Regression coverage for DELETE /superadmin/companies/{id}. The cascade
manually deletes ~60 company-scoped tables in FK-safe order (no ON DELETE
CASCADE at the DB level) — Role, RolePermission, CompanyLocation,
EmployeeLocation, AttendanceSession, EmployeeLocationLog, and LeaveRequest
were added to the schema after this cascade was written and were never
added to it. Deleting any company that had used HRMS GPS attendance,
custom roles, or leave requests raised an uncaught FK IntegrityError,
surfaced to the user as a generic "An internal error occurred" — found
via a live report of exactly that error when deleting a real company."""
from datetime import UTC, datetime

from app.models import (
    Account, AttendanceSession, Company, CompanyLocation, Employee,
    EmployeeLocation, EmployeeLocationLog, GeneralLedgerEntry, JournalEntry,
    JournalLine, LeaveRequest, Permission, Role, RolePermission, User,
)
from app.security import hash_password


def _make_superadmin(db):
    # Idempotent — Company.trn is unique, and this test module's DB persists
    # across every test function in the session (same shared-tenant gotcha
    # documented for auth_headers elsewhere in this suite), so a second call
    # from a different test function must reuse the existing row rather than
    # colliding on a fresh insert of the same literal TRN/email.
    admin = db.query(User).filter(User.email == "superadmin-test@etaxflow.com").first()
    if not admin:
        company = Company(name="ETaxFlow Admin Test", trn="SUPERADMIN-TEST")
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, email="superadmin-test@etaxflow.com",
                     full_name="Super Admin", role="superadmin", password_hash=hash_password("test12345"))
        db.add(admin)
        db.commit()
    r = _login_client_ref["client"].post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


_login_client_ref = {}


def test_delete_company_with_hrms_gps_and_rbac_data(client, db):
    _login_client_ref["client"] = client
    target = Company(name="Target Co", trn="TARGET-DELETE-TEST")
    db.add(target)
    db.flush()

    emp = Employee(company_id=target.id, employee_no="DEL-001", full_name="Delete Test Employee")
    db.add(emp)
    db.flush()

    role = Role(company_id=target.id, role_name="Delete Test Role")
    db.add(role)
    db.flush()
    perm = db.query(Permission).first()
    if not perm:
        perm = Permission(module="employees", permission_name="view")
        db.add(perm)
        db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    emp.role_id = role.id

    location = CompanyLocation(company_id=target.id, location_name="HQ", latitude=25.2, longitude=55.3)
    db.add(location)
    db.flush()
    emp.work_location_id = location.id
    db.add(EmployeeLocation(employee_id=emp.id, location_id=location.id))

    session = AttendanceSession(company_id=target.id, employee_id=emp.id, location_id=location.id,
                                 check_in=datetime.now(UTC), status="open")
    db.add(session)
    db.flush()
    db.add(EmployeeLocationLog(company_id=target.id, employee_id=emp.id, session_id=session.id,
                                latitude=25.2, longitude=55.3))
    db.add(LeaveRequest(company_id=target.id, employee_id=emp.id, leave_type="Annual Leave",
                         start_date="2026-08-01", end_date="2026-08-02", days=2, status="pending"))

    # A company with real, posted transactions has GeneralLedgerEntry rows
    # whose journal_line_id FKs into journal_lines — reproduces the ordering
    # bug (GL entries were deleted after journal_lines, not before).
    account = Account(company_id=target.id, code="1000", name="Cash", type="asset")
    db.add(account)
    db.flush()
    journal = JournalEntry(company_id=target.id, entry_number="JE-DEL-TEST-001", description="Delete test entry")
    db.add(journal)
    db.flush()
    line = JournalLine(journal_id=journal.id, account_id=account.id, debit=100, credit=0)
    db.add(line)
    db.flush()
    db.add(GeneralLedgerEntry(company_id=target.id, voucher_no="JE-DEL-TEST-001", voucher_type="Journal",
                               account_id=account.id, journal_entry_id=journal.id, journal_line_id=line.id,
                               debit=100, credit=0))
    db.commit()

    headers = _make_superadmin(db)
    r = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 200, r.text

    assert db.query(Company).filter(Company.id == target.id).first() is None
    assert db.query(Employee).filter(Employee.company_id == target.id).count() == 0
    assert db.query(Role).filter(Role.company_id == target.id).count() == 0
    assert db.query(CompanyLocation).filter(CompanyLocation.company_id == target.id).count() == 0
    assert db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.company_id == target.id).count() == 0
    assert db.query(JournalEntry).filter(JournalEntry.company_id == target.id).count() == 0


def test_delete_company_rejects_wrong_password(client, db):
    """Regression test for a real security gap: the endpoint used to accept
    DELETE with no body/secret at all — the only "authorization" was two
    hardcoded string literals checked entirely client-side in
    superadmin.html, never sent to or verified by the backend. Confirms the
    server now independently verifies the acting superadmin's own account
    password and rejects the company survives a wrong or missing one."""
    _login_client_ref["client"] = client
    target = Company(name="Wrong Password Target Co", trn="TARGET-DELETE-WRONGPWD")
    db.add(target)
    db.commit()

    headers = _make_superadmin(db)

    wrong = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "not-the-real-password"})
    assert wrong.status_code == 403, wrong.text

    missing = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={})
    assert missing.status_code == 422, missing.text

    assert db.query(Company).filter(Company.id == target.id).first() is not None
