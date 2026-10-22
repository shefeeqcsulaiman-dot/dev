"""Rota days off aren't absences: an explicit Off, or a day with no rota entry in a week
where the employee has entries, counts as a day off in the monthly report, the
employee's daily view, and the day's attendance roll call."""
from datetime import date

from app.models import AppDataRecord
from app.rota_days import rota_day_statuses
from tests.test_attendance_monthly_report import _company_id, _seed_app_record, _seed_employee, _seed_punch


def _rota(db, company_id, employee_no, day, **fields):
    _seed_app_record(db, company_id, "rotaAssignments", f"{employee_no}-{day}",
                     {"id": f"{employee_no}-{day}", "employee_id": employee_no, "date": day, **fields})


def _setup(db, company_id):
    # Fri/Sat weekend: Mon 24 - Thu 27 and Sun 30 Aug 2026 are working days. Other tests
    # save their own weekend policy in this shared company (one is "custom"), and the
    # report reads the first it finds, so only this one may be there.
    db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "hr_settings",
                                   AppDataRecord.record_key == "weekend-policy-config").delete(synchronize_session=False)
    db.commit()
    _seed_app_record(db, company_id, "hr_settings", "weekend-policy-config", {"mode": "fri_sat"})
    emp = _seed_employee(db, company_id, "ROFF-1", "Rota Off Person")
    _rota(db, company_id, "ROFF-1", "2026-08-24", code="M", mark="Shift", start="08:00", end="17:00")
    _rota(db, company_id, "ROFF-1", "2026-08-25", code="OFF", mark="Off")          # explicit off
    _rota(db, company_id, "ROFF-1", "2026-08-27", code="M", mark="Shift", start="08:00", end="17:00")
    _seed_punch(db, company_id, "ROFF-1", "2026-08-24", 8)
    _seed_punch(db, company_id, "ROFF-1", "2026-08-24", 17, direction="out")
    return emp


def test_rota_day_statuses_explicit_and_gap_days(db, client, auth_headers):
    company_id = _company_id(client, auth_headers)
    _setup(db, company_id)
    got = rota_day_statuses(db, company_id, ["ROFF-1"], date(2026, 8, 24), date(2026, 9, 6))
    assert got[("ROFF-1", "2026-08-25")] == "off"
    assert got[("ROFF-1", "2026-08-26")] == "off"      # gap in a week with shifts
    assert ("ROFF-1", "2026-08-24") not in got         # working day
    assert ("ROFF-1", "2026-09-01") not in got         # next week has no rota: nothing inferred


def test_monthly_report_employee_daily_and_today_respect_days_off(db, client, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _setup(db, company_id)

    report = client.get("/api/v1/attendance/monthly-report?period=2026-08", headers=auth_headers).json()
    row = next(r for r in report["employees"] if r["employee_no"] == "ROFF-1")
    assert row["present_days"] == 1
    assert row["off_days"] == 3                         # Tue 25, Wed 26, Sun 30
    assert row["absent_days"] == report["working_days"] - 1 - 3 - row["leave_days"]

    daily = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-08", headers=auth_headers).json()
    status = {d["date"]: d["status"] for d in daily["days"]}
    assert status["2026-08-24"] == "present"
    assert status["2026-08-25"] == "off" and status["2026-08-26"] == "off" and status["2026-08-30"] == "off"
    assert status["2026-08-27"] == "absent"            # rostered, didn't come
    assert status["2026-08-17"] == "absent"            # a week with no rota at all stays as before

    off_day = client.get("/api/v1/attendance/today?date=2026-08-25", headers=auth_headers).json()
    assert {"employee_id": "ROFF-1", "employee_name": "Rota Off Person", "kind": "off"} in off_day["off"]
    worked_day = client.get("/api/v1/attendance/today?date=2026-08-24", headers=auth_headers).json()
    assert all(o["employee_id"] != "ROFF-1" for o in worked_day["off"])


def test_rota_days_off_are_not_counted_for_calendar_day_leave(db, client, auth_headers):
    """Calendar-day leave counts weekends and holidays, but a rota day off is never taken
    from the balance (2026-10-20): Mon 24 shift, Tue 25 Off, Wed 26 no entry, Thu 27 shift."""
    from app.routers.leave import leave_basis, leave_day_breakdown

    company_id = _company_id(client, auth_headers)
    _setup(db, company_id)
    assert leave_basis(db, company_id, "ROFF-1", "Sick Leave") != "Working"
    got = leave_day_breakdown(db, company_id, "ROFF-1", "Sick Leave", date(2026, 8, 24), date(2026, 8, 27))
    assert got["calendar_days"] == 4 and got["days"] == 2 and got["skipped"] == {"day_off": 2}
    # A week with no rota at all: nothing inferred, every day counts.
    none = leave_day_breakdown(db, company_id, "ROFF-1", "Sick Leave", date(2026, 9, 1), date(2026, 9, 3))
    assert none["days"] == 3 and none["skipped"] == {}


def test_week_with_only_leave_marks_infers_no_days_off(db, client, auth_headers):
    """Leave booked from the rota (Mark As: Leave) in a week with no rota set is not a
    published rota: the week's other days must not turn into days off (2026-10-22)."""
    from app.routers.leave import leave_day_breakdown

    company_id = _company_id(client, auth_headers)
    _setup(db, company_id)
    _rota(db, company_id, "ROFF-1", "2026-09-08", code="L", mark="Leave", leave_type="Annual Leave")
    got = rota_day_statuses(db, company_id, ["ROFF-1"], date(2026, 9, 7), date(2026, 9, 13))
    assert got == {("ROFF-1", "2026-09-08"): "leave"}
    leave = leave_day_breakdown(db, company_id, "ROFF-1", "Sick Leave", date(2026, 9, 9), date(2026, 9, 10))
    assert leave["days"] == 2 and leave["skipped"] == {} and leave["skipped_dates"] == []


def test_leave_day_breakdown_lists_the_skipped_dates(db, client, auth_headers):
    from app.routers.leave import leave_day_breakdown

    company_id = _company_id(client, auth_headers)
    _setup(db, company_id)
    got = leave_day_breakdown(db, company_id, "ROFF-1", "Sick Leave", date(2026, 8, 24), date(2026, 8, 27))
    assert got["skipped_dates"] == [{"date": "2026-08-25", "reason": "day_off"}, {"date": "2026-08-26", "reason": "day_off"}]
