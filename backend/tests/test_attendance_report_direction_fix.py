"""A day row pushed again by the remote report script fixes a punch whose direction was guessed
wrongly before (e.g. an evening check-out first stored as a check-in), instead of being dropped
as a duplicate; explicit clock_in/clock_out fields beat bare positional raw_events."""
import json
import uuid
from datetime import date, timedelta

from app.models import AttendanceDetail
from tests.test_attendance_flattened_row_at_punch import _company_id_helper, _flattened_row

DAY = (date.today() - timedelta(days=5)).isoformat()


def _device_key(client, auth_headers):
    r = client.post("/api/v1/attendance/devices", json={"name": f"Report PC {uuid.uuid4().hex[:4]}", "device_type": "ZKTeco iClock",
                                                        "ip_address": "192.168.1.90", "port": 4370}, headers=auth_headers)
    assert r.status_code == 201, r.text
    return {"X-Device-Key": r.json()["api_key"]}


def _row(db, cid, emp):
    db.expire_all()
    return db.query(AttendanceDetail).filter(AttendanceDetail.company_id == cid, AttendanceDetail.employee_id == emp).one()


def test_full_day_push_corrects_evening_scan_stored_as_check_in(client, auth_headers, db):
    cid = _company_id_helper(client, auth_headers)
    key = _device_key(client, auth_headers)
    emp = f"R{uuid.uuid4().hex[:5]}"
    # earlier partial push: only the evening scan, positional -> taken as a check-in
    partial = _flattened_row(employee_id=emp, work_date=DAY, raw_events=json.dumps([f"{DAY}T19:45:14"]))
    assert client.post("/api/v1/adms", headers=key, json=partial).json()["inserted"] == 1
    assert _row(db, cid, emp).clock_out_1 is None

    full = _flattened_row(employee_id=emp, employee_name="Ashleigh", work_date=DAY,
                          clock_in_1=f"{DAY}T09:34:57+04:00", clock_out_1=f"{DAY}T19:45:14+04:00",
                          raw_events=json.dumps([f"{DAY}T09:34:57", f"{DAY}T19:45:14"]))
    body = client.post("/api/v1/adms", headers=key, json=full).json()
    assert body["inserted"] == 1 and body["corrected"] == 1, body
    row = _row(db, cid, emp)
    assert row.clock_in_1 is not None and row.clock_out_1 is not None and row.session_count == 1

    # pushing the same day again changes nothing
    again = client.post("/api/v1/adms", headers=key, json=full).json()
    assert again["duplicates"] == 2 and again["corrected"] == 0


def test_explicit_clock_fields_beat_positional_raw_events(client, auth_headers, db):
    cid = _company_id_helper(client, auth_headers)
    key = _device_key(client, auth_headers)
    emp = f"R{uuid.uuid4().hex[:5]}"
    # raw_events lists only the evening scan, but clock_out_1 says it is a check-out
    row = _flattened_row(employee_id=emp, work_date=DAY, clock_out_1=f"{DAY}T19:45:14+04:00",
                         raw_events=json.dumps([f"{DAY}T19:45:14"]))
    assert client.post("/api/v1/adms", headers=key, json=row).json()["inserted"] == 1
    events = json.loads(_row(db, cid, emp).raw_events)
    assert [e["direction"] for e in events] == ["out"]
