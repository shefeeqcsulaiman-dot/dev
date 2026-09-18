"""Superadmin Download Backup: GET /superadmin/companies/{id}/db-dump (one
company) and GET /superadmin/companies/backup-all (zip of every company's
own restorable SQL file). Both reuse build_company_sql_dump(), the same
builder the company owner's own GET /app-data/db-dump now calls, so the
existing owner endpoint is covered here too as a regression check on the
refactor."""
import io
import zipfile

from app.models import Company, Employee, User
from app.security import hash_password

_client_ref = {}


def _make_superadmin(db):
    admin = db.query(User).filter(User.email == "superadmin-backup-test@etaxflow.com").first()
    if not admin:
        company = Company(name="ETaxFlow Backup Test Admin", trn="SUPERADMIN-BACKUP-TEST")
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, email="superadmin-backup-test@etaxflow.com",
                     full_name="Backup Super Admin", role="superadmin", password_hash=hash_password("test12345"))
        db.add(admin)
        db.commit()
    r = _client_ref["client"].post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_superadmin_can_download_any_single_company_backup(client, db):
    _client_ref["client"] = client
    target = Company(name="Backup Target Co", trn="BACKUP-TARGET-1")
    db.add(target)
    db.flush()
    db.add(Employee(company_id=target.id, employee_no="BK-001", full_name="Backup Employee"))
    db.commit()

    r = client.get(f"/api/v1/superadmin/companies/{target.id}/db-dump", headers=_make_superadmin(db))
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.sql"')
    body = r.text
    assert f"-- Company ID : {target.id}" in body
    assert "Superadmin (Backup Super Admin)" in body
    assert "BK-001" in body


def test_single_company_backup_404_for_unknown_company(client, db):
    _client_ref["client"] = client
    r = client.get("/api/v1/superadmin/companies/does-not-exist/db-dump", headers=_make_superadmin(db))
    assert r.status_code == 404


def test_backup_all_returns_zip_with_one_sql_file_per_company_and_excludes_internal_company(client, db):
    _client_ref["client"] = client
    a = Company(name="Zip Co A", trn="BACKUP-ZIP-A")
    b = Company(name="Zip Co B", trn="BACKUP-ZIP-B")
    db.add_all([a, b])
    db.flush()
    db.add(Employee(company_id=a.id, employee_no="ZA-1", full_name="Zip A Employee"))
    db.add(Employee(company_id=b.id, employee_no="ZB-1", full_name="Zip B Employee"))
    db.commit()
    # The seeded internal superadmin company (unique TRN) already exists in
    # the test DB; reuse it rather than colliding on a second insert.
    internal = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
    if not internal:
        internal = Company(name="Internal Superadmin Co", trn="SUPERADMIN-INTERNAL")
        db.add(internal)
        db.commit()

    r = client.get("/api/v1/superadmin/companies/backup-all", headers=_make_superadmin(db))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    a_files = [n for n in names if f"taxflow-db-{a.id[:8]}" in n]
    b_files = [n for n in names if f"taxflow-db-{b.id[:8]}" in n]
    internal_files = [n for n in names if f"taxflow-db-{internal.id[:8]}" in n]
    assert len(a_files) == 1 and len(b_files) == 1
    assert internal_files == []

    a_sql = zf.read(a_files[0]).decode("utf-8")
    b_sql = zf.read(b_files[0]).decode("utf-8")
    # Each company's file holds only its own data -- never a neighbour's.
    assert "ZA-1" in a_sql and "ZB-1" not in a_sql
    assert "ZB-1" in b_sql and "ZA-1" not in b_sql


def test_backup_endpoints_reject_non_superadmin(client, db, auth_headers):
    _client_ref["client"] = client
    target = Company(name="Backup Reject Target", trn="BACKUP-REJECT-1")
    db.add(target)
    db.commit()
    assert client.get(f"/api/v1/superadmin/companies/{target.id}/db-dump", headers=auth_headers).status_code == 403
    assert client.get("/api/v1/superadmin/companies/backup-all", headers=auth_headers).status_code == 403


def test_company_owner_own_backup_still_works_after_refactor(client, auth_headers):
    r = client.get("/api/v1/app-data/db-dump", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.text.startswith("-- TaxFlow Database Backup")
    assert "BEGIN;" in r.text and "COMMIT;" in r.text
