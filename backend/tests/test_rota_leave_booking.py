"""HRMS > Rota > Mark As: Leave books real, approved leave (2026-10-21): a request with its
leave type that uses the balance; removing it from the rota cancels one-day leave."""
from datetime import date, timedelta

from app.models import LeaveRequest
from tests.test_leave_accrual import _company_id, _employee


def _day(n=40):
    d = date.today() + timedelta(days=n)
    while d.weekday() >= 5:   # a weekday, so it is a leave day on any basis
        d += timedelta(days=1)
    return d


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
