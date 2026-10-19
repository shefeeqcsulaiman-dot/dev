"""Super Admin > Restore from backup (app/backup_restore.py): a company's backup restores into
a new company that matches it (records, ledger, VAT, HR), shares no id with it, copies no
logins, and leaves the original untouched; anything that isn't the backup's own INSERTs is
refused before a company is created."""
import gzip
import io
import json
from uuid import uuid4

import pytest
from sqlalchemy import func

from app import backup_restore, demo_data
from app.models import (
    Account, AppDataRecord, AttendanceDetail, Company, Employee, GeneralLedgerEntry, Invoice, JournalEntry,
    JournalLine, LeaveRequest, Party, PayrollItem, PayrollRun, SourceTransaction, TaxLine, User,
)
from app.module_catalog import ALL_MODULES
from app.routers.app_data import iter_company_sql_dump
from tests.test_module_permissions import _make_restricted_company


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(backup_restore.time, "sleep", lambda s: None)
    monkeypatch.setattr(demo_data.time, "sleep", lambda s: None)

    # run the job in the request, so the test sees its result
    monkeypatch.setattr(backup_restore, "_spawn", lambda target, args: target(*args))


def _counts(db, cid):
    out = {}
    for model in (Employee, Invoice, JournalEntry, SourceTransaction, TaxLine, LeaveRequest, PayrollRun,
                  AttendanceDetail, GeneralLedgerEntry, Party, Account):
        out[model.__tablename__] = db.query(model).filter(model.company_id == cid).count()
    out["journal_lines"] = (db.query(JournalLine).join(JournalEntry, JournalLine.journal_id == JournalEntry.id)
                            .filter(JournalEntry.company_id == cid).count())
    out["payroll_items"] = (db.query(PayrollItem).join(PayrollRun, PayrollItem.run_id == PayrollRun.id)
                            .filter(PayrollRun.company_id == cid).count())
    for coll, n in (db.query(AppDataRecord.collection, func.count()).filter(AppDataRecord.company_id == cid)
                    .group_by(AppDataRecord.collection)):
        out[f"app:{coll}"] = n
    return out


def _trial_balance(db, cid):
    rows = (db.query(Account.code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalLine.journal_id == JournalEntry.id)
            .filter(JournalEntry.company_id == cid).group_by(Account.code))
    return {code: (dr, cr) for code, dr, cr in rows}


def _vat(db, cid):
    return {d: (t, x) for d, t, x in db.query(TaxLine.direction, func.sum(TaxLine.taxable_amount), func.sum(TaxLine.tax_amount))
            .filter(TaxLine.company_id == cid).group_by(TaxLine.direction)}


def _dump(db, cid) -> str:
    return "".join(iter_company_sql_dump(db, cid, "test"))


def _upload(client, sa, data: bytes, filename="backup.sql", email=None, name=""):
    email = email or f"restore-{uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/superadmin/restore-backup", headers=sa,
                    files={"file": (filename, io.BytesIO(data), "application/octet-stream")},
                    data={"admin_email": email, "admin_password": "restore123", "name": name})
    return r, email


def _job(client, sa, r):
    assert r.status_code == 200, r.text
    job = client.get(f"/api/v1/superadmin/restore-backup/{r.json()['job_id']}", headers=sa).json()
    return job


def test_restored_copy_matches_the_original(client, db):
    tag = uuid4().hex[:4]
    cid, _admin, sa = _make_restricted_company(client, db, f"rs-{tag}", sorted(ALL_MODULES))
    demo = {"id": f"d{tag}", "company_id": cid, "months": 1, "status": "running", "step": "", "log": [], "summary": {}}
    demo_data._run(demo)
    assert demo["status"] == "done", demo.get("error")
    db.expire_all()
    before = _counts(db, cid)
    tb, vat = _trial_balance(db, cid), _vat(db, cid)
    assert before["employees"] and before["invoices"] and before["tax_lines"] and before["leave_requests"]
    sql = _dump(db, cid)

    job = _job(client, sa, _upload(client, sa, gzip.compress(sql.encode()), "nightly.sql.gz")[0])
    assert job["status"] == "done", job
    new = job["company_id"]
    assert new and new != cid
    db.expire_all()
    copy = db.get(Company, new)
    assert copy.name.endswith(")") and "(restored " in copy.name
    assert copy.modules_enabled == db.get(Company, cid).modules_enabled

    after = _counts(db, new)
    # an exact copy (nothing missing, nothing seeded on top), plus the restore's own audit entry
    assert after == {**before, "app:audit": before.get("app:audit", 0) + 1}
    assert _trial_balance(db, new) == tb
    assert _vat(db, new) == vat
    assert _counts(db, cid) == before  # the original is untouched

    # nothing shared with the original, no logins copied
    def ids(model, company):
        return {i for (i,) in db.query(model.id).filter(model.company_id == company)}
    for model in (AppDataRecord, Employee, Invoice, JournalEntry, Account):
        assert not ids(model, cid) & ids(model, new), model.__tablename__
    assert db.query(Employee).filter(Employee.company_id == new, Employee.username.isnot(None)).count() == 0
    emp = db.query(Employee).filter(Employee.company_id == new).first()
    r = client.post("/api/v1/ess/login", json={"username": emp.employee_no, "password": emp.employee_no, "company_id": new})
    assert r.status_code == 401  # the employee number no longer works as a password in the copy

    # records read back the same (typed tables rebuilt), references point inside the copy
    def docs(company, collection):
        rows = db.query(AppDataRecord).filter(AppDataRecord.company_id == company, AppDataRecord.collection == collection)
        out = []
        for row in rows:
            doc = {k: v for k, v in json.loads(row.payload or "{}").items() if k not in backup_restore._EMPLOYEE_SECRET_KEYS}
            out.append(backup_restore._UUID.sub("U", json.dumps(doc, sort_keys=True, default=str)))
        return sorted(out)
    for collection in ("customers", "products", "salesInvoices", "hrHolidays", "employees", "rotaAssignments"):
        assert docs(new, collection) == docs(cid, collection), collection
    lr = db.query(LeaveRequest).filter(LeaveRequest.company_id == new).first()
    assert db.get(Employee, lr.employee_id).company_id == new

    # the new admin signs in and sees the data
    login = client.post("/api/v1/auth/login", json={"email": job_email(job, db), "password": "restore123"})
    assert login.status_code == 200, login.text
    token = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.get("/api/v1/app-data/records/customers", headers=token)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == before["app:customers"]

def job_email(job, db):
    return db.query(User.email).filter(User.company_id == job["company_id"], User.role == "admin").scalar()


def test_restoring_twice_makes_two_independent_copies(client, db):
    tag = uuid4().hex[:4]
    cid, admin, sa = _make_restricted_company(client, db, f"r2-{tag}", ["sales"])
    r = client.post("/api/v1/app-data", params={"action": "save"}, headers=admin,
                    json={"collection": "customers", "record": {"name": f"Cust {tag}", "trn": ""}})
    assert r.status_code == 200, r.text
    db.get(Company, cid).trn = f"TRN-{tag}"  # unique per company: the copies start without one
    db.commit()
    sql = _dump(db, cid).encode()
    a = _job(client, sa, _upload(client, sa, sql, name=f"Copy A {tag}")[0])
    b = _job(client, sa, _upload(client, sa, sql, name=f"Copy B {tag}")[0])
    assert a["status"] == b["status"] == "done", (a, b)
    assert a["company_id"] != b["company_id"]
    db.expire_all()
    assert db.get(Company, a["company_id"]).name == f"Copy A {tag}"
    for job in (a, b):
        assert db.get(Company, job["company_id"]).trn is None
        assert db.query(Party).filter(Party.company_id == job["company_id"]).count() == 1


@pytest.mark.parametrize("bad", [
    "DROP TABLE users;",
    "UPDATE users SET role = 'superadmin';",
    "INSERT INTO users (id, email) VALUES ('{id}', 'x@y.z') ON CONFLICT (id) DO NOTHING;",
    "INSERT INTO employees (id, company_id, employee_no, nope) VALUES ('{id}', '{cid}', 'E1', 'x') ON CONFLICT (id) DO NOTHING;",
    "INSERT INTO employees (id, company_id, employee_no) VALUES ('{id}', '{cid}', lower('E1')) ON CONFLICT (id) DO NOTHING;",
    "INSERT INTO employees (id, company_id, employee_no) VALUES ('{id}', '{cid}', 'E1') ON CONFLICT (id) DO UPDATE SET employee_no = 'x';",
])
def test_anything_but_the_backups_own_inserts_is_refused(client, db, bad):
    tag = uuid4().hex[:4]
    cid, _admin, sa = _make_restricted_company(client, db, f"rb-{tag}", ["hrms"])
    companies = db.query(Company).count()
    text = (f"-- TaxFlow Database Backup\n-- Company ID : {cid}\nBEGIN;\n"
            f"INSERT INTO employees (id, company_id, employee_no, full_name) VALUES ('{uuid4()}', '{cid}', 'OK1', 'Ok') ON CONFLICT (id) DO NOTHING;\n"
            + bad.format(id=uuid4(), cid=cid) + "\nCOMMIT;\n")
    job = _job(client, sa, _upload(client, sa, text.encode())[0])
    assert job["status"] == "error" and job["error"], job
    db.expire_all()
    assert db.query(Company).count() == companies


def test_quotes_and_backslashes_are_data(client, db):
    tag = uuid4().hex[:4]
    cid, _admin, sa = _make_restricted_company(client, db, f"rq-{tag}", ["hrms"])
    tricky = "O'Brien'); DELETE FROM users; --"
    text = (f"-- TaxFlow Database Backup\n-- Company ID : {cid}\nBEGIN;\n"
            f"INSERT INTO employees (id, company_id, employee_no, full_name, department) VALUES "
            f"('{uuid4()}', '{cid}', 'Q1', '{tricky.replace(chr(39), chr(39) * 2)}', E'Back\\\\slash\\\\') ON CONFLICT (id) DO NOTHING;\n"
            "COMMIT;\n")
    users = db.query(User).count()
    job = _job(client, sa, _upload(client, sa, text.encode())[0])
    assert job["status"] == "done", job
    db.expire_all()
    emp = db.query(Employee).filter(Employee.company_id == job["company_id"]).one()
    assert emp.full_name == tricky
    assert emp.department == "Back\\slash\\"
    assert db.query(User).count() == users + 1  # only the new admin


def test_a_failing_restore_leaves_no_company(client, db, monkeypatch):
    tag = uuid4().hex[:4]
    cid, admin, sa = _make_restricted_company(client, db, f"rf-{tag}", ["sales"])
    client.post("/api/v1/app-data", params={"action": "save"}, headers=admin,
                json={"collection": "customers", "record": {"name": f"Fail {tag}"}})
    sql = _dump(db, cid).encode()

    def boom(self):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(backup_restore._Restore, "finish_ledger", boom)
    companies, users = db.query(Company).count(), db.query(User).count()
    job = _job(client, sa, _upload(client, sa, sql)[0])
    assert job["status"] == "error" and "nothing was created" in job["error"]
    db.expire_all()
    assert (db.query(Company).count(), db.query(User).count()) == (companies, users)
    assert not backup_restore.restore_running()


def test_restore_is_super_admin_only_and_checks_the_form(client, db, auth_headers):
    tag = uuid4().hex[:4]
    _cid, _admin, sa = _make_restricted_company(client, db, f"ra-{tag}", ["sales"])
    r, _ = _upload(client, auth_headers, b"-- TaxFlow Database Backup\n")
    assert r.status_code == 403
    r, _ = _upload(client, sa, b"", email="x@example.com")
    assert r.status_code == 400
    taken = db.query(User.email).filter(User.role == "admin").first()[0]
    r, _ = _upload(client, sa, b"-- TaxFlow Database Backup\n", email=taken)
    assert r.status_code == 409
