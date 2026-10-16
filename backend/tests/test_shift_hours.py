"""Worked time by rota shift (agreed 2026-10-16): a punch before the shift start counts from
the start, the shift's break is taken off, the day's standard is the shift's own length, and
overtime is what's left past it after the cool-off. Late = after the shift's start + its
grace (Shift Setup), measured from the start; rota days off aren't late days."""
import uuid
from datetime import date, datetime, timedelta, timezone

from app import attendance_store
from app.models import Employee
from app.routers import attendance

DAY = date(2026, 3, 3)  # a past Tuesday: a working day under every weekend policy


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _employee(db, company_id):
    emp = Employee(company_id=company_id, employee_no=f"SH{uuid.uuid4().hex[:6]}", full_name="Shift Tester",
                   department="ShiftDept", status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _punch(db, company_id, emp, day, hhmm, direction):
    offset = attendance._company_offset(db, company_id)
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, m)
    attendance_store.upsert_attendance_event(db, company_id=company_id, employee_id=emp.employee_no,
                                             punch_time=(local - offset).replace(tzinfo=timezone.utc),
                                             direction=direction, source="device")


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _rota(client, headers, emp, day, **fields):
    _save(client, headers, "rotaAssignments", {"id": f"{emp.employee_no}-{day}", "employee_id": emp.employee_no,
                                               "date": day.isoformat(), **fields})


def _ot_row(db, company_id, emp, day):
    rows = attendance.overtime_eligibility_rows(db, company_id, [emp], day, day)
    return rows[0] if rows else None


def test_early_punch_counts_from_shift_start_and_break_is_deducted(client, db, auth_headers, monkeypatch):
    monkeypatch.setattr(attendance, "_ot_cooloff_seconds", lambda db, cid: 0)
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid)
    _rota(client, auth_headers, emp, DAY, code="M", mark="Shift", start="07:00", end="16:00", break_minutes=60)
    _punch(db, cid, emp, DAY, "06:30", "in")
    _punch(db, cid, emp, DAY, "16:20", "out")

    row = _ot_row(db, cid, emp, DAY)
    # 07:00 -> 16:20 = 9:20, less the 1 h break = 8:20 (not 06:30 -> 16:20 = 8:50)
    assert row["worked"] == "8:20"
    assert row["standard"] == "8:00"            # the shift's own length: 9 h less the break
    assert row["eligible_ot"] == "0:20"
    assert row["shift"] == "07:00-16:00"


def test_cooloff_comes_off_before_overtime(client, db, auth_headers, monkeypatch):
    monkeypatch.setattr(attendance, "_ot_cooloff_seconds", lambda db, cid: 30 * 60)
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid)
    _rota(client, auth_headers, emp, DAY, code="M", mark="Shift", start="07:00", end="16:00", break_minutes=60)
    _punch(db, cid, emp, DAY, "06:30", "in")
    _punch(db, cid, emp, DAY, "16:20", "out")
    row = _ot_row(db, cid, emp, DAY)
    assert row["eligible_ot"] == "0:00" and row["eligibility"].startswith("Not eligible")

    other = _employee(db, cid)
    _rota(client, auth_headers, other, DAY, code="M", mark="Shift", start="07:00", end="16:00", break_minutes=60)
    _punch(db, cid, other, DAY, "06:55", "in")
    _punch(db, cid, other, DAY, "17:15", "out")
    row = _ot_row(db, cid, other, DAY)
    assert row["worked"] == "9:15" and row["eligible_ot"] == "0:45"   # 1:15 past the shift, less 0:30


def test_late_punch_counts_from_the_punch_and_shift_length_sets_the_standard(client, db, auth_headers, monkeypatch):
    monkeypatch.setattr(attendance, "_ot_cooloff_seconds", lambda db, cid: 0)
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid)
    # a 12-hour night shift (19:00 -> 07:00, 1 h break): its standard is 11 h, not the company's 8
    _rota(client, auth_headers, emp, DAY, code="N", mark="Shift", start="19:00", end="07:00", break_minutes=60)
    _punch(db, cid, emp, DAY, "07:10", "in")
    _punch(db, cid, emp, DAY, "20:10", "out")
    row = _ot_row(db, cid, emp, DAY)
    # the shift starts 19:00, so the 07:10-19:00 part is "early" and doesn't count
    assert row is None or row["eligible_ot"] == "0:00"


def test_late_report_uses_each_days_shift_start_and_grace(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _save(client, auth_headers, "rotaShifts", {"id": f"shift-{uuid.uuid4().hex[:6]}", "code": "AFT",
                                               "name": "Afternoon", "start": "14:00", "end": "22:00",
                                               "break_minutes": 30, "grace": "10 minutes"})
    early_bird, afternoon, off_day = _employee(db, cid), _employee(db, cid), _employee(db, cid)

    # 07:00 shift, in at 07:30: 30 min late (the company 09:00 start would have said on time)
    _rota(client, auth_headers, early_bird, DAY, code="M", mark="Shift", start="07:00", end="16:00")
    _punch(db, cid, early_bird, DAY, "07:30", "in")
    # afternoon shift from Shift Setup (start 14:00, grace 10): in at 14:08 is on time
    _rota(client, auth_headers, afternoon, DAY, code="AFT", mark="Shift")
    _punch(db, cid, afternoon, DAY, "14:08", "in")
    # a rota day off with a punch at 11:00 isn't a late day
    _rota(client, auth_headers, off_day, DAY, code="OFF", mark="Off", type="Off")
    _rota(client, auth_headers, off_day, DAY + timedelta(days=1), code="M", mark="Shift", start="09:00", end="17:00")
    _punch(db, cid, off_day, DAY, "11:00", "in")

    r = client.get("/api/v1/attendance/late-report?period=2026-03", headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = {e["employee_no"]: e for e in r.json()["employees"]}
    assert rows[early_bird.employee_no]["late_days"] == 1 and rows[early_bird.employee_no]["total_late"] == "0:30"
    assert rows[afternoon.employee_no]["late_days"] == 0
    assert rows[off_day.employee_no]["late_days"] == 0
