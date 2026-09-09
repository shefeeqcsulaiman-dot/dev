"""Regression coverage for POST /attendance/import-rows -- a separate,
additive bulk-import endpoint for already-computed daily attendance rows
(e.g. migrating historical data from another system). Deliberately does
NOT change what /punch or /adms accept: total_seconds/session_count/etc.
are things only this server's own pairing computation can correctly
produce, so a real device/bridge script could never construct this shape
-- this endpoint exists for humans/scripts importing finished reports, not
live device ingestion."""
import json

from app.models import AttendanceDetail


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
