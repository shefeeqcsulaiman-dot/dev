"""Regression coverage for GET /attendance/monthly-report -- the new HRMS
Reports Attendance report. Previously "Reports & Analytics" in the HRMS
sidebar just opened the Payroll page; there was no attendance report
anywhere in HRMS at all."""
import json
from datetime import datetime, timezone

from app import attendance_store
from app.models import AppDataRecord, Employee, LeaveRequest


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no, full_name):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _seed_punch(db, company_id, employee_no, punch_date, hour, minute=0, direction="in"):
    return attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=employee_no,
        punch_time=datetime(2026, 8, int(punch_date[-2:]), hour, minute, tzinfo=timezone.utc),
        direction=direction, source="device",
    )


def _seed_app_record(db, company_id, collection, record_key, payload):
    row = AppDataRecord(company_id=company_id, collection=collection, record_key=record_key,
                         payload=json.dumps(payload))
    db.add(row)
    db.commit()
    return row


def test_monthly_report_counts_present_absent_leave_and_hours(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "ATT-RPT-001", "Report Test Employee")

    # Fri/Sat weekend policy, so Aug 2026's working days are Sun-Thu.
    _seed_app_record(db, company_id, "hr_settings", "weekend-policy-config", {"mode": "fri_sat"})

    # Wed 26 Aug 2026: worked 08:00-17:00 (9h, 1h OT over an 8h standard day).
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-26", 8)
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-26", 17, direction="out")
    # Thu 27 Aug 2026: worked 08:00-16:00 (8h, no OT).
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-27", 8)
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-27", 16, direction="out")
    # Sun 30 Aug 2026 (a real working day under Fri/Sat weekend): 09:00-13:00 (4h).
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-30", 9)
    _seed_punch(db, company_id, "ATT-RPT-001", "2026-08-30", 13, direction="out")

    # Approved sick leave on Mon 24 Aug 2026 (a working day).
    db.add(LeaveRequest(company_id=company_id, employee_id=emp.id, leave_type="Sick Leave",
                         start_date="2026-08-24", end_date="2026-08-24", days=1, status="approved"))
    db.commit()

    r = client.get("/api/v1/attendance/monthly-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["period"] == "2026-08"

    row = next(e for e in data["employees"] if e["employee_no"] == "ATT-RPT-001")
    assert row["present_days"] == 3
    assert row["leave_days"] == 1
    assert row["total_hours"] == "21.00"   # 9 + 8 + 4
    assert row["ot_hours"] == "1.00"       # only the 26th exceeded 8h
    # absent_days = working_days - present_days - leave_days; sanity check
    # it's non-negative and consistent with the other three numbers.
    assert row["absent_days"] == data["working_days"] - row["present_days"] - row["leave_days"]


def test_monthly_report_rejects_bad_period_format(client, auth_headers):
    r = client.get("/api/v1/attendance/monthly-report?period=not-a-period", headers=auth_headers)
    assert r.status_code == 400, r.text


def test_monthly_report_scoped_to_own_company(client, db, auth_headers, second_tenant_headers):
    company_id = _company_id(client, auth_headers)
    _seed_employee(db, company_id, "ATT-RPT-ISO", "Isolation Test Employee")
    _seed_punch(db, company_id, "ATT-RPT-ISO", "2026-08-26", 8)
    _seed_punch(db, company_id, "ATT-RPT-ISO", "2026-08-26", 17, direction="out")

    r = client.get("/api/v1/attendance/monthly-report?period=2026-08", headers=second_tenant_headers)
    assert r.status_code == 200, r.text
    assert all(e["employee_no"] != "ATT-RPT-ISO" for e in r.json()["employees"])
