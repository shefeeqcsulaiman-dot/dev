"""Regression coverage for DELETE /attendance/punches/{id}. Previously there
was no way to remove a single erroneous punch (a mis-scanned device event,
a typo'd employee_id, a diagnostic/test punch) short of a superadmin wiping
a company's entire attendance history.

Since the attendance_details migration, a punch's "id" is the individual
scan event's id inside its day-row's raw_events JSON (not an AttendancePunch
row id) -- seeded here via attendance_store.upsert_attendance_event(), whose
return value carries that event_id."""
import json
from datetime import datetime, timezone

from app import attendance_store
from app.models import AttendanceDetail


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def _seed_punch(db, company_id, employee_id, punch_date_day):
    result = attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=employee_id,
        punch_time=datetime(2026, 8, punch_date_day, 8, 0, tzinfo=timezone.utc),
        direction="in", source="device",
    )
    return result["event_id"]


def test_delete_punch_removes_it(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    event_id = _seed_punch(db, company_id, "__diag_test__", 28)

    r = client.delete(f"/api/v1/attendance/punches/{event_id}", headers=auth_headers)
    assert r.status_code == 204, r.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "__diag_test__",
    ).first()
    # The row's only event was deleted, so the whole day-row is removed too.
    assert row is None


def test_delete_punch_404_for_unknown_id(client, auth_headers):
    r = client.delete("/api/v1/attendance/punches/does-not-exist", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_delete_punch_scoped_to_own_company(client, db, auth_headers, second_tenant_headers):
    company_id = _company_id(client, auth_headers)
    event_id = _seed_punch(db, company_id, "__diag_test__2", 28)

    # A different company's admin must not be able to delete this punch.
    r = client.delete(f"/api/v1/attendance/punches/{event_id}", headers=second_tenant_headers)
    assert r.status_code == 404, r.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "__diag_test__2",
    ).first()
    assert row is not None
    assert event_id in [e["id"] for e in json.loads(row.raw_events)]


def test_delete_punch_leaves_other_events_in_same_day_intact(client, db, auth_headers):
    """Deleting one event must not remove sibling events (e.g. a checkout)
    recorded on the same day-row."""
    company_id = _company_id(client, auth_headers)
    in_result = attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id="__diag_test__3",
        punch_time=datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc),
        direction="in", source="device",
    )
    attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id="__diag_test__3",
        punch_time=datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        direction="out", source="device",
    )

    r = client.delete(f"/api/v1/attendance/punches/{in_result['event_id']}", headers=auth_headers)
    assert r.status_code == 204, r.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "__diag_test__3",
    ).first()
    assert row is not None
    remaining = json.loads(row.raw_events)
    assert len(remaining) == 1
    assert remaining[0]["direction"] == "out"
