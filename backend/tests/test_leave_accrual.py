"""Leave days and entitlement by policy (2026-10-17):
- Working basis skips the company weekend, Holiday Calendar days and rota days off;
  Calendar basis counts every day; non-annual types use their own Leave Types Basis.
- Annual Leave is earned by HR Settings > Accrual from the join date, in the leave year.
- recount_leave_requests() re-applies the rules to existing requests."""
import json
import uuid
from datetime import date, timedelta

from app.models import AppDataRecord, Employee, LeaveRequest
from app.routers import leave
from app.routers.leave import accrued_days, leave_year_window


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"}, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _employee(client, db, headers, cid, **extra):
    no = f"LA{uuid.uuid4().hex[:6]}"
    _save(client, headers, "employees", {"id": no, "name": f"Leave {no}", "department": "LeaveDept",
                                         "designation": "Staff", "status": "Active", "salary": 5000, **extra})
    db.expire_all()
    return db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).first()


def _settings(client, headers, **values):
    _save(client, headers, "hrLeavePolicy", {"id": "leave-policy", **values})


def _days(client, headers, emp, leave_type, start, end):
    r = client.get("/api/v1/leave/days", headers=headers,
                   params={"employee_id": emp.id, "leave_type": leave_type, "start": start, "end": end})
    assert r.status_code == 200, r.text
    return r.json()


def test_working_basis_skips_weekend_holidays_and_rota_days_off(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}],
              leave_types=[{"type": "Sick Leave", "days": 90, "basis": "Calendar"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working")
    # Mon 30 Nov .. Sun 6 Dec 2026: National Day 2-3 Dec, a rota day off on Fri 4 Dec
    _save(client, auth_headers, "hrHolidays", {"id": "la-hol-1202", "date": "2026-12-02", "name": "UAE National Day"})
    _save(client, auth_headers, "hrHolidays", {"id": "la-hol-1203", "date": "2026-12-03", "name": "UAE National Day"})
    _save(client, auth_headers, "rotaAssignments", {"id": f"{emp.employee_no}-2026-12-04", "employee_id": emp.employee_no,
                                                    "date": "2026-12-04", "code": "OFF", "mark": "Off"})
    for day in ("2026-11-30", "2026-12-01"):
        _save(client, auth_headers, "rotaAssignments", {"id": f"{emp.employee_no}-{day}", "employee_id": emp.employee_no,
                                                        "date": day, "code": "M", "mark": "Shift", "start": "09:00", "end": "17:00"})
    no_rota = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working")
    weekend_row = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
                                                 AppDataRecord.record_key == "weekend-policy-config").all()
    saved = [(r.id, r.payload) for r in weekend_row]
    for r in weekend_row:
        r.payload = json.dumps({"mode": "sat_sun"})
    db.commit()
    try:
        d = _days(client, auth_headers, emp, "Annual Leave", "2026-11-30", "2026-12-06")
        # On the rota: Mon 30 + Tue 1 working; Wed 2, Thu 3 holidays; Fri 4 off; Sat 5, Sun 6 have no
        # entry in a week that has one, so they are rota days off too.
        assert d["basis"] == "Working" and d["days"] == 2
        assert d["skipped"] == {"holiday": 2, "day_off": 3}
        # Without a rota: the company weekend applies.
        d = _days(client, auth_headers, no_rota, "Annual Leave", "2026-11-30", "2026-12-06")
        assert d["days"] == 3 and d["skipped"] == {"weekend": 2, "holiday": 2}
        # Calendar: neither the 2 public holidays (2026-10-22) nor the 3 rota days off (Fri-Sun,
        # 2026-10-20) are counted; the weekend is
        cal = _days(client, auth_headers, emp, "Sick Leave", "2026-11-30", "2026-12-06")
        assert cal["days"] == 2 and cal["skipped"] == {"holiday": 2, "day_off": 3}
        assert _days(client, auth_headers, no_rota, "Sick Leave", "2026-11-30", "2026-12-06")["days"] == 5   # no rota
        _settings(client, auth_headers, leave_types=[{"type": "Sick Leave", "days": 90, "basis": "Working"}])
        assert _days(client, auth_headers, emp, "Sick Leave", "2026-11-30", "2026-12-06")["days"] == 2   # its own Basis
    finally:
        for rid, payload in saved:
            db.get(AppDataRecord, rid).payload = payload
        db.commit()
        _settings(client, auth_headers)


def test_leave_year_windows():
    assert leave_year_window({}, None, date(2026, 10, 17)) == (date(2026, 1, 1), date(2027, 1, 1))
    assert leave_year_window({"year_start": "April"}, None, date(2026, 2, 1)) == (date(2025, 4, 1), date(2026, 4, 1))
    assert leave_year_window({"year_start": "Joining Date (rolling)"}, date(2023, 6, 15), date(2026, 3, 1)) == (date(2025, 6, 15), date(2026, 6, 15))


def test_accrual_monthly_quarterly_upfront_from_join_date():
    on = date(2026, 10, 17)
    assert accrued_days({"accrual": "Monthly (1/12 per month)"}, 30, date(2020, 1, 1), on) == 25        # Jan..Oct
    assert accrued_days({"accrual": "Monthly (1/12 per month)"}, 30, date(2026, 8, 15), on) == 7.5      # Aug, Sep, Oct
    assert accrued_days({"accrual": "Monthly (1/12 per month)"}, 30, date(2026, 11, 1), on) == 0        # not joined yet
    assert accrued_days({"accrual": "Upfront (full year on start)"}, 30, date(2020, 1, 1), on) == 30
    assert accrued_days({"accrual": "Upfront (full year on start)"}, 30, date(2026, 10, 1), on) == 7.5   # Oct..Dec of 12
    assert accrued_days({"accrual": "Quarterly"}, 30, date(2020, 1, 1), on) == 30                         # Q4 started
    assert accrued_days({"accrual": "Quarterly"}, 30, date(2026, 8, 1), on) == 15                         # Q3 + Q4


def test_new_joiner_can_only_take_what_is_earned(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, accrual="Monthly (1/12 per month)",
              leave_policies=[{"value": "UAE 30 Calendar", "days": 30, "basis": "Calendar"}])
    today = date.today()
    joined = date(today.year, today.month, 1)
    emp = _employee(client, db, auth_headers, cid, leave_policy="UAE 30 Calendar", join_date=joined.isoformat())
    try:
        bal = next(b for b in client.get("/api/v1/leave/balance", headers=auth_headers).json() if b["employee_id"] == emp.id)
        assert bal["annual_entitlement"] == 2.5 and bal["annual_yearly_entitlement"] == 30   # one month earned
        start = date(today.year, today.month, 1)
        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": emp.id, "leave_type": "Annual Leave",
            "start_date": start.isoformat(), "end_date": start.replace(day=start.day + 4).isoformat()})
        assert r.status_code == 400 and "available" in r.json()["detail"]
    finally:
        _settings(client, auth_headers)


def test_recount_applies_the_rules_to_existing_requests(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working")
    try:
        req = LeaveRequest(company_id=cid, employee_id=emp.id, leave_type="Annual Leave", start_date="2026-11-02",
                           end_date="2026-11-08", days=7, status="approved")   # counted as calendar days before
        db.add(req)
        db.commit()
        assert leave.recount_leave_requests(db, cid, date(2026, 1, 1)) >= 1
        db.refresh(req)
        assert req.days == 5   # Mon 2 .. Fri 6 Nov; the weekend skipped
    finally:
        _settings(client, auth_headers)


def test_next_year_leave_can_use_next_years_whole_entitlement(client, db, auth_headers):
    """Booked in advance, leave in the next leave year is checked against that year's
    whole entitlement -- not only what is earned by its start date (a January holiday
    booked in December). This year's leave still follows the monthly earning."""
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, accrual="Monthly (1/12 per month)",
              leave_policies=[{"value": "UAE 30 Calendar", "days": 30, "basis": "Calendar"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="UAE 30 Calendar", join_date="2020-01-01")
    try:
        start = date(date.today().year + 1, 1, 10)
        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": emp.id, "leave_type": "Annual Leave",
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=9)).isoformat()})
        assert r.status_code == 201, r.text                     # 10 days in January: was 2.5 earned
        r = client.post(f"/api/v1/leave/requests/{r.json()['id']}/approve", headers=auth_headers)
        assert r.status_code == 200, r.text                     # approval uses the same rule
        big = start + timedelta(days=30)
        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": emp.id, "leave_type": "Annual Leave",
            "start_date": big.isoformat(), "end_date": (big + timedelta(days=24)).isoformat()})
        assert r.status_code == 400 and "available" in r.json()["detail"]   # 10 + 25 > 30 for the year
    finally:
        _settings(client, auth_headers)


def test_flexible_policy_gives_the_whole_year_at_once(client, db, auth_headers):
    """Named Leave Policies > Flexible = Yes: the whole leave year's Annual Leave is available
    from its start (shown in HRMS and ESS) instead of being earned month by month; someone who
    joined this year gets the rest of the year's share at once."""
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, accrual="Monthly (1/12 per month)", year_start="January",
              leave_policies=[{"value": "UAE 30 Calendar", "days": 30, "basis": "Calendar", "flexible": "No"},
                              {"value": "Flexi 30", "days": 30, "basis": "Calendar", "flexible": "Yes"}])
    today = date.today()
    jan = date(today.year, 1, 1).isoformat()
    steady = _employee(client, db, auth_headers, cid, leave_policy="UAE 30 Calendar", join_date=jan)
    flexi = _employee(client, db, auth_headers, cid, leave_policy="Flexi 30", join_date=jan)
    joined_now = date(today.year, today.month, 1)
    newcomer = _employee(client, db, auth_headers, cid, leave_policy="Flexi 30", join_date=joined_now.isoformat())
    try:
        bal = {b["employee_id"]: b for b in client.get("/api/v1/leave/balance", headers=auth_headers).json()}
        assert bal[steady.id]["annual_entitlement"] == accrued_days({"accrual": "Monthly"}, 30, date(today.year, 1, 1), today)
        assert bal[flexi.id]["annual_entitlement"] == 30
        months_left = 12 - today.month + 1
        expected = round(30 * months_left / 12 * 2) / 2
        assert bal[newcomer.id]["annual_entitlement"] == (int(expected) if float(expected).is_integer() else expected)
    finally:
        _settings(client, auth_headers)


def test_joining_month_counts_only_by_the_15th():
    """HR decision 2026-10-22: joined 1-15 Jul earns July; joined 16-31 Jul starts in August."""
    s = {"accrual": "Monthly", "year_start": "January"}
    assert accrued_days(s, 24, date(2026, 7, 15), date(2026, 12, 31)) == 12   # Jul-Dec
    assert accrued_days(s, 24, date(2026, 7, 16), date(2026, 12, 31)) == 10   # Aug-Dec
    assert accrued_days(s, 24, date(2026, 7, 28), date(2026, 7, 31)) == 0     # nothing earned in July
    assert accrued_days(s, 24, date(2020, 7, 28), date(2026, 12, 31)) == 24   # joined in an earlier year
    rolling = {"accrual": "Monthly", "year_start": "Joining Date (rolling)"}
    assert accrued_days(rolling, 24, date(2020, 7, 28), date(2027, 7, 27)) == 24   # anniversary years unchanged


def test_flexible_gives_nothing_before_joining(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, accrual="Monthly (1/12 per month)", year_start="January",
              leave_policies=[{"value": "Flexi 30", "days": 30, "basis": "Calendar", "flexible": "Yes"}])
    later = date.today() + timedelta(days=40)
    emp = _employee(client, db, auth_headers, cid, leave_policy="Flexi 30", join_date=later.isoformat())
    try:
        bal = {b["employee_id"]: b for b in client.get("/api/v1/leave/balance", headers=auth_headers).json()}
        assert bal[emp.id]["annual_entitlement"] == 0
    finally:
        _settings(client, auth_headers)


def test_leave_dates_must_be_complete_and_at_most_a_year(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(client, db, auth_headers, cid, join_date="2020-01-01")
    r = client.get(f"/api/v1/leave/days?employee_id={emp.id}&leave_type=Annual%20Leave&start=0002-10-11&end=2026-10-22",
                   headers=auth_headers)
    assert r.status_code == 400 and "incomplete" in r.json()["detail"]
    r = client.get(f"/api/v1/leave/days?employee_id={emp.id}&leave_type=Annual%20Leave&start=2025-01-01&end=2026-06-01",
                   headers=auth_headers)
    assert r.status_code == 400 and "one year" in r.json()["detail"]
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave", "start_date": "0202-02-11", "end_date": "2026-02-19"})
    assert r.status_code == 400
