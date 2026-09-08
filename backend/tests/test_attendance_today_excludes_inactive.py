"""GET /attendance/today (Today's Attendance / Present Today KPI) previously
counted a punch as "present" regardless of the matching Employee's status --
every other Employee query in this codebase (payroll.py, leave.py,
hr_access.py, this file's own /monthly-report) already filters on
Employee.status == "active", but this endpoint did not. A terminated/
deactivated employee whose old badge still triggers a scan (or any bulk
device-sync bug that punches every enrolled ID regardless of employment
status) must not show up as checked in."""
from datetime import datetime, timezone

from app import attendance_store
from app.models import Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no, full_name, status="active"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status=status)
    db.add(emp)
    db.commit()
    return emp


def _seed_punch(db, company_id, employee_id, punch_date, hour):
    attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=employee_id,
        punch_time=datetime(2026, 9, 8, hour, 0, tzinfo=timezone.utc),
        direction="in", source="device",
    )


def test_inactive_employee_punch_does_not_count_as_present(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_employee(db, company_id, "INACTIVE-001", "Departed Employee", status="inactive")
    _seed_employee(db, company_id, "ACTIVE-001", "Current Employee", status="active")
    _seed_punch(db, company_id, "INACTIVE-001", "2026-09-08", hour=3)
    _seed_punch(db, company_id, "ACTIVE-001", "2026-09-08", hour=4)

    r = client.get("/api/v1/attendance/today?date=2026-09-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "INACTIVE-001" not in data["employee_ids"]
    assert "ACTIVE-001" in data["employee_ids"]
    ids = {e["employee_id"] for e in data["employees"]}
    assert "INACTIVE-001" not in ids
    assert "ACTIVE-001" in ids


def test_unmatched_employee_id_still_shown(client, db, auth_headers):
    """An employee_id with NO matching Employee record at all is a
    different, intentional signal (a device/employee_no mismatch an admin
    needs to see) -- it must NOT be filtered out by the inactive-employee
    fix above."""
    company_id = _company_id(client, auth_headers)
    _seed_punch(db, company_id, "NO-SUCH-EMPLOYEE", "2026-09-08", hour=5)

    r = client.get("/api/v1/attendance/today?date=2026-09-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "NO-SUCH-EMPLOYEE" in data["employee_ids"]
    matched = next(e for e in data["employees"] if e["employee_id"] == "NO-SUCH-EMPLOYEE")
    assert matched["matched"] is False
