"""Company Locations (HRMS > GPS Attendance): edit, deactivate and delete a location that is
in use, input checks, and the automatic check-out once an employee has left the radius."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import AttendanceSession, CompanyLocation, Employee, EmployeeLocation, EmployeeLocationLog
from tests.test_gps_checkin import CLINIC, FAR, _checkin, _employee_with_sites, _uid


def _site_of(db, emp):
    return db.query(CompanyLocation).join(EmployeeLocation, EmployeeLocation.location_id == CompanyLocation.id).filter(
        EmployeeLocation.employee_id == emp.id).one()


def test_delete_location_in_use_unlinks_and_closes_open_sessions(client, db, auth_headers):
    cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    loc = _site_of(db, emp)
    loc_id = loc.id
    db.get(Employee, emp.id).work_location_id = loc_id
    db.commit()
    assert _checkin(client, h, CLINIC).status_code == 200

    r = client.delete(f"/api/v1/hr/company-locations/{loc_id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    db.expire_all()
    assert db.get(CompanyLocation, loc_id) is None
    assert db.query(EmployeeLocation).filter(EmployeeLocation.location_id == loc_id).count() == 0
    assert db.get(Employee, emp.id).work_location_id is None
    session = db.query(AttendanceSession).filter(AttendanceSession.employee_id == emp.id).one()
    assert session.status == "closed" and session.check_out is not None


def test_edit_and_deactivate_location(client, db, auth_headers):
    r = client.post("/api/v1/hr/company-locations", headers=auth_headers, json={
        "location_name": f"  Edit   Clinic {_uid()} ", "latitude": CLINIC[0], "longitude": CLINIC[1], "allowed_radius_meters": 150})
    assert r.status_code == 201, r.text
    loc = r.json()
    assert loc["status"] == "active" and "  " not in loc["location_name"]

    r = client.put(f"/api/v1/hr/company-locations/{loc['id']}", headers=auth_headers, json={
        "location_name": "Edited Clinic", "latitude": FAR[0], "longitude": FAR[1], "allowed_radius_meters": 400, "status": "inactive"})
    assert r.status_code == 200, r.text
    assert r.json()["location_name"] == "Edited Clinic" and r.json()["allowed_radius_meters"] == 400
    assert r.json()["status"] == "inactive" and abs(r.json()["latitude"] - FAR[0]) < 1e-6

    # Leaving status out keeps it
    r = client.put(f"/api/v1/hr/company-locations/{loc['id']}", headers=auth_headers, json={
        "location_name": "Edited Clinic", "latitude": FAR[0], "longitude": FAR[1], "allowed_radius_meters": 400})
    assert r.json()["status"] == "inactive"


def test_location_input_is_checked(client, db, auth_headers):
    base = {"location_name": "Checked Clinic", "latitude": CLINIC[0], "longitude": CLINIC[1], "allowed_radius_meters": 200}
    for change in ({"location_name": "   "}, {"latitude": 95}, {"longitude": -200}, {"allowed_radius_meters": 0},
                   {"allowed_radius_meters": -50}, {"allowed_radius_meters": 99999}, {"status": "maybe"}):
        r = client.post("/api/v1/hr/company-locations", headers=auth_headers, json={**base, **change})
        assert r.status_code == 422, (change, r.text)


def test_sweep_checks_out_someone_last_seen_outside(client, db, auth_headers):
    """The phone sent one ping from outside the radius and then went quiet (app closed):
    the background sweep checks them out at the moment they left."""
    from app import worker

    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, CLINIC).status_code == 200
    session = db.query(AttendanceSession).filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open").one()
    session.check_in = datetime.now(UTC) - timedelta(minutes=40)
    for log in db.query(EmployeeLocationLog).filter(EmployeeLocationLog.session_id == session.id):
        log.created_at = datetime.now(UTC) - timedelta(minutes=40)
    left_at = datetime.now(UTC) - timedelta(minutes=12)
    db.add(EmployeeLocationLog(company_id=emp.company_id, employee_id=emp.id, session_id=session.id,
                               latitude=Decimal(str(FAR[0])), longitude=Decimal(str(FAR[1])), inside_geofence=False,
                               created_at=left_at))
    db.commit()

    worker.auto_checkout_stale_sessions()
    db.expire_all()
    closed = db.get(AttendanceSession, session.id)
    assert closed.status == "closed" and closed.auto_checkout
    out = closed.check_out if closed.check_out.tzinfo else closed.check_out.replace(tzinfo=UTC)
    assert abs((out - left_at).total_seconds()) < 2


def test_sweep_leaves_people_inside_alone(client, db, auth_headers):
    from app import worker

    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, CLINIC).status_code == 200
    session = db.query(AttendanceSession).filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open").one()
    session.check_in = datetime.now(UTC) - timedelta(minutes=40)
    db.commit()
    worker.auto_checkout_stale_sessions()
    db.expire_all()
    assert db.get(AttendanceSession, session.id).status == "open"


def test_ping_reports_the_location_for_the_phone_warning(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, CLINIC).status_code == 200
    r = client.post("/api/v1/hr/location", headers=h, json={"latitude": FAR[0], "longitude": FAR[1], "accuracy": 10})
    body = r.json()
    assert r.status_code == 200 and body["inside_geofence"] is False and body["auto_checked_out"] is False
    assert body["location_name"] == "Clinic 0" and body["grace_minutes"] == 5 and body["allowed_radius_meters"] == 200
