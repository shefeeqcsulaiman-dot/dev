"""GPS check-in (ESS > GPS Check-In, /hr/check-in /check-out /location):

- a check-in/out is written into the attendance records (ESS history, reports, payroll);
- the employee may check in at whichever assigned active site they are at;
- weak GPS readings, out-of-range and 0,0 coordinates are refused;
- leaving the site with the app open checks out at the moment they left, and a session
  forgotten for 16+ hours no longer blocks the next check-in;
- the company admin's own HRMS login manages locations and sees live attendance."""
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import AttendanceDetail, AttendanceSession, CompanyLocation, EmployeeLocation, EmployeeLocationLog
from tests.test_ess_requests import _company_id, _ess_login

CLINIC = (25.2048, 55.2708)       # Dubai
NEAR_CLINIC = (25.2052, 55.2711)  # ~50 m away
FAR = (25.2600, 55.3000)          # ~6.8 km away
MARINA = (25.0800, 55.1400)       # second clinic, ~19 km away


def _uid():
    return uuid.uuid4().hex[:6]


def _employee_with_sites(client, db, auth_headers, sites, statuses=None):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, f"GPS-{_uid()}", f"gps.{_uid()}")
    for i, (lat, lng) in enumerate(sites):
        loc = CompanyLocation(company_id=cid, location_name=f"Clinic {i}", latitude=lat, longitude=lng,
                              allowed_radius_meters=200, status=(statuses or {}).get(i, "active"))
        db.add(loc)
        db.flush()
        db.add(EmployeeLocation(employee_id=emp.id, location_id=loc.id, is_primary=(i == 0)))
    db.commit()
    return cid, emp, h


def _checkin(client, h, point, accuracy=12):
    body = {"latitude": point[0], "longitude": point[1]}
    if accuracy is not None:
        body["accuracy"] = accuracy
    return client.post("/api/v1/hr/check-in", headers=h, json=body)


def _gps_events(client, h):
    return [e for e in client.get("/api/v1/ess/attendance", headers=h).json() if e["source"] == "gps"]


def test_check_in_and_out_reach_attendance(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    r = _checkin(client, h, NEAR_CLINIC)
    assert r.status_code == 200, r.text
    assert [e["direction"] for e in _gps_events(client, h)] == ["in"]

    r = client.post("/api/v1/hr/check-out", headers=h, json={"latitude": CLINIC[0], "longitude": CLINIC[1]})
    assert r.status_code == 200, r.text
    assert sorted(e["direction"] for e in _gps_events(client, h)) == ["in", "out"]

    today = client.get("/api/v1/attendance/today", headers=auth_headers)
    assert today.status_code == 200 and emp.employee_no in today.text


def test_outside_every_site_is_refused_and_nothing_recorded(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    r = _checkin(client, h, FAR)
    assert r.status_code == 403 and "Outside company location" in r.text and "Clinic 0" in r.text
    assert _gps_events(client, h) == []


def test_any_assigned_site_works_not_only_the_primary(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC, MARINA])
    r = _checkin(client, h, MARINA)
    assert r.status_code == 200, r.text
    assert r.json()["location_name"] == "Clinic 1"


def test_inactive_site_is_not_used(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC, MARINA], statuses={1: "inactive"})
    assert _checkin(client, h, MARINA).status_code == 403


def test_weak_or_missing_accuracy_is_refused(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    r = _checkin(client, h, CLINIC, accuracy=5000)
    assert r.status_code == 422 and "GPS signal too weak" in r.text
    r = _checkin(client, h, CLINIC, accuracy=None)
    assert r.status_code == 422 and "GPS signal too weak" in r.text
    # limit = the larger of 100 m and the site's radius (200 m here)
    assert _checkin(client, h, CLINIC, accuracy=250).status_code == 422
    assert _checkin(client, h, CLINIC, accuracy=150).status_code == 200


def test_invalid_coordinates_are_refused(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, (999, 999)).status_code == 422
    assert _checkin(client, h, (25.2, 181)).status_code == 422
    r = _checkin(client, h, (0, 0))
    assert r.status_code == 422 and "did not report a location" in r.text


def test_leaving_the_site_checks_out_at_the_moment_they_left(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, CLINIC).status_code == 200
    session = db.query(AttendanceSession).filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open").one()
    # checked in 20 minutes ago (inside), seen outside from 8 minutes ago
    for log in db.query(EmployeeLocationLog).filter(EmployeeLocationLog.session_id == session.id):
        log.created_at = datetime.now(UTC) - timedelta(minutes=20)
    session.check_in = datetime.now(UTC) - timedelta(minutes=20)
    left_at = datetime.now(UTC) - timedelta(minutes=8)
    db.add(EmployeeLocationLog(company_id=emp.company_id, employee_id=emp.id, session_id=session.id,
                               latitude=Decimal(str(FAR[0])), longitude=Decimal(str(FAR[1])), inside_geofence=False,
                               created_at=left_at))
    db.commit()

    r = client.post("/api/v1/hr/location", headers=h, json={"latitude": FAR[0], "longitude": FAR[1], "accuracy": 10})
    assert r.status_code == 200 and r.json()["auto_checked_out"] is True, r.text
    db.expire_all()
    closed = db.get(AttendanceSession, session.id)
    check_out = closed.check_out if closed.check_out.tzinfo else closed.check_out.replace(tzinfo=UTC)
    assert abs((check_out - left_at).total_seconds()) < 2
    stored = [e for row in db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == emp.employee_no)
              for e in json.loads(row.raw_events or "[]") if e.get("source") == "gps"]
    assert sorted(e["direction"] for e in stored) == ["in", "out"]
    out = next(e for e in stored if e["direction"] == "out")
    assert datetime.fromisoformat(out["punch_time"]).astimezone(UTC) == check_out


def test_forgotten_session_does_not_block_the_next_check_in(client, db, auth_headers):
    cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    db.add(AttendanceSession(company_id=cid, employee_id=emp.id, status="open",
                             check_in=datetime.now(UTC) - timedelta(hours=20)))
    db.commit()
    r = _checkin(client, h, CLINIC)
    assert r.status_code == 200, r.text
    assert _checkin(client, h, CLINIC).status_code == 409   # a fresh session still blocks a second check-in


def test_admin_login_manages_locations_and_sees_live_attendance(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    r = client.post("/api/v1/hr/company-locations", headers=auth_headers, json={
        "location_name": f"Admin Clinic {_uid()}", "latitude": CLINIC[0], "longitude": CLINIC[1], "allowed_radius_meters": 150})
    assert r.status_code == 201, r.text
    loc_id = r.json()["id"]
    assert any(loc["id"] == loc_id for loc in client.get("/api/v1/hr/company-locations", headers=auth_headers).json())

    emp, h = _ess_login(client, db, auth_headers, cid, f"GPSA-{_uid()}", f"gpsa.{_uid()}")
    r = client.post("/api/v1/hr/employee-locations", headers=auth_headers, json={"employee_id": emp.id, "location_id": loc_id})
    assert r.status_code == 201, r.text
    assert any(a["employee_id"] == emp.id for a in client.get("/api/v1/hr/employee-locations", headers=auth_headers).json())

    assert _checkin(client, h, CLINIC).status_code == 200
    live = client.get("/api/v1/hr/live-locations", headers=auth_headers)
    assert live.status_code == 200 and any(p["employee_id"] == emp.id for p in live.json())

    r = client.put(f"/api/v1/hr/company-locations/{loc_id}", headers=auth_headers, json={
        "location_name": "Renamed Clinic", "latitude": CLINIC[0], "longitude": CLINIC[1], "allowed_radius_meters": 300})
    assert r.status_code == 200 and r.json()["allowed_radius_meters"] == 300


def test_employee_without_location_permission_cannot_manage_locations(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, f"GPSN-{_uid()}", f"gpsn.{_uid()}")
    r = client.post("/api/v1/hr/company-locations", headers=h, json={
        "location_name": "Nope", "latitude": CLINIC[0], "longitude": CLINIC[1]})
    assert r.status_code == 403, r.text
    assert client.get("/api/v1/hr/live-locations", headers=h).status_code == 403
