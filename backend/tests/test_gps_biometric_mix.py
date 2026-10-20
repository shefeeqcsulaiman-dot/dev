"""Biometric device + GPS check-in together (2026-10-20):

A. ESS knows about a device check-in: it shows "checked in", refuses a second phone check-in,
   and the phone can check out a day started on the device.
B. Two check-outs (phone, then device, or the reverse): the last one ends the day.
C. A device scan with no in/out a few minutes after a GPS check-in is a re-scan, not a check-out.
D. Check-in method "Biometric only" turns phone check-in off."""
from datetime import UTC, datetime, timedelta

from app import attendance_store
from app.attendance_store import _pair_day_punches
from app.models import AttendanceDetail
from tests.test_gps_checkin import CLINIC, _checkin, _employee_with_sites


def _device(db, emp, when, direction):
    attendance_store.upsert_attendance_event(db, company_id=emp.company_id, employee_id=emp.employee_no,
                                             punch_time=when, direction=direction, device_id="dev-1",
                                             device_name="Front door", source="device")


def _today_row(db, emp):
    db.expire_all()
    return db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == emp.employee_no).order_by(AttendanceDetail.work_date.desc()).first()


def test_device_check_in_shows_in_ess_and_phone_can_check_out(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    _device(db, emp, datetime.now(UTC) - timedelta(minutes=30), "in")

    status = client.get("/api/v1/hr/dashboard", headers=h).json()
    assert status["checked_in"] is True and status["check_in_source"] == "device" and status["check_in_at"]

    again = _checkin(client, h, CLINIC)
    assert again.status_code == 409 and "attendance device" in again.json()["detail"]

    out = client.post("/api/v1/hr/check-out", headers=h, json={"latitude": CLINIC[0], "longitude": CLINIC[1]})
    assert out.status_code == 200, out.text
    row = _today_row(db, emp)
    assert row.clock_in_1 is not None and row.clock_out_1 is not None and row.total_seconds > 0
    assert client.get("/api/v1/hr/dashboard", headers=h).json()["checked_in"] is False


def test_the_last_check_out_ends_the_day():
    t = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)
    pairs = _pair_day_punches([(t, "in"), (t + timedelta(hours=8, minutes=50), "out"), (t + timedelta(hours=9), "out")])
    assert pairs == [(t, t + timedelta(hours=9))]
    # A real second session is unchanged
    pairs = _pair_day_punches([(t, "in"), (t + timedelta(hours=4), "out"), (t + timedelta(hours=5), "in"), (t + timedelta(hours=9), "out")])
    assert pairs == [(t, t + timedelta(hours=4)), (t + timedelta(hours=5), t + timedelta(hours=9))]
    # An out with no session before it in the day still starts nothing (overnight shifts)
    assert _pair_day_punches([(t, "out")]) == []


def test_direction_less_scan_next_to_gps_is_a_rescan():
    t = datetime(2026, 10, 20, 4, 50, tzinfo=UTC)
    events = [(t, "in"), (t + timedelta(minutes=6), "unknown"), (t + timedelta(hours=9), "unknown"),
              (t + timedelta(hours=9, minutes=5), "unknown")]
    assert _pair_day_punches(events) == [(t, t + timedelta(hours=9, minutes=5))]
    # Without GPS: alternate in / out
    assert _pair_day_punches([(t, "unknown"), (t + timedelta(hours=9), "unknown")]) == [(t, t + timedelta(hours=9))]


def test_biometric_only_turns_phone_check_in_off(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    try:
        r = client.put("/api/v1/hr/attendance-method", headers=auth_headers, json={"mode": "biometric"})
        assert r.status_code == 200 and r.json()["mode"] == "biometric"
        assert client.get("/api/v1/hr/attendance-method", headers=h).json()["mode"] == "biometric"
        assert client.get("/api/v1/hr/dashboard", headers=h).json()["gps_enabled"] is False
        blocked = _checkin(client, h, CLINIC)
        assert blocked.status_code == 403 and "biometric" in blocked.json()["detail"]
        assert client.put("/api/v1/hr/attendance-method", headers=auth_headers, json={"mode": "sometimes"}).status_code == 422
    finally:
        client.put("/api/v1/hr/attendance-method", headers=auth_headers, json={"mode": "both"})
    assert _checkin(client, h, CLINIC).status_code == 200
