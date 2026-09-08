"""Regression coverage for GET /ess/attendance. Previously this returned
punch_time in raw UTC with no timezone adjustment at all, while HRMS's own
Today's Attendance screen (attendance.py's /today) already applies the
company's local UTC offset — the same punch showed a different clock time
depending on which screen you looked at it from."""
from datetime import datetime, timezone

import app.timezone_utils as timezone_utils
from app import attendance_store
from app.models import Company, Employee


def test_ess_attendance_applies_company_local_offset(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp = Employee(company_id=company_id, employee_no="ESS-TEST-001", full_name="ESS Test Employee",
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)

    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=auth_headers,
        json={"username": "ess.test.attendance", "password": "esstest123", "is_active": True},
    )
    assert r.status_code == 200, r.text

    # auth_headers reuses one shared test company for the whole pytest
    # session, and another test file may have changed its country (e.g.
    # exercising a different VAT rate) — read the real offset that
    # ess_attendance() itself will apply instead of assuming UAE's +4.
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)

    # A punch at a UTC time whose local hour lands on the NEXT calendar day
    # for every offset this app supports (+3 or +4) — exercises the
    # offset, not just a same-day shift.
    punch_time_utc = datetime(2026, 8, 20, 21, 30, tzinfo=timezone.utc)
    local_time = punch_time_utc + offset
    attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=emp.employee_no, employee_name=emp.full_name,
        punch_time=punch_time_utc, direction="in", source="device",
    )

    login = client.post("/api/v1/ess/login", json={"username": "ess.test.attendance", "password": "esstest123"})
    assert login.status_code == 200, login.text
    ess_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = client.get("/api/v1/ess/attendance", headers=ess_headers)
    assert r.status_code == 200, r.text
    row = next(p for p in r.json() if p["punch_date"] == local_time.strftime("%Y-%m-%d"))
    assert local_time.strftime("%H:%M") in row["punch_time"]
