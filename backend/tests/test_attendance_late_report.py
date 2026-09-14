"""Regression coverage for GET /attendance/late-report -- the Late Coming
Report added to HRMS Reports alongside the Attendance Report. Flags days
an employee's first clock-in landed after the configured standard start
time (+ grace period)."""
import json
from datetime import datetime, timezone

from app import attendance_store
from app.models import AppDataRecord, Employee


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no, full_name, department="Operations"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    department=department, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _seed_checkin(db, company_id, employee_no, day, hour, minute):
    return attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=employee_no,
        punch_time=datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc),
        direction="in", source="device",
    )


def _seed_weekend_policy(db, company_id):
    # Only inserted if this shared test company doesn't already have one --
    # auth_headers reuses one company for the whole pytest session, and this
    # is a raw insert (not an upsert), same convention already used in
    # test_attendance_monthly_report.py.
    existing = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "weekend-policy-config",
    ).first()
    if not existing:
        db.add(AppDataRecord(company_id=company_id, collection="hr_settings",
                              record_key="weekend-policy-config", payload=json.dumps({"mode": "fri_sat"})))
        db.commit()


def _save_late_rules(client, headers, start_time, grace_minutes):
    # The real /app-data?action=save bridge (same one saveLateRulesConfig()
    # in app.js uses) upserts by record id -- unlike a raw AppDataRecord
    # insert, this can't leave duplicate "late-rules-config" rows behind for
    # a later test's query to nondeterministically pick between.
    r = client.post(
        "/api/v1/app-data", headers=headers, params={"action": "save"},
        json={"collection": "hr_settings", "record": {"id": "late-rules-config", "startTime": start_time, "graceMinutes": grace_minutes}},
    )
    assert r.status_code == 200, r.text


def test_late_report_defaults_to_9am_no_grace_when_unconfigured(client, db, auth_headers):
    # Must run before any other test in this file saves a late-rules-config
    # (auth_headers' shared test company has no other writer of this key
    # anywhere else in the suite) -- otherwise this is testing a rule some
    # earlier test already saved, not the true unconfigured default.
    company_id = _company_id(client, auth_headers)
    _seed_weekend_policy(db, company_id)
    _seed_employee(db, company_id, "LATE-DEFAULT", "Default Rule Employee")
    # Wed 26 Aug 2026 (working day under Fri/Sat weekend), local 09:05 --
    # UAE default company is UTC+4, so 05:05 UTC -> 09:05 local, 5 minutes
    # past the default 09:00-with-no-grace threshold.
    _seed_checkin(db, company_id, "LATE-DEFAULT", 26, 5, 5)

    r = client.get("/api/v1/attendance/late-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["standard_start_time"] == "09:00"
    assert data["grace_minutes"] == 0
    row = next(e for e in data["employees"] if e["employee_no"] == "LATE-DEFAULT")
    assert row["late_days"] == 1
    assert row["total_late"] == "0:05"


def test_late_report_flags_checkin_after_start_time_plus_grace(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_weekend_policy(db, company_id)
    _save_late_rules(client, auth_headers, "09:00", 15)
    _seed_employee(db, company_id, "LATE-001", "Late Report Employee")

    # Local 09:20 -- 5 minutes past the 09:00 + 15min grace threshold.
    _seed_checkin(db, company_id, "LATE-001", 26, 5, 20)
    # Thu 27 Aug 2026, local 09:10 -- inside the 15-minute grace, not late.
    _seed_checkin(db, company_id, "LATE-001", 27, 5, 10)

    r = client.get("/api/v1/attendance/late-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["standard_start_time"] == "09:00"
    assert data["grace_minutes"] == 15
    row = next(e for e in data["employees"] if e["employee_no"] == "LATE-001")
    assert row["late_days"] == 1
    assert row["total_late_seconds"] == 5 * 60
    assert row["total_late"] == "0:05"
    assert row["last_late_date"] == "2026-08-26"


def test_late_report_on_time_employee_has_zero_late_days(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_weekend_policy(db, company_id)
    _save_late_rules(client, auth_headers, "09:00", 10)
    _seed_employee(db, company_id, "LATE-003", "On Time Employee")
    # Local 08:50 -- before the standard start time entirely.
    _seed_checkin(db, company_id, "LATE-003", 26, 4, 50)

    r = client.get("/api/v1/attendance/late-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(e for e in r.json()["employees"] if e["employee_no"] == "LATE-003")
    assert row["late_days"] == 0
    assert row["total_late_seconds"] == 0
    assert row["total_late"] == "0:00"
    assert row["last_late_date"] is None


def test_late_rules_config_saved_via_generic_bridge_is_picked_up(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_weekend_policy(db, company_id)
    _save_late_rules(client, auth_headers, "09:30", 5)

    r = client.get("/api/v1/attendance/late-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["standard_start_time"] == "09:30"
    assert r.json()["grace_minutes"] == 5


def test_late_report_sorts_worst_latecomers_first_by_default(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_weekend_policy(db, company_id)
    _save_late_rules(client, auth_headers, "09:00", 0)
    _seed_employee(db, company_id, "LATE-SORT-A", "Slightly Late")
    _seed_employee(db, company_id, "LATE-SORT-B", "Very Late")
    _seed_checkin(db, company_id, "LATE-SORT-A", 26, 5, 5)   # 5 min late
    _seed_checkin(db, company_id, "LATE-SORT-B", 26, 6, 0)   # 1h late

    r = client.get("/api/v1/attendance/late-report?period=2026-08", headers=auth_headers)
    assert r.status_code == 200, r.text
    employees = r.json()["employees"]
    idx_a = next(i for i, e in enumerate(employees) if e["employee_no"] == "LATE-SORT-A")
    idx_b = next(i for i, e in enumerate(employees) if e["employee_no"] == "LATE-SORT-B")
    assert idx_b < idx_a  # the 1-hour-late employee ranks above the 5-minute-late one
