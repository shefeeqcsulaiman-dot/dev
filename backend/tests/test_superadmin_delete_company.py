"""Regression coverage for DELETE /superadmin/companies/{id}. The cascade
manually deletes ~60 company-scoped tables in FK-safe order (no ON DELETE
CASCADE at the DB level) — Role, RolePermission, CompanyLocation,
EmployeeLocation, AttendanceSession, EmployeeLocationLog, and LeaveRequest
were added to the schema after this cascade was written and were never
added to it. Deleting any company that had used HRMS GPS attendance,
custom roles, or leave requests raised an uncaught FK IntegrityError,
surfaced to the user as a generic "An internal error occurred" — found
via a live report of exactly that error when deleting a real company.

Delete now moves the company to a 7-day recycle bin after a backup; the cascade runs
when the nightly purge (worker.purge_recycle_bin) deletes it for good."""
from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    Account, AttendanceSession, Branch, Company, CompanyLocation, Employee,
    EmployeeLocation, EmployeeLocationLog, GeneralLedgerEntry,
    ImpersonationSession, JournalEntry, JournalLine, LeaveRequest,
    Permission, Role, RolePermission, User,
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


@pytest.fixture(autouse=True)
def _backups_in_tmp(tmp_path, monkeypatch):
    from app import storage
    monkeypatch.setattr(storage, "LOCAL_STORAGE_ROOT", tmp_path / "storage")
    return tmp_path / "storage"


def _delete_and_purge(client, db, headers, company_id):
    """Delete (to the recycle bin), then run the nightly purge as if 7 days had passed."""
    from app import worker

    r = client.request("DELETE", f"/api/v1/superadmin/companies/{company_id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 202, r.text
    db.expire_all()
    company = db.get(Company, company_id)
    assert company is not None and company.deleted_at is not None
    company.deleted_at = datetime.now(UTC) - timedelta(days=8)
    db.commit()
    result = worker.purge_recycle_bin()
    assert company_id in result["purged"], result
    db.expunge_all()  # the purged rows are gone; keep the test's objects readable


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
    tid = target.id
    _delete_and_purge(client, db, headers, tid)

    assert db.query(Company).filter(Company.id == tid).first() is None
    assert db.query(Employee).filter(Employee.company_id == tid).count() == 0
    assert db.query(Role).filter(Role.company_id == tid).count() == 0
    assert db.query(CompanyLocation).filter(CompanyLocation.company_id == tid).count() == 0
    assert db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.company_id == tid).count() == 0
    assert db.query(JournalEntry).filter(JournalEntry.company_id == tid).count() == 0


def test_delete_company_previously_impersonated_as_branch(client, db):
    """A company that was ever targeted by Super Admin > Impersonate (as
    either a branch or a company-admin user) left an ImpersonationSession
    row whose target_branch_id/target_user_id/company_id FK straight into
    this company's own data — delete_company() never cleaned those up, so
    any such company could no longer be deleted at all (FK IntegrityError,
    surfaced as a generic "An internal error occurred"), reproduced live by
    deleting a company that had just been used to test the branch-
    impersonation feature."""
    _login_client_ref["client"] = client
    target = Company(name="Impersonated Target Co", trn="TARGET-DELETE-IMPERSONATED")
    db.add(target)
    db.flush()
    branch = Branch(company_id=target.id, name="Del Test Branch")
    db.add(branch)
    db.commit()

    headers = _make_superadmin(db)
    imp = client.post(
        f"/api/v1/superadmin/companies/{target.id}/impersonate",
        headers=headers, json={"branch_id": branch.id},
    )
    assert imp.status_code == 200, imp.text
    assert db.query(ImpersonationSession).filter(ImpersonationSession.company_id == target.id).count() == 1

    tid = target.id
    _delete_and_purge(client, db, headers, tid)
    assert db.query(Company).filter(Company.id == tid).first() is None
    assert db.query(ImpersonationSession).filter(ImpersonationSession.company_id == tid).count() == 0


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


def _rows(payload):
    return payload.get("items", payload.get("companies", [])) if isinstance(payload, dict) else payload


def test_delete_moves_company_to_recycle_bin_with_backup(client, db, _backups_in_tmp):
    """Delete backs the company up, blocks its sign-ins and hides it, but keeps every row;
    Restore brings it back exactly as it was."""
    _login_client_ref["client"] = client
    target = Company(name="Recycle Target Co", trn="TARGET-RECYCLE", subscription_expires_at="2099-01-01")
    db.add(target)
    db.flush()
    db.add(User(company_id=target.id, email="recycle-admin@example.com", full_name="Recycle Admin",
                role="admin", password_hash=hash_password("Recycle-pass-1")))
    db.add(Employee(company_id=target.id, employee_no="REC-001", full_name="Recycle Employee"))
    db.commit()
    login = {"email": "recycle-admin@example.com", "password": "Recycle-pass-1"}
    token = client.post("/api/v1/auth/login", json=login).json()["access_token"]
    staff = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=staff).status_code == 200

    headers = _make_superadmin(db)
    assert any(c["id"] == target.id for c in _rows(client.get("/api/v1/superadmin/companies", headers=headers).json()))
    r = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 202, r.text
    # TestClient runs the background backup before returning.
    status = client.get(f"/api/v1/superadmin/companies/{target.id}/delete-status", headers=headers).json()
    assert status["status"] == "done", status
    db.expire_all()
    key = db.get(Company, target.id).deleted_backup_key
    assert key.startswith(f"deleted-company-backups/{target.id}/")
    assert (_backups_in_tmp / key).stat().st_size > 0

    db.expire_all()
    company = db.get(Company, target.id)
    assert company.deleted_at is not None and company.deleted_prev_expiry == "2099-01-01"
    assert db.query(Employee).filter(Employee.company_id == target.id).count() == 1
    # Sign-in and the existing session are both blocked.
    assert client.post("/api/v1/auth/login", json=login).status_code != 200
    assert client.get("/api/v1/auth/me", headers=staff).status_code != 200
    # Hidden from the company list, shown in the recycle bin.
    assert all(c["id"] != target.id for c in _rows(client.get("/api/v1/superadmin/companies", headers=headers).json()))
    bin_rows = client.get("/api/v1/superadmin/recycle-bin", headers=headers).json()
    row = next(c for c in bin_rows if c["id"] == target.id)
    assert row["days_left"] in (6, 7) and row["backup_key"] == key
    # Deleting it again is refused.
    again = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert again.status_code == 409

    # Restore puts it back with its old expiry; sign-in works again.
    assert client.post(f"/api/v1/superadmin/companies/{target.id}/restore", headers=headers).status_code == 200
    db.expire_all()
    company = db.get(Company, target.id)
    assert company.deleted_at is None and company.subscription_expires_at == "2099-01-01"
    assert client.post("/api/v1/auth/login", json=login).status_code == 200
    assert all(c["id"] != target.id for c in client.get("/api/v1/superadmin/recycle-bin", headers=headers).json())


def test_delete_refused_when_backup_fails(client, db, monkeypatch):
    """No backup, no delete: the company stays exactly as it was."""
    from app import storage

    _login_client_ref["client"] = client
    target = Company(name="Backup Fail Co", trn="TARGET-BACKUP-FAIL", subscription_expires_at="2099-01-01")
    db.add(target)
    db.commit()
    headers = _make_superadmin(db)

    def broken(*a, **k):
        raise OSError("bucket unreachable")
    monkeypatch.setattr(storage, "upload_backup_file", broken)
    r = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 202, r.text
    status = client.get(f"/api/v1/superadmin/companies/{target.id}/delete-status", headers=headers).json()
    assert status["status"] == "failed" and "nothing was deleted" in status["detail"], status
    db.expire_all()
    company = db.get(Company, target.id)
    assert company.deleted_at is None and company.subscription_expires_at == "2099-01-01"


def test_purge_keeps_company_when_final_backup_fails(client, db, monkeypatch):
    from app import storage, worker

    _login_client_ref["client"] = client
    target = Company(name="Purge Keep Co", trn="TARGET-PURGE-KEEP")
    db.add(target)
    db.commit()
    headers = _make_superadmin(db)
    r = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 202, r.text
    db.expire_all()
    db.get(Company, target.id).deleted_at = datetime.now(UTC) - timedelta(days=8)
    db.commit()

    def broken(*a, **k):
        raise OSError("bucket unreachable")
    monkeypatch.setattr(storage, "upload_backup_file", broken)
    result = worker.purge_recycle_bin()
    assert target.id in result["kept"] and target.id not in result["purged"]
    db.expire_all()
    assert db.get(Company, target.id) is not None


def test_purge_leaves_recently_deleted_companies(client, db):
    from app import worker

    _login_client_ref["client"] = client
    target = Company(name="Purge Recent Co", trn="TARGET-PURGE-RECENT")
    db.add(target)
    db.commit()
    headers = _make_superadmin(db)
    r = client.request("DELETE", f"/api/v1/superadmin/companies/{target.id}", headers=headers, json={"password": "test12345"})
    assert r.status_code == 202, r.text
    result = worker.purge_recycle_bin()
    assert target.id not in result["purged"]
    db.expire_all()
    assert db.get(Company, target.id) is not None
