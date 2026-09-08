"""Regression coverage for approving an `attendanceCorrections` record
(POST /app-data?action=save) -- previously a pure status-flip that never
actually wrote attendance data despite the UI's own copy claiming "Approved
corrections update attendance." app_data.py's sync_domain_model() now
writes real punch events via attendance_store on approval, using a
delete-then-recreate-by-source pattern so re-approving after an edit (or
double-approving) converges instead of accumulating duplicate events."""
import json

from app.models import AttendanceDetail, Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no, full_name):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _approve_correction(client, headers, correction_id, employee_id, date, checkin=None, checkout=None):
    record = {"id": correction_id, "employee_id": employee_id, "date": date, "status": "Approved"}
    if checkin:
        record["checkin"] = checkin
    if checkout:
        record["checkout"] = checkout
    return client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "attendanceCorrections", "record": record},
    )


def test_approving_correction_writes_real_attendance(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "CORR-001", "Correction Test Employee")

    r = _approve_correction(client, auth_headers, "corr-1", emp.employee_no, "2026-08-28", checkin="08:00", checkout="17:00")
    assert r.status_code == 200, r.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == emp.employee_no,
        AttendanceDetail.work_date == "2026-08-28",
    ).first()
    assert row is not None
    assert row.clock_in_1 is not None
    assert row.clock_out_1 is not None
    events = json.loads(row.raw_events)
    assert len(events) == 2
    assert all(e["source"] == "correction" for e in events)


def test_reapproving_correction_is_idempotent_not_additive(client, db, auth_headers):
    """Re-approving the same correction (e.g. after the requester edited
    the times) must converge to the new values, not accumulate a second
    pair of events alongside the first."""
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "CORR-002", "Reapprove Test Employee")

    r1 = _approve_correction(client, auth_headers, "corr-2", emp.employee_no, "2026-08-29", checkin="08:00", checkout="16:00")
    assert r1.status_code == 200, r1.text
    r2 = _approve_correction(client, auth_headers, "corr-2", emp.employee_no, "2026-08-29", checkin="09:00", checkout="17:00")
    assert r2.status_code == 200, r2.text

    row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == emp.employee_no,
        AttendanceDetail.work_date == "2026-08-29",
    ).first()
    assert row is not None
    events = json.loads(row.raw_events)
    # Exactly 2 events (one in, one out) -- not 4 -- reflecting the SECOND
    # (corrected) set of times, not both.
    assert len(events) == 2


def test_correction_does_not_affect_other_employees(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp1 = _seed_employee(db, company_id, "CORR-ISO-1", "Isolation Employee 1")
    _seed_employee(db, company_id, "CORR-ISO-2", "Isolation Employee 2")

    r = _approve_correction(client, auth_headers, "corr-iso", emp1.employee_no, "2026-08-30", checkin="08:00")
    assert r.status_code == 200, r.text

    other_row = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id, AttendanceDetail.employee_id == "CORR-ISO-2",
    ).first()
    assert other_row is None
