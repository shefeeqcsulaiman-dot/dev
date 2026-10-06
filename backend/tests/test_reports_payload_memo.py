"""Within one report build each collection is parsed once; callers still get their own copies."""
import uuid

from app.routers import reports
from tests.test_attendance_monthly_report import _company_id


def test_memo_reads_once_and_hands_out_copies(client, db, auth_headers, monkeypatch):
    cid = _company_id(client, auth_headers)
    ref = f"MEMO-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/v1/app-data?action=save", headers=auth_headers, json={"collection": "bills", "record": {"ref": ref, "total": 10}})
    assert r.status_code == 200, r.text

    loads = []
    real = reports._load_payloads_with_branch
    monkeypatch.setattr(reports, "_load_payloads_with_branch", lambda *a: loads.append(a[2]) or real(*a))
    token = reports._payload_memo.set({})
    try:
        first = reports.app_data_payloads(db, cid, "bills")
        next(row for row in first if row.get("ref") == ref)["total"] = 999
        second = reports.app_data_payloads(db, cid, "bills")
    finally:
        reports._payload_memo.reset(token)
    assert loads == ["bills"]
    assert next(row for row in second if row.get("ref") == ref)["total"] == 10
