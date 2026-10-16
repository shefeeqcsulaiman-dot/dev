"""Super Admin "Fill with demo data" (app/demo_data.py): two months for a fresh company fill
every area through the app's own API, adds without overwriting, and needs the company name."""
from uuid import uuid4

from app import demo_data
from app.module_catalog import ALL_MODULES
from app.models import AppDataRecord, AttendanceDetail, Employee, Invoice, LeaveRequest, PayrollRun
from tests.test_module_permissions import _make_restricted_company


def test_demo_data_needs_the_company_name(client, db):
    cid, _, sa = _make_restricted_company(client, db, f"dd-{uuid4().hex[:4]}", ["sales"])
    r = client.post(f"/api/v1/superadmin/companies/{cid}/demo-data", headers=sa, json={"months": 1, "confirm_name": "wrong"})
    assert r.status_code == 400


def test_two_months_of_demo_data_fill_every_area(client, db, monkeypatch):
    monkeypatch.setattr(demo_data.time, "sleep", lambda s: None)
    tag = uuid4().hex[:4]
    cid, admin_headers, _ = _make_restricted_company(client, db, f"dm-{tag}", sorted(ALL_MODULES))
    job = {"id": f"t{tag}", "company_id": cid, "months": 2, "status": "running", "step": "", "log": [], "summary": {}}
    demo_data._run(job)
    assert job["status"] == "done", job.get("error")

    def count(collection):
        return db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == collection).count()

    db.expire_all()
    assert db.query(Employee).filter(Employee.company_id == cid).count() >= 20
    assert db.query(Employee).filter(Employee.company_id == cid, Employee.username.isnot(None)).count() >= 20  # ESS logins
    assert count("rotaAssignments") > 100
    assert db.query(AttendanceDetail).filter(AttendanceDetail.company_id == cid).count() > 50
    assert db.query(LeaveRequest).filter(LeaveRequest.company_id == cid).count() > 0
    for collection in ("customers", "vendors", "products", "quotations", "salesInvoices", "purchaseRecords", "payments", "expenses"):
        assert count(collection) > 0, collection
    assert db.query(Invoice).filter(Invoice.company_id == cid).count() > 10
    assert db.query(PayrollRun).filter(PayrollRun.company_id == cid).count() >= 1

    # a second run only adds: same customers aren't duplicated, nothing is overwritten
    customers = count("customers")
    job2 = {**job, "id": f"u{tag}", "log": [], "summary": {}}
    demo_data._run(job2)
    assert job2["status"] == "done", job2.get("error")
    db.expire_all()
    assert count("customers") == customers
