"""HR Rules > Weekend Policy decides which days a Working-basis leave skips (2026-10-22 check):
Saturday-Sunday, Friday-Saturday, or Custom by rota (no fixed weekend: only rostered days count,
dates with no rota yet count 0 until the rota is saved)."""
import json

from app.models import AppDataRecord
from tests.test_leave_accrual import _company_id, _days, _employee, _save, _settings


def test_working_leave_follows_the_weekend_policy(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working", join_date="2020-01-01")
    before = [(r.id, r.payload) for r in db.query(AppDataRecord).filter(
        AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "weekend-policy-config")]
    try:
        # Mon 7 .. Sun 13 Dec 2026: no public holidays, no rota for this employee
        # Custom by rota with no rota at all: nothing counts yet (recounted when the rota is made)
        expected = {"sat_sun": (5, {"weekend": 2}), "fri_sat": (5, {"weekend": 2}), "custom": (0, {"no_rota": 7})}
        for mode, (days, skipped) in expected.items():
            _save(client, auth_headers, "hr_settings", {"id": "weekend-policy-config", "mode": mode})
            d = _days(client, auth_headers, emp, "Annual Leave", "2026-12-07", "2026-12-13")
            assert d["basis"] == "Working", d
            assert d["days"] == days and d["skipped"] == skipped, (mode, d)
        # Friday-Saturday: the Sunday is a working day, the Friday is not
        _save(client, auth_headers, "hr_settings", {"id": "weekend-policy-config", "mode": "fri_sat"})
        assert _days(client, auth_headers, emp, "Annual Leave", "2026-12-13", "2026-12-13")["days"] == 1
        assert _days(client, auth_headers, emp, "Annual Leave", "2026-12-11", "2026-12-11")["days"] == 0
    finally:
        db.expire_all()
        for row in db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
                                                  AppDataRecord.record_key == "weekend-policy-config"):
            old = dict(before).get(row.id)
            if old is None:
                db.delete(row)
            else:
                row.payload = old
        db.commit()


def test_custom_by_rota_uses_each_persons_rota(client, db, auth_headers):
    """Custom by rota: no company weekend; each person's rota days off are their weekend.
    Week Mon 7 .. Sun 13 Sep 2026: shifts Mon-Thu and Saturday, Friday Off, Sunday no entry
    (a day off: the week has a rota)."""
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working", join_date="2020-01-01")
    for day in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-12"):
        _save(client, auth_headers, "rotaAssignments", {"id": f"{emp.employee_no}-{day}", "employee_id": emp.employee_no,
                                                        "date": day, "code": "M", "mark": "Shift", "start": "09:00", "end": "17:00"})
    _save(client, auth_headers, "rotaAssignments", {"id": f"{emp.employee_no}-2026-09-11", "employee_id": emp.employee_no,
                                                    "date": "2026-09-11", "code": "OFF", "mark": "Off"})
    before = [(r.id, r.payload) for r in db.query(AppDataRecord).filter(
        AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "weekend-policy-config")]
    try:
        _save(client, auth_headers, "hr_settings", {"id": "weekend-policy-config", "mode": "custom"})
        # Leave: the Saturday shift counts, Friday and Sunday are this person's days off
        d = _days(client, auth_headers, emp, "Annual Leave", "2026-09-07", "2026-09-13")
        assert d["days"] == 5 and d["skipped"] == {"day_off": 2}, d
        # Attendance: rota days off are "off", not absent; the rostered Saturday with no punch is absent
        r = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-09", headers=auth_headers)
        assert r.status_code == 200, r.text
        by_date = {x["date"]: x for x in r.json()["days"]}
        assert by_date["2026-09-11"]["status"] == "off"
        assert by_date["2026-09-13"]["status"] == "off"
        assert by_date["2026-09-12"]["status"] == "absent"
        assert not by_date["2026-09-12"]["is_weekend"]
        # Monthly report: the two days off are not counted as absences
        r = client.get("/api/v1/attendance/monthly-report?period=2026-09", headers=auth_headers)
        data = r.json()
        row = next(e for e in data["employees"] if e["employee_no"] == emp.employee_no)
        assert data["working_days"] == 30   # no company weekend
        assert row["off_days"] >= 2
        assert row["absent_days"] == data["working_days"] - row["present_days"] - row["leave_days"] - row["off_days"]
    finally:
        db.expire_all()
        for row in db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
                                                  AppDataRecord.record_key == "weekend-policy-config"):
            old = dict(before).get(row.id)
            if old is None:
                db.delete(row)
            else:
                row.payload = old
        db.commit()


def _set_weekend(client, headers, db, cid, mode):
    before = [(r.id, r.payload) for r in db.query(AppDataRecord).filter(
        AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "weekend-policy-config")]
    _save(client, headers, "hr_settings", {"id": "weekend-policy-config", "mode": mode})
    return before


def _restore_weekend(db, cid, before):
    db.expire_all()
    for row in db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings",
                                              AppDataRecord.record_key == "weekend-policy-config"):
        old = dict(before).get(row.id)
        if old is None:
            db.delete(row)
        else:
            row.payload = old
    db.commit()


def _shift(no, day, mark="Shift"):
    return {"id": f"{no}-{day}", "employee_id": no, "date": day,
            "code": "OFF" if mark == "Off" else "M", "mark": mark, "start": "" if mark == "Off" else "09:00", "end": "" if mark == "Off" else "17:00"}


def _leave_days(db, rid):
    from app.models import LeaveRequest
    db.expire_all()
    return db.get(LeaveRequest, rid).days


def test_custom_by_rota_counts_no_rota_days_as_zero_then_recounts(client, db, auth_headers):
    """Custom by rota (2026-10-22): dates with no rota yet count 0; saving or deleting the rota
    for them recounts the requests that cover them."""
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}])
    e1 = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working", join_date="2020-01-01")
    e2 = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working", join_date="2020-01-01")
    before = _set_weekend(client, auth_headers, db, cid, "custom")
    try:
        # Week Mon 4 .. Sun 10 Jan 2027 is rostered for e1 (Mon, Tue shifts; Wed off; the rest
        # inferred off); week 11..17 Jan has no rota yet.
        _save(client, auth_headers, "rotaAssignments", _shift(e1.employee_no, "2027-01-04"))
        _save(client, auth_headers, "rotaAssignments", _shift(e1.employee_no, "2027-01-05"))
        _save(client, auth_headers, "rotaAssignments", _shift(e1.employee_no, "2027-01-06", "Off"))
        d = _days(client, auth_headers, e1, "Annual Leave", "2027-01-04", "2027-01-17")
        assert d["days"] == 2 and d["skipped"] == {"day_off": 5, "no_rota": 7}, d

        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": e1.id, "leave_type": "Annual Leave", "start_date": "2027-01-04", "end_date": "2027-01-17"})
        assert r.status_code == 201, r.text
        rid1 = r.json()["id"]
        assert r.json()["days"] == 2
        # Only no-rota dates: a 0-day request is accepted (it is recounted later)
        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": e2.id, "leave_type": "Annual Leave", "start_date": "2027-01-11", "end_date": "2027-01-17"})
        assert r.status_code == 201, r.text
        rid2 = r.json()["id"]
        assert r.json()["days"] == 0

        # HR publishes week 11..17 Jan (Mon-Fri shifts) for both: both requests are recounted
        week = [f"2027-01-{d:02d}" for d in range(11, 16)]
        r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"},
                        json={"collection": "rotaAssignments", "records": [_shift(e.employee_no, day) for e in (e1, e2) for day in week]})
        assert r.status_code == 200, r.text
        assert _leave_days(db, rid1) == 2 + 5
        assert _leave_days(db, rid2) == 5

        # A shift removed again: the day is a day off (the week has a rota), one day fewer
        r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "delete"},
                        json={"collection": "rotaAssignments", "record": {"id": f"{e2.employee_no}-2027-01-15"}})
        assert r.status_code == 200, r.text
        assert _leave_days(db, rid2) == 4

        # A date with no rota and nothing else: still refused when it isn't a no-rota zero
        # (a fixed weekend: Saturday-Sunday only)
        _save(client, auth_headers, "hr_settings", {"id": "weekend-policy-config", "mode": "sat_sun"})
        r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
            "employee_id": e2.id, "leave_type": "Annual Leave", "start_date": "2027-02-06", "end_date": "2027-02-07"})
        assert r.status_code == 400 and "no leave days" in r.json()["detail"]
    finally:
        _restore_weekend(db, cid, before)


def test_rota_shift_on_a_weekend_day_counts(client, db, auth_headers):
    """Saturday-Sunday weekend, but this person works the Saturday on the rota: that Saturday is
    a leave day (it used to be skipped as "weekend")."""
    cid = _company_id(client, auth_headers)
    _settings(client, auth_headers, leave_policies=[{"value": "Internal 22 Working", "days": 22, "basis": "Working"}])
    emp = _employee(client, db, auth_headers, cid, leave_policy="Internal 22 Working", join_date="2020-01-01")
    before = _set_weekend(client, auth_headers, db, cid, "sat_sun")
    try:
        for day in ("2027-01-18", "2027-01-19", "2027-01-20", "2027-01-21", "2027-01-22", "2027-01-23"):
            _save(client, auth_headers, "rotaAssignments", _shift(emp.employee_no, day))
        d = _days(client, auth_headers, emp, "Annual Leave", "2027-01-18", "2027-01-24")
        # Mon-Sat worked: 6 days; Sunday has no entry in a rostered week, so it is a day off
        assert d["days"] == 6 and d["skipped"] == {"day_off": 1}, d
    finally:
        _restore_weekend(db, cid, before)
