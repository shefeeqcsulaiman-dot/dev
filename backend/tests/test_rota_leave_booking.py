"""HRMS > Rota > Mark As: Leave books real, approved leave (2026-10-21): a request with its
leave type that uses the balance; removing it from the rota cancels one-day leave."""
from datetime import date, timedelta

from app.models import LeaveRequest
from tests.leave_dates import leave_day
from tests.test_leave_accrual import _company_id, _employee


def _day(n=40):
    # a weekday that isn't a saved public holiday, so it is a leave day on any basis
    return leave_day(n)


def test_rota_leave_books_approved_leave(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    day = _day()
    body = {"employee_no": emp.employee_no, "date": day.isoformat(), "leave_type": "Sick Leave"}
    r = client.post("/api/v1/leave/rota-book", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["leave_type"] == "Sick Leave" and out["days"] == 1
    # Same day, same type again: the same request, not a second one
    again = client.post("/api/v1/leave/rota-book", headers=auth_headers, json=body)
    assert again.status_code == 200 and again.json()["id"] == out["id"]
    # A different type on that day is refused
    other = client.post("/api/v1/leave/rota-book", headers=auth_headers, json={**body, "leave_type": "Casual Leave"})
    assert other.status_code == 409 and "Change it in Leave" in other.json()["detail"]
    db.expire_all()
    assert db.query(LeaveRequest).filter(LeaveRequest.employee_id == emp.id, LeaveRequest.status == "approved").count() == 1

    # Off the rota again: the one-day leave is cancelled and kept on record
    r = client.post("/api/v1/leave/rota-unbook", headers=auth_headers, json={"employee_no": emp.employee_no, "date": day.isoformat()})
    assert r.status_code == 200 and r.json()["cancelled"] == out["id"]
    db.expire_all()
    assert db.get(LeaveRequest, out["id"]).status == "cancelled"
    # Nothing left on that day: fine
    r = client.post("/api/v1/leave/rota-unbook", headers=auth_headers, json={"employee_no": emp.employee_no, "date": day.isoformat()})
    assert r.status_code == 200 and r.json()["cancelled"] is None


def test_rota_does_not_cut_a_longer_leave(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    start = _day(60)
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Sick Leave", "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat()})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/leave/rota-unbook", headers=auth_headers, json={"employee_no": emp.employee_no, "date": start.isoformat()})
    assert r.status_code == 409 and "Change or cancel it in Leave" in r.json()["detail"]


def test_rota_leave_checks_the_balance_and_type(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    body = {"employee_no": emp.employee_no, "date": _day(80).isoformat()}
    assert client.post("/api/v1/leave/rota-book", headers=auth_headers, json={**body, "leave_type": "Nap Leave"}).status_code == 400
    assert client.post("/api/v1/leave/rota-book", headers=auth_headers, json={**body, "employee_no": "NOPE-1", "leave_type": "Sick Leave"}).status_code == 404


def test_rota_day_off_marked_as_leave_counts_one_day(client, db, auth_headers):
    """Saturday off in the rota, HR marks it Leave (2026-10-22): one leave day, not refused
    as "all weekend, public holiday or rota days off"."""
    from tests.test_attendance_monthly_report import _seed_app_record

    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    monday = _day(60)
    monday -= timedelta(days=monday.weekday())
    saturday = monday + timedelta(days=5)
    _seed_app_record(db, cid, "rotaAssignments", f"{emp.employee_no}-{monday}",
                     {"id": f"{emp.employee_no}-{monday}", "employee_id": emp.employee_no, "date": monday.isoformat(),
                      "code": "M", "mark": "Shift", "start": "08:00", "end": "17:00"})
    _seed_app_record(db, cid, "rotaAssignments", f"{emp.employee_no}-{saturday}",
                     {"id": f"{emp.employee_no}-{saturday}", "employee_id": emp.employee_no, "date": saturday.isoformat(),
                      "code": "OFF", "mark": "Off"})
    r = client.post("/api/v1/leave/rota-book", headers=auth_headers,
                    json={"employee_no": emp.employee_no, "date": saturday.isoformat(), "leave_type": "Annual Leave"})
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 1


def _rota_leave(db, cid, emp, day, mark="Leave"):
    from tests.test_attendance_monthly_report import _seed_app_record

    key = f"{emp.employee_no}-{day}"
    _seed_app_record(db, cid, "rotaAssignments", key,
                     {"id": key, "employee_id": emp.employee_no, "date": day.isoformat(),
                      "code": "L" if mark == "Leave" else "M", "mark": mark})
    return key


def _rota_keys(db, cid, emp):
    from app.models import AppDataRecord, RotaRecord

    db.expire_all()
    keys = {k for (k,) in db.query(AppDataRecord.record_key).filter(
        AppDataRecord.company_id == cid, AppDataRecord.collection == "rotaAssignments",
        AppDataRecord.record_key.like(f"{emp.employee_no}-%"))}
    mirrored = {k for (k,) in db.query(RotaRecord.record_key).filter(
        RotaRecord.company_id == cid, RotaRecord.employee_no == emp.employee_no)}
    assert keys == mirrored
    return keys


def test_cancelled_leave_leaves_the_rota(client, db, auth_headers):
    """Leave cancelled in HRMS > Leave (2026-10-22): its rota Leave marks go, a shift on one of
    its days stays, and a day other approved leave still covers keeps its mark."""
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    start = _day(90)
    days = [start + timedelta(days=i) for i in range(3)]
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Sick Leave", "start_date": days[0].isoformat(),
        "end_date": days[2].isoformat()})
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    assert client.post(f"/api/v1/leave/requests/{leave_id}/approve", headers=auth_headers).status_code == 200
    first, last = _rota_leave(db, cid, emp, days[0]), _rota_leave(db, cid, emp, days[2])
    shift = _rota_leave(db, cid, emp, days[1], mark="Shift")
    # A one-day leave booked from the rota after the long leave was cut short: not touched
    later = _day(120)
    booked = client.post("/api/v1/leave/rota-book", headers=auth_headers,
                         json={"employee_no": emp.employee_no, "date": later.isoformat(), "leave_type": "Sick Leave"})
    assert booked.status_code == 200, booked.text
    kept = _rota_leave(db, cid, emp, later)
    assert {first, last, shift, kept} <= _rota_keys(db, cid, emp)

    r = client.post(f"/api/v1/leave/requests/{leave_id}/cancel", headers=auth_headers, json={"reason": "Back early"})
    assert r.status_code == 200, r.text
    keys = _rota_keys(db, cid, emp)
    assert first not in keys and last not in keys
    assert shift in keys and kept in keys


def test_rejected_and_ess_cancelled_leave_leave_the_rota(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    day = _day(150)
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Sick Leave", "start_date": day.isoformat(), "end_date": day.isoformat()})
    assert r.status_code == 201, r.text
    key = _rota_leave(db, cid, emp, day)
    assert client.post(f"/api/v1/leave/requests/{r.json()['id']}/reject", headers=auth_headers).status_code == 200
    assert key not in _rota_keys(db, cid, emp)

    # ESS: the employee cancels their own pending request
    from tests.test_ess_requests import _ess_login

    ess_emp, h = _ess_login(client, db, auth_headers, cid, "ROTA-ESS-1", "rota.ess1")
    day2 = _day(170)
    r = client.post("/api/v1/ess/leave", headers=h, json={
        "leave_type": "Annual Leave", "start_date": day2.isoformat(), "end_date": day2.isoformat()})
    assert r.status_code == 201, r.text
    key2 = _rota_leave(db, cid, ess_emp, day2)
    assert client.post(f"/api/v1/ess/leave/{r.json()['id']}/cancel", headers=h).status_code == 200
    assert key2 not in _rota_keys(db, cid, ess_emp)
