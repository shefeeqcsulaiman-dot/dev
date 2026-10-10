"""Settings > Data > Fill demo data (POST /api/v1/demo-data): fills every area through the
app's own API as a background job, for company admins only, and a second run updates the
same records instead of adding copies."""
from uuid import uuid4

from app.models import AppDataRecord, Branch, Employee, Invoice, JournalEntry, LeaveRequest, PayrollRun, User
from tests.conftest import ensure_user


def _admin(client, db):
    tag = uuid4().hex[:8]
    email = f"demo-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"96{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _run(client, headers):
    r = client.post("/api/v1/demo-data", headers=headers)
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/app-data/jobs/{r.json()['job_id']}", headers=headers).json()
    assert job["status"] == "completed", job
    return job["result"]


def _count(db, company_id, collection):
    return db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection).count()


def test_fills_every_area(client, db):
    company_id, headers = _admin(client, db)
    result = _run(client, headers)
    assert result["failures"] == [], result["failures"]
    counts = result["counts"]
    assert counts["branches"] == 4 and counts["employees"] == 10
    db.expire_all()
    assert db.query(Branch).filter(Branch.company_id == company_id).count() == 4
    assert db.query(Employee).filter(Employee.company_id == company_id).count() == 10
    for collection, n in (("customers", 8), ("vendors", 6), ("products", 15), ("purchaseRecords", 10), ("bills", 4),
                          ("salesInvoices", 12), ("quotations", 4), ("expenses", 6), ("rotaShifts", 3), ("tasks", 6),
                          ("overtimeRequests", 4), ("employeeLoans", 1), ("salaryAdvances", 1), ("hrHolidays", 2)):
        assert _count(db, company_id, collection) == n, collection
    assert _count(db, company_id, "rotaAssignments") == 70
    assert _count(db, company_id, "payments") == 8
    assert db.query(LeaveRequest).filter(LeaveRequest.company_id == company_id).count() == 3
    assert db.query(PayrollRun).filter(PayrollRun.company_id == company_id).count() == 1
    assert db.query(JournalEntry).filter(JournalEntry.company_id == company_id).count() > 2  # postings + manual
    assert db.query(Invoice).filter(Invoice.company_id == company_id).count() == 12  # sales posted as real invoices


def test_second_run_updates_instead_of_duplicating(client, db):
    company_id, headers = _admin(client, db)
    _run(client, headers)
    first = {c: _count(db, company_id, c) for c in ("customers", "products", "salesInvoices", "employees", "payments")}
    result = _run(client, headers)
    db.expire_all()
    assert {c: _count(db, company_id, c) for c in first} == first
    assert db.query(Branch).filter(Branch.company_id == company_id).count() == 4
    assert db.query(PayrollRun).filter(PayrollRun.company_id == company_id).count() == 1
    assert result["failures"] == [], result["failures"]


def test_admins_only(client, db, auth_headers):
    from tests.test_ess_requests import _company_id, _ess_login

    _, ess_headers = _ess_login(client, db, auth_headers, _company_id(client, auth_headers),
                                f"DD-{uuid4().hex[:5]}", f"dd.{uuid4().hex[:6]}")
    assert client.post("/api/v1/demo-data", headers=ess_headers).status_code == 403
    assert client.post("/api/v1/demo-data").status_code == 401
    assert db.query(User).count() > 0
