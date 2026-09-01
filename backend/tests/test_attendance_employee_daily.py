"""GET /attendance/employee-daily -- the drill-down behind the Attendance
Report's per-employee rows, which previously only showed month-level
totals with no way to see which specific days actually contributed."""
from datetime import datetime, timezone

from app.models import AppDataRecord, AttendancePunch, Employee, LeaveRequest
import json


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no, full_name):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _seed_punch(db, company_id, employee_no, punch_date, hour, direction="in"):
    p = AttendancePunch(
        company_id=company_id, employee_id=employee_no, employee_name=None,
        punch_time=datetime(2026, 8, int(punch_date[-2:]), hour, tzinfo=timezone.utc),
        punch_date=punch_date, direction=direction, source="device",
    )
    db.add(p)
    db.commit()


def test_employee_daily_breakdown(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "ATT-DAILY-001", "Daily Detail Test")

    # Fri/Sat weekend, so Aug 2026's working days are Sun-Thu.
    db.add(AppDataRecord(company_id=company_id, collection="hr_settings", record_key="weekend-policy-config",
                          payload=json.dumps({"mode": "fri_sat"})))
    db.commit()

    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 8)
    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 17, direction="out")

    db.add(LeaveRequest(company_id=company_id, employee_id=emp.id, leave_type="Sick Leave",
                         start_date="2026-08-24", end_date="2026-08-24", days=1, status="approved"))
    db.commit()

    r = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["employee_name"] == "Daily Detail Test"
    assert data["period"] == "2026-08"
    assert len(data["days"]) == 31

    by_date = {d["date"]: d for d in data["days"]}
    assert by_date["2026-08-26"]["status"] == "present"
    # check_in/out are shifted to company-local time (UAE default, UTC+4)
    # by the endpoint -- the punches were seeded at 08:00/17:00 UTC.
    assert by_date["2026-08-26"]["check_in"] == "12:00"
    assert by_date["2026-08-26"]["check_out"] == "21:00"
    assert by_date["2026-08-26"]["hours"] == "9.00"
    assert by_date["2026-08-24"]["status"] == "leave"
    assert by_date["2026-08-29"]["status"] == "weekend"  # a Saturday under fri_sat
    assert by_date["2026-08-25"]["status"] == "absent"   # a working day, no punch, no leave


def test_employee_daily_404_for_unknown_employee(client, auth_headers):
    r = client.get("/api/v1/attendance/employee-daily?employee_id=does-not-exist&period=2026-08", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_employee_daily_rejects_bad_period(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "ATT-DAILY-002", "Bad Period Test")
    r = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=nope", headers=auth_headers)
    assert r.status_code == 400, r.text
