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


def test_flattened_row_at_punch_absent_day_writes_absent_marker(client, auth_headers, db):
    """Exact case found live: Andrea's payload -- work_date given, every
    clock_in/out field null, raw_events empty. Must NOT record a false "in"
    punch at push-time (the bug), and instead write an explicit ABSENT
    marker row: no clock times, zero hours, session_count 0, raw_events []."""
    company_id = _company_id_helper(client, auth_headers)
    payload = _flattened_row(employee_id="72", employee_name="Andrea", work_date="2026-09-08")
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["inserted"] == 0
    assert body["events"] == 0
    assert body["absent_marked"] is True

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "72",
        AttendanceDetail.work_date == "2026-09-08",
    ).one()
    assert row.clock_in_1 is None
    assert row.clock_out_1 is None
    assert row.work_seconds_1 is None
    assert row.total_seconds == 0
    assert row.ot_seconds == 0
    assert row.under_seconds == 8 * 3600
    assert row.session_count == 0
    assert json.loads(row.raw_events) == []

    # Resending the same absent payload is a no-op (marker already there).
    resp2 = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp2.json()["absent_marked"] is False
    assert db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "72",
    ).count() == 1


def test_absent_marker_not_shown_as_present_in_today(client, auth_headers, db):
    company_id = _company_id_helper(client, auth_headers)
    client.post("/api/v1/attendance/punch", headers=auth_headers,
                json=_flattened_row(employee_id="74", employee_name="Marker Only", work_date="2026-09-08"))
    r = client.get("/api/v1/attendance/today?date=2026-09-08", headers=auth_headers)
    assert r.status_code == 200
    assert "74" not in r.json()["employee_ids"]


def test_absent_marker_upgraded_to_present_by_later_punch(client, auth_headers, db):
    """A real punch arriving after the absent marker replaces the NULLs
    with real times -- the row converges to present, no duplicate row.
    One "in" with no "out" is an in-progress first shift: clock_in_1 set
    (so "present, still on the clock" is visible) but session_count 0
    (no COMPLETE session yet)."""
    company_id = _company_id_helper(client, auth_headers)
    client.post("/api/v1/attendance/punch", headers=auth_headers,
                json=_flattened_row(employee_id="75", employee_name="Late Scan", work_date="2026-09-08"))
    client.post("/api/v1/attendance/punch", headers=auth_headers,
                json=_flattened_row(employee_id="75", employee_name="Late Scan", work_date="2026-09-08",
                                     clock_in_1="2026-09-08T08:00:00+04:00"))
    rows = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "75",
        AttendanceDetail.work_date == "2026-09-08",
    ).all()
    assert len(rows) == 1
    assert rows[0].clock_in_1 is not None
    assert rows[0].clock_out_1 is None
    assert rows[0].session_count == 0
    assert rows[0].total_seconds == 0


def test_trailing_unmatched_in_is_not_a_session(client, auth_headers, db):
    """Exact case from the Axl report: raw_events = [in, out, in] -- one
    complete session plus a trailing unmatched "in". Must produce
    session_count 1, clock_in_2/clock_out_2 NULL, total = the first
    session only. The trailing "in" stays in raw_events for audit."""
    company_id = _company_id_helper(client, auth_headers)
    payload = _flattened_row(
        employee_id="76", employee_name="Axl", work_date="2026-09-08",
        raw_events="[\"2026-09-08T06:55:07\", \"2026-09-08T15:50:27\", \"2026-09-08T16:14:07\"]",
    )
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "76",
        AttendanceDetail.work_date == "2026-09-08",
    ).one()
    assert row.session_count == 1
    assert row.clock_in_1 is not None
    assert row.clock_out_1 is not None
    assert row.clock_in_2 is None
    assert row.clock_out_2 is None
    assert row.work_seconds_2 is None
    # first session only: 06:55:07 -> 15:50:27 = 8h55m20s = 32120s
    assert row.total_seconds == 32120
    # all 3 raw events preserved for audit
    assert len(json.loads(row.raw_events)) == 3


def test_two_complete_sessions_still_produce_session_count_2(client, auth_headers, db):
    """The regression guard for the Axl fix: a genuine split shift
    (in, out, in, out) must still be session_count 2 with both slots."""
    company_id = _company_id_helper(client, auth_headers)
    payload = _flattened_row(
        employee_id="77", employee_name="Split Shift", work_date="2026-09-08",
        raw_events="[\"2026-09-08T06:55:00\", \"2026-09-08T15:50:00\", \"2026-09-08T16:14:00\", \"2026-09-08T18:00:00\"]",
    )
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "77",
        AttendanceDetail.work_date == "2026-09-08",
    ).one()
    assert row.session_count == 2
    assert row.clock_in_1 is not None and row.clock_out_1 is not None
    assert row.clock_in_2 is not None and row.clock_out_2 is not None


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
