"""Regression coverage for the wide daily-report payload shape ({"Date":
"DD/MM/YYYY", "Clock In 1": "HH:MM", ...}) that older/locally-edited copies
of daily_attendance_report.py push to /api/v1/adms (aliased to
/attendance/punch here). Before this, PunchIn silently ignored every field
it didn't recognize, defaulted punch_time to "now" (the push time, not the
real scan time), and inserted a punch for every row regardless of whether
the employee actually clocked in — an absent employee's blank "Clock In 1"
row still became a false "in" punch."""
import json

from app import timezone_utils
from app.models import AttendanceDetail, Company


def _wide_payload(employee_id="60", date="07/09/2026", clock_in_1="07:29", clock_out_1=""):
    return {
        "employee_id": employee_id, "timestamp": f"{date.split('/')[2]}-{date.split('/')[1]}-{date.split('/')[0]}T{clock_in_1 or '00:00'}:00",
        "Emp No.": employee_id, "AC-No.": employee_id, "Day": "MON", "Name": "Georgina", "Date": date,
        "Clock In 1": clock_in_1, "Clock Out 1": clock_out_1, "Work Time 1": "",
        "Clock In 2": "", "Clock Out 2": "", "Work Time 2": "",
        "Clock In 3": "", "Clock Out 3": "", "Work Time 3": "",
        "Clock In 4": "", "Clock Out 4": "", "Work Time 4": "",
        "Clock In 5": "", "Clock Out 5": "", "Work Time 5": "",
        "Total in time": "00:00", "OT": "00:00", "Under Time": "08:00",
        "Absent": "", "SICK": "", "Holiday": "",
    }


def test_wide_payload_records_real_scan_time_not_push_time(client, auth_headers, db):
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=_wide_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["inserted"] == 1
    assert body["events"] == 1

    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "60").order_by(AttendanceDetail.work_date.desc()).first()
    assert row is not None
    assert row.clock_in_1 is not None

    # Don't assume UAE/+4: the "qa-admin" company is shared across the whole
    # test session (see conftest.ensure_user), and other test files may have
    # changed its country. Compute the real expected offset instead of
    # hardcoding it, so this doesn't flake under full-suite ordering.
    country = db.query(Company.country).filter(Company.id == row.company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)
    expected_utc_minutes = (7 * 60 + 29) - int(offset.total_seconds() // 60)
    expected_hour, expected_minute = divmod(expected_utc_minutes % (24 * 60), 60)
    # 07:29 local -> expected_hour:expected_minute UTC, NOT "now" (the old bug).
    assert row.clock_in_1.hour == expected_hour and row.clock_in_1.minute == expected_minute


def test_wide_payload_absent_row_creates_no_punch(client, auth_headers, db):
    """A row with no Clock In/Out at all (an absent day) must not register
    as a present employee — this was the second half of the bug class."""
    resp = client.post("/api/v1/attendance/punch", headers=auth_headers, json=_wide_payload(employee_id="61", clock_in_1="", clock_out_1=""))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["inserted"] == 0
    assert body["events"] == 0
    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "61").first()
    assert row is None


def test_wide_payload_records_both_clock_in_and_out(client, auth_headers, db):
    resp = client.post(
        "/api/v1/attendance/punch", headers=auth_headers,
        json=_wide_payload(employee_id="62", clock_in_1="08:00", clock_out_1="17:00"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 2
    assert body["events"] == 2
    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "62").first()
    assert row is not None
    assert row.clock_in_1 is not None
    assert row.clock_out_1 is not None


def test_wide_payload_replay_is_idempotent(client, auth_headers, db):
    payload = _wide_payload(employee_id="63", clock_in_1="09:15", clock_out_1="")
    first = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    second = client.post("/api/v1/attendance/punch", headers=auth_headers, json=payload)
    assert first.json()["inserted"] == 1
    assert second.json()["duplicates"] == 1
    assert second.json()["inserted"] == 0
    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "63").first()
    assert row is not None
    events = json.loads(row.raw_events)
    assert len(events) == 1
