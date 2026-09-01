"""GET /attendance/employee-daily -- the drill-down behind the Attendance
Report's per-employee rows. Mirrors daily_attendance_report.py's column
set (Emp No./AC-No./Day/Name/Date/up to 3 Clock In-Out-Work Time triples/
Total/OT/Under Time/Absent/SICK/Holiday) with proper direction-aware
session pairing, not just a first-punch/last-punch summary."""
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


def _seed_punch(db, company_id, employee_no, punch_date, hour, minute=0, direction="in"):
    p = AttendancePunch(
        company_id=company_id, employee_id=employee_no, employee_name=None,
        punch_time=datetime(2026, 8, int(punch_date[-2:]), hour, minute, tzinfo=timezone.utc),
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

    # Two real sessions the same day (e.g. a lunch break) -- must pair by
    # direction, not just take the overall first/last punch of the day.
    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 8, 0)
    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 12, 0, direction="out")
    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 13, 0)
    _seed_punch(db, company_id, "ATT-DAILY-001", "2026-08-26", 17, 0, direction="out")

    db.add(LeaveRequest(company_id=company_id, employee_id=emp.id, leave_type="Sick Leave",
                         start_date="2026-08-24", end_date="2026-08-24", days=1, status="approved"))
    db.add(LeaveRequest(company_id=company_id, employee_id=emp.id, leave_type="Annual Leave",
                         start_date="2026-08-23", end_date="2026-08-23", days=1, status="approved"))
    db.commit()

    r = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["employee_name"] == "Daily Detail Test"
    assert data["period"] == "2026-08"
    assert data["max_sessions"] == 3
    assert len(data["days"]) == 31

    by_date = {d["date"]: d for d in data["days"]}
    d26 = by_date["2026-08-26"]
    assert d26["status"] == "present"
    assert d26["emp_no"] == "ATT-DAILY-001"
    assert d26["ac_no"] == "ATT-DAILY-001"
    assert len(d26["sessions"]) == 3
    # check_in/out are shifted to company-local time (UAE default, UTC+4)
    # by the endpoint -- punches were seeded in UTC.
    assert d26["sessions"][0]["clock_in"] == "12:00"
    assert d26["sessions"][0]["clock_out"] == "16:00"
    assert d26["sessions"][0]["work_time"] == "4.00"
    assert d26["sessions"][1]["clock_in"] == "17:00"
    assert d26["sessions"][1]["clock_out"] == "21:00"
    assert d26["sessions"][1]["work_time"] == "4.00"
    assert d26["sessions"][2]["clock_in"] is None
    assert d26["total_hours"] == "8.00"
    assert d26["ot_hours"] == "0.00"
    assert d26["under_hours"] == "0.00"

    assert by_date["2026-08-24"]["status"] == "sick"
    assert by_date["2026-08-24"]["sick"] == "Yes"
    assert by_date["2026-08-23"]["status"] == "leave"
    assert by_date["2026-08-23"]["sick"] == ""
    assert by_date["2026-08-29"]["status"] == "weekend"  # a Saturday under fri_sat
    assert by_date["2026-08-25"]["status"] == "absent"   # a working day, no punch, no leave
    assert by_date["2026-08-25"]["absent"] == "Yes"


def test_employee_daily_404_for_unknown_employee(client, auth_headers):
    r = client.get("/api/v1/attendance/employee-daily?employee_id=does-not-exist&period=2026-08", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_employee_daily_rejects_bad_period(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "ATT-DAILY-002", "Bad Period Test")
    r = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=nope", headers=auth_headers)
    assert r.status_code == 400, r.text
