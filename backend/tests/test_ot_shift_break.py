"""Overtime ignores the unpaid break of the shift the rota gives that day: a 10-hour day on a
shift with a 60-minute break is 9 worked hours. Time already clocked out between sessions
counts toward the break, and days without a rota shift are unchanged."""
import uuid
from datetime import datetime, timezone

from app import attendance_store
from app.models import Employee
from tests.test_attendance_monthly_report import _company_id, _seed_app_record

DAY = "2026-07-15"  # a Wednesday


def _hm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _employee(db, company_id, tag):
    emp = Employee(company_id=company_id, employee_no=f"OTB-{tag}", full_name=f"Break {tag}", basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _punch(db, company_id, emp, hour, minute=0, direction="in"):
    attendance_store.upsert_attendance_event(
        db, company_id=company_id, employee_id=emp.employee_no,
        punch_time=datetime(2026, 7, 15, hour, minute, tzinfo=timezone.utc), direction=direction, source="device",
    )


def _rota(db, company_id, emp, **extra):
    rec = {"id": f"{emp.employee_no}-{DAY}", "employee_id": emp.employee_no, "date": DAY, "type": "Morning", "code": "M",
           # an 8-hour shift: the 10-hour days below run past it (a day's standard is its shift's length)
           "start": "08:00", "end": "16:00", "mark": "Shift", **extra}
    _seed_app_record(db, company_id, "rotaAssignments", rec["id"], rec)


def _eligibility(client, h, emp):
    rows = client.get(f"/api/v1/attendance/overtime-eligibility?date_from={DAY}&date_to={DAY}", headers=h).json()["rows"]
    return next(r for r in rows if r["employee_no"] == emp.employee_no)


def test_shift_break_is_not_overtime(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, emp, break_minutes=60)
    _punch(db, cid, emp, 8)
    _punch(db, cid, emp, 18, direction="out")  # 10h at work

    row = _eligibility(client, auth_headers, emp)
    assert row["worked"] == "9:00" and row["break"] == "1:00"
    assert _hm(row["extra"]) == 9 * 60 - _hm(row["standard"])

    daily = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-07", headers=auth_headers).json()
    day = next(d for d in daily["days"] if d["date"] == DAY)
    report = client.get("/api/v1/attendance/monthly-report?period=2026-07", headers=auth_headers).json()
    month = next(e for e in report["employees"] if e["employee_no"] == emp.employee_no)
    assert day["ot_hours"] == month["ot_hours"] == row["eligible_ot"]


def test_break_taken_as_clock_out_is_not_deducted_twice(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, emp, break_minutes=60)
    _punch(db, cid, emp, 8)
    _punch(db, cid, emp, 12, direction="out")
    _punch(db, cid, emp, 13)
    _punch(db, cid, emp, 18, direction="out")  # 9h worked, lunch already off the clock

    row = _eligibility(client, auth_headers, emp)
    assert row["worked"] == "9:00" and row["break"] is None


def test_shift_setup_break_used_when_assignment_has_none_and_no_rota_means_no_deduction(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:4].upper()
    _seed_app_record(db, cid, "rotaShifts", f"shift-{tag}", {"code": f"Z{tag}", "name": "Long", "start": "08:00", "end": "18:00", "break_minutes": 30})
    with_shift = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, with_shift, code=f"Z{tag}")
    no_rota = _employee(db, cid, uuid.uuid4().hex[:6])
    for emp in (with_shift, no_rota):
        _punch(db, cid, emp, 8)
        _punch(db, cid, emp, 18, direction="out")

    assert _eligibility(client, auth_headers, with_shift)["worked"] == "9:30"
    plain = _eligibility(client, auth_headers, no_rota)
    assert plain["worked"] == "10:00" and plain["break"] is None


def test_extra_shift_hours_are_all_overtime(client, db, auth_headers):
    """Rota "Mark As: Extra shift" (code OT): every hour worked that day is overtime, with no
    standard day and no cool-off taken off (2026-10-20). Working exactly the shift used to
    give 0 overtime, because the extra shift's own length became the day's standard."""
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, emp, code="OT", mark="OT", type="Overtime", break_minutes=0)
    _punch(db, cid, emp, 8)
    _punch(db, cid, emp, 16, direction="out")

    row = _eligibility(client, auth_headers, emp)
    assert row["extra_shift"] is True and row["eligible"] is True
    assert row["standard"] == "0:00" and row["eligible_ot"] == row["worked"] and _hm(row["worked"]) > 0
    assert row["eligibility"] == "Extra shift - paid automatically"

    daily = client.get(f"/api/v1/attendance/employee-daily?employee_id={emp.id}&period=2026-07", headers=auth_headers).json()
    day = next(d for d in daily["days"] if d["date"] == DAY)
    assert day["ot_hours"] == row["worked"]


def test_normal_shift_still_uses_its_length_as_standard(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, emp, break_minutes=0)
    _punch(db, cid, emp, 8)
    _punch(db, cid, emp, 16, direction="out")
    rows = client.get(f"/api/v1/attendance/overtime-eligibility?date_from={DAY}&date_to={DAY}", headers=auth_headers).json()["rows"]
    assert all(r["employee_no"] != emp.employee_no for r in rows)   # worked exactly the shift: no overtime


def test_extra_shift_overtime_is_paid_in_payroll_without_a_request(client, db, auth_headers):
    """Auto-approved: the payroll run pays an Extra shift's worked hours at the OT rate, with
    no overtime request; a day that has a request is left to that request (no double pay)."""
    from decimal import Decimal

    cid = _company_id(client, auth_headers)
    emp = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, emp, code="OT", mark="OT", type="Overtime", break_minutes=0)
    _punch(db, cid, emp, 8)
    _punch(db, cid, emp, 16, direction="out")
    worked = _hm(_eligibility(client, auth_headers, emp)["worked"]) / 60

    other = _employee(db, cid, uuid.uuid4().hex[:6])
    _rota(db, cid, other, code="OT", mark="OT", type="Overtime", break_minutes=0)
    _punch(db, cid, other, 8)
    _punch(db, cid, other, 16, direction="out")
    _seed_app_record(db, cid, "overtimeRequests", f"req-{other.employee_no}", {
        "id": f"req-{other.employee_no}", "employee_id": other.employee_no, "employee": other.full_name,
        "date": DAY, "ot_hours": 1, "multiplier": "1.25x", "status": "Rejected"})

    r = client.post("/api/v1/payroll/generate", headers=auth_headers, json={"period": "2026-07"})
    assert r.status_code == 201, r.text
    items = {i["employee_id"]: i for i in r.json()["items"]}
    expected = (Decimal(str(worked)) * Decimal("5000") / Decimal("176") * Decimal("1.25")).quantize(Decimal("0.01"))
    assert abs(Decimal(str(items[emp.id]["overtime"])) - expected) <= Decimal("0.01"), (items[emp.id], expected)
    assert Decimal(str(items[other.id]["overtime"])) == 0   # HR rejected that day's request
