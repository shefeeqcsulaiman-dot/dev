"""GET /attendance/today used to only ever answer for the current day —
the Attendance Calendar previously had no click handler on any day cell at
all, so there was no caller needing an arbitrary date. Widened with an
optional `date` param so clicking a calendar day can show that day's real
roll call."""
from datetime import datetime, timezone

from app import attendance_store


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def test_today_endpoint_accepts_explicit_past_date(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id="ATT-DATE-001", employee_name="Date Param Test",
        punch_time=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
        direction="in", source="device",
    )

    r = client.get("/api/v1/attendance/today?date=2026-08-20", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["date"] == "2026-08-20"
    assert data["present_count"] == 1
    assert data["employee_ids"] == ["ATT-DATE-001"]

    # A different date with no punches must not leak the other day's row.
    r2 = client.get("/api/v1/attendance/today?date=2026-08-21", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["present_count"] == 0


def test_today_endpoint_rejects_bad_date_format(client, auth_headers):
    r = client.get("/api/v1/attendance/today?date=not-a-date", headers=auth_headers)
    assert r.status_code == 400, r.text
