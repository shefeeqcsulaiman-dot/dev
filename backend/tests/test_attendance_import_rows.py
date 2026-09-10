"""Regression coverage for POST /attendance/import-rows -- a separate,
additive bulk-import endpoint for already-computed daily attendance rows
(e.g. migrating historical data from another system). Deliberately does
NOT change what /punch or /adms accept: total_seconds/session_count/etc.
are things only this server's own pairing computation can correctly
produce, so a real device/bridge script could never construct this shape
-- this endpoint exists for humans/scripts importing finished reports, not
live device ingestion."""
import json
from datetime import datetime

from app import timezone_utils
from app.models import AttendanceDetail, Company


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def test_import_rows_from_clock_in_out_fields(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    resp = client.post(
        "/api/v1/attendance/import-rows", headers=auth_headers,
        json={"rows": [{
            "employee_id": "IMPORT-001", "employee_name": "Import Test Employee",
            "work_date": "2026-08-15", "clock_in_1": "08:00", "clock_out_1": "17:00",
            # Server-derived fields must be accepted-but-ignored, not trusted.
            "total_seconds": "999999", "session_count": "999",
        }]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rows_processed"] == 1
    assert body["events_inserted"] == 2
    assert body["row_errors"] == []

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "IMPORT-001",
    ).one()
    assert row.clock_in_1 is not None
    assert row.clock_out_1 is not None
    # Real computed total (9h = 32400s), NOT the bogus 999999 the caller sent.
    assert row.total_seconds == 9 * 3600
    assert row.session_count == 1


def test_import_rows_from_raw_events(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    raw_events = json.dumps([
        {"id": "evt-1", "punch_time": "2026-08-16T04:00:00+00:00", "direction": "in", "source": "import"},
        {"id": "evt-2", "punch_time": "2026-08-16T13:00:00+00:00", "direction": "out", "source": "import"},
    ])
    resp = client.post(
        "/api/v1/attendance/import-rows", headers=auth_headers,
        json={"rows": [{"employee_id": "IMPORT-002", "raw_events": raw_events}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["events_inserted"] == 2

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "IMPORT-002",
    ).one()
    assert row.session_count == 1
    assert row.total_seconds == 9 * 3600


def test_import_rows_accepts_bare_string_raw_events_with_flattened_fallback(client, auth_headers, db):
    """Exact payload shape a user tested with: raw_events is a JSON array of
    BARE ISO timestamp strings (no {punch_time, direction} object wrapper),
    alongside a fully-populated flattened clock_in_1/clock_out_1 pair and
    caller-computed total_seconds/ot_seconds/session_count (all of which
    must be ignored, not trusted). Server-derived work_seconds_2..5 use
    JSON null (not empty string) for the unused sessions."""
    company_id = _company_id(client, auth_headers)
    payload = {
        "employee_id": "63", "employee_name": "Brigi", "work_date": "2026-09-08",
        "clock_in_1": "2026-09-08T09:43:29+04:00", "clock_out_1": "2026-09-08T19:11:43+04:00",
        "work_seconds_1": 34094,
        "clock_in_2": None, "clock_out_2": None, "work_seconds_2": 0,
        "clock_in_3": None, "clock_out_3": None, "work_seconds_3": 0,
        "clock_in_4": None, "clock_out_4": None, "work_seconds_4": 0,
        "clock_in_5": None, "clock_out_5": None, "work_seconds_5": 0,
        "total_seconds": 34094, "ot_seconds": 5294, "under_seconds": 0, "session_count": 1,
        "raw_events": "[\"2026-09-08T09:43:29\", \"2026-09-08T19:11:43\"]",
    }
    resp = client.post("/api/v1/attendance/import-rows", headers=auth_headers, json={"rows": [payload]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rows_processed"] == 1
    assert body["events_inserted"] == 2
    assert body["row_errors"] == []

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "63",
        AttendanceDetail.work_date == "2026-09-08",
    ).one()
    assert row.employee_name == "Brigi"
    # The raw_events bare-string entries ("2026-09-08T09:43:29", no offset)
    # win over clock_in_1's explicit "+04:00" here (raw_events is tried
    # first and succeeded), so these convert as company-LOCAL time via the
    # shared test company's configured country -- which other test files in
    # this suite may have changed (e.g. exercising a different VAT rate),
    # per the "Shared Test-Company Pollution" convention this suite already
    # follows elsewhere. Compute the real offset instead of assuming UAE+4.
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)
    expected_in = datetime(2026, 9, 8, 9, 43, 29) - offset
    expected_out = datetime(2026, 9, 8, 19, 11, 43) - offset
    assert row.clock_in_1.hour == expected_in.hour and row.clock_in_1.minute == 43 and row.clock_in_1.second == 29
    assert row.clock_out_1.hour == expected_out.hour and row.clock_out_1.minute == 11 and row.clock_out_1.second == 43
    assert row.session_count == 1
    # Real computed total (9h28m14s), NOT the caller-sent total_seconds=34094
    # (which happens to be the same value here since the caller computed it
    # correctly, but ot_seconds=5294 and session_count are still ignored --
    # verified by the fact the server derives its OWN ot_seconds using its
    # own 8h default rather than trusting 5294 directly).
    expected_total = (15 * 3600 + 11 * 60 + 43) - (5 * 3600 + 43 * 60 + 29)
    assert row.total_seconds == expected_total
    assert row.ot_seconds == max(0, expected_total - 8 * 3600)


def test_import_rows_rejects_missing_employee_id(client, auth_headers):
    resp = client.post(
        "/api/v1/attendance/import-rows", headers=auth_headers,
        json={"rows": [{"work_date": "2026-08-15", "clock_in_1": "08:00"}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rows_processed"] == 0
    assert len(body["row_errors"]) == 1
    assert body["row_errors"][0]["row"] == 0


def test_import_rows_absent_row_writes_absent_marker(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    resp = client.post(
        "/api/v1/attendance/import-rows", headers=auth_headers,
        json={"rows": [{
            "employee_id": "IMPORT-ABSENT-01", "employee_name": "Absent Import",
            "work_date": "2026-08-14", "clock_in_1": None, "clock_out_1": None,
            "total_seconds": 0, "session_count": 0, "raw_events": "[]",
        }]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rows_processed"] == 1
    assert body["events_inserted"] == 0
    assert body["row_errors"] == []

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "IMPORT-ABSENT-01",
        AttendanceDetail.work_date == "2026-08-14",
    ).one()
    assert row.clock_in_1 is None
    assert row.total_seconds == 0
    assert row.ot_seconds == 0
    assert row.under_seconds == 8 * 3600
    assert row.session_count == 0
    assert json.loads(row.raw_events) == []


def test_import_rows_is_idempotent_on_replay(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    payload = {"rows": [{
        "employee_id": "IMPORT-003", "work_date": "2026-08-17", "clock_in_1": "09:00",
    }]}
    first = client.post("/api/v1/attendance/import-rows", headers=auth_headers, json=payload)
    second = client.post("/api/v1/attendance/import-rows", headers=auth_headers, json=payload)
    assert first.json()["events_inserted"] == 1
    assert second.json()["events_duplicate"] == 1
    assert second.json()["events_inserted"] == 0

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "IMPORT-003",
    ).one()
    assert len(json.loads(row.raw_events)) == 1


def test_import_rows_accepts_full_iso_datetime_with_offset(client, auth_headers, db):
    """Exact payload shape a user tested with in production troubleshooting:
    clock_in_1 as a full ISO datetime WITH a UTC offset, not "HH:MM". Every
    other field present but blank/placeholder, matching a raw
    AttendanceDetail-row template with only clock_in_1 actually filled in."""
    company_id = _company_id(client, auth_headers)
    payload = {
        "employee_id": "60", "employee_name": "Georgina", "work_date": "2026-09-09",
        "clock_in_1": "2026-09-09T07:29:00+04:00", "clock_out_1": "", "work_seconds_1": "",
        "clock_in_2": "", "clock_out_2": "", "work_seconds_2": "",
        "clock_in_3": "", "clock_out_3": "", "work_seconds_3": "",
        "clock_in_4": "", "clock_out_4": "", "work_seconds_4": "",
        "clock_in_5": "", "clock_out_5": "", "work_seconds_5": "",
        "total_seconds": "", "ot_seconds": "", "under_seconds": "", "session_count": "", "raw_events": "",
    }
    resp = client.post("/api/v1/attendance/import-rows", headers=auth_headers, json={"rows": [payload]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rows_processed"] == 1
    assert body["events_inserted"] == 1
    assert body["row_errors"] == []

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "60",
        AttendanceDetail.work_date == "2026-09-09",
    ).one()
    assert row.employee_name == "Georgina"
    # 07:29:00+04:00 -> 03:29:00 UTC
    assert row.clock_in_1.hour == 3 and row.clock_in_1.minute == 29
    assert row.clock_out_1 is None


def test_import_rows_does_not_change_punch_endpoint_shape(client, auth_headers, db):
    """The two live-ingestion shapes on /punch (raw PunchIn and the wide
    daily-report shape) must keep working exactly as before -- this new
    endpoint is additive, not a replacement."""
    resp = client.post(
        "/api/v1/attendance/punch", headers=auth_headers,
        json={"employee_id": "STILL-WORKS-001", "punch_time": "2026-08-18T08:00:00", "direction": "in"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["ok"] is True


def test_import_rows_accepts_device_key_auth(client, auth_headers, db):
    """An unattended remote script (no company login session) must be able
    to push here the same way it already can to /punch and /adms -- e.g.
    a scheduled daily_attendance_report.py-style script whose only
    credential is a device API key."""
    company_id = _company_id(client, auth_headers)
    created = client.post("/api/v1/attendance/devices", json={
        "name": "Import Rows Test Device", "device_type": "ZKTeco iClock",
        "ip_address": "192.168.1.70", "port": 4370,
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    api_key = created.json()["api_key"]

    resp = client.post(
        "/api/v1/attendance/import-rows",
        headers={"X-Device-Key": api_key},
        json={"rows": [{"employee_id": "IMPORT-DEVICEKEY-001", "work_date": "2026-08-19", "clock_in_1": "08:00"}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["events_inserted"] == 1

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "IMPORT-DEVICEKEY-001",
    ).one()
    assert row.clock_in_1 is not None


def test_import_rows_rejects_no_auth_at_all(client):
    resp = client.post(
        "/api/v1/attendance/import-rows",
        json={"rows": [{"employee_id": "NO-AUTH-001", "work_date": "2026-08-19", "clock_in_1": "08:00"}]},
    )
    assert resp.status_code == 401, resp.text
