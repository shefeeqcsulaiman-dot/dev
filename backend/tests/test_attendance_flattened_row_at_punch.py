"""Regression coverage for the flattened-row payload shape (work_date,
clock_in_1..5, raw_events -- an AttendanceDetail-export shape) arriving at
the DEVICE webhook path (/punch, /adms) rather than /attendance/import-rows.

Found live in production: a remote script was updated to send this shape
(matching what /attendance/import-rows expects) but never had its URL
changed away from /api/v1/adms. Before this fix, PunchIn silently ignored
every field it didn't recognize (work_date, clock_in_1, raw_events, ...),
defaulted punch_time to "now", and inserted a punch regardless of whether
the row represented a real clock-in or a genuinely absent day -- the exact
same bug class the wide-report-shape handling elsewhere in this file
already exists to avoid, just triggered by a different payload shape."""
import json
from datetime import datetime

from app import timezone_utils
from app.models import AttendanceDetail, Company


def _flattened_row(employee_id="70", employee_name="Test Employee", work_date="2026-09-08",
                    clock_in_1=None, clock_out_1=None, raw_events=""):
    return {
        "employee_id": employee_id, "employee_name": employee_name, "work_date": work_date,
        "clock_in_1": clock_in_1, "clock_out_1": clock_out_1, "work_seconds_1": 0,
        "clock_in_2": None, "clock_out_2": None, "work_seconds_2": 0,
        "clock_in_3": None, "clock_out_3": None, "work_seconds_3": 0,
        "clock_in_4": None, "clock_out_4": None, "work_seconds_4": 0,
        "clock_in_5": None, "clock_out_5": None, "work_seconds_5": 0,
        "total_seconds": 0, "ot_seconds": 0, "under_seconds": 28800, "session_count": 0,
        "raw_events": raw_events,
    }


def test_flattened_row_at_punch_records_real_time_not_push_time(client, auth_headers, db):
    """Exact case found live: Oliva-style payload with a real ISO+offset
    clock_in_1/clock_out_1 pair, sent to /attendance/punch (aliases /adms)."""
    company_id = _company_id_helper(client, auth_headers)
    payload = _flattened_row(
        employee_id="71", employee_name="Oliva",
        clock_in_1="2026-09-08T09:29:55+04:00", clock_out_1="2026-09-08T19:03:03+04:00",
        raw_events="[\"2026-09-08T09:29:55\", \"2026-09-08T19:03:03\"]",
    )
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["inserted"] == 2
    assert body["events"] == 2

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "71",
        AttendanceDetail.work_date == "2026-09-08",
    ).one()
    # The raw_events bare-string entries ("2026-09-08T09:29:55", no offset)
    # win over clock_in_1's explicit "+04:00" (raw_events is tried first
    # and succeeds), so these convert as company-LOCAL time -- which other
    # test files in this suite may have changed (shared test company; see
    # the "Shared Test-Company Pollution" convention this suite follows).
    # Compute the real offset instead of assuming UAE+4. NOT "now" (the bug
    # this fix avoids), and NOT misfiled onto today's date the way the old
    # PunchIn fallback did.
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)
    expected_in = datetime(2026, 9, 8, 9, 29, 55) - offset
    assert row.clock_in_1.hour == expected_in.hour and row.clock_in_1.minute == 29 and row.clock_in_1.second == 55
    assert row.clock_out_1 is not None


def test_flattened_row_at_punch_absent_day_creates_no_punch(client, auth_headers, db):
    """Exact case found live: Andrea's payload -- work_date given, every
    clock_in/out field null, raw_events empty. Must record NOTHING, not a
    false "in" punch at push-time (the actual bug reported in production)."""
    company_id = _company_id_helper(client, auth_headers)
    payload = _flattened_row(employee_id="72", employee_name="Andrea")
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["inserted"] == 0
    assert body["events"] == 0

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "72",
    ).first()
    assert row is None


def test_flattened_row_at_punch_via_device_key(client, auth_headers, db):
    """The actual production path: an X-Device-Key-authenticated request
    (no company login session), same as the real remote script uses."""
    company_id = _company_id_helper(client, auth_headers)
    created = client.post("/api/v1/attendance/devices", json={
        "name": "Flattened Row Test Device", "device_type": "ZKTeco iClock",
        "ip_address": "192.168.1.80", "port": 4370,
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    api_key = created.json()["api_key"]

    payload = _flattened_row(employee_id="73", employee_name="Junior", clock_in_1="2026-09-08T07:34:39+04:00")
    resp = client.post("/api/v1/attendance/punch", headers={"X-Device-Key": api_key}, json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["inserted"] == 1

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "73",
    ).one()
    assert row.clock_in_1 is not None
    events = json.loads(row.raw_events)
    assert events[0]["source"] == "device"
    assert events[0]["device_name"] == "Flattened Row Test Device"


def _company_id_helper(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]
