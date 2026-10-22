"""Rota days off: which (employee, date) pairs the rota says are not working days.

A day is off when the rota marks it Off, or when the employee has no rota entry that
day but does have a shift or Off entry elsewhere in the same Mon-Sun week (the rota is
published for them, so the gap is a day off). A week with no entries, or only Leave /
Holiday marks (e.g. leave booked from the rota), means the rota isn't set yet, so nothing
is inferred. Rota Leave / Holiday marks are reported too.
"""
import json
from datetime import date, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import AppDataRecord, RotaRecord
from app.rota_records import minutes, rota_kind


def rota_in_range(lo: str, hi: str):
    """SQL filter for rotaAssignments rows dated lo..hi (plus any row not yet date-stamped).
    Callers still check each payload's own date."""
    return (
        AppDataRecord.collection == "rotaAssignments",
        or_(and_(AppDataRecord.record_date >= lo, AppDataRecord.record_date <= hi), AppDataRecord.record_date.is_(None)),
    )


_kind = rota_kind  # app/rota_records.py: the same rule its kind column is filled with

# For more employees than this, reading the company's range by its (company, day) index and
# filtering here beats an IN list (measured: 330 staff, one week 12 -> 7 ms).
_MAX_IN = 50


def _employee_days(db: Session, company_id: str, wanted: set, lo: str, hi: str, *columns):
    """rota_records rows (employee_no, day, *columns) for these employees, dated lo..hi."""
    query = db.query(RotaRecord.employee_no, RotaRecord.day, *columns).filter(
        RotaRecord.company_id == company_id, RotaRecord.day >= lo, RotaRecord.day <= hi)
    if len(wanted) <= _MAX_IN:
        query = query.filter(RotaRecord.employee_no.in_(wanted))
    return [row for row in query.all() if row[0] in wanted]


def rota_day_statuses(db: Session, company_id: str, employee_nos, start: date, end: date) -> dict[tuple[str, str], str]:
    """{(employee_no, 'YYYY-MM-DD'): 'off' | 'leave' | 'holiday'} for start..end; working days are omitted."""
    wanted = set(employee_nos)
    if not wanted or end < start:
        return {}
    week_from = start - timedelta(days=start.weekday())
    week_to = end + timedelta(days=6 - end.weekday())
    lo, hi = week_from.isoformat(), week_to.isoformat()

    # Typed rows (app/rota_records.py) for just these employees, not every payload in the range.
    by_emp: dict[str, dict[str, str]] = {}
    for eno, day, kind in _employee_days(db, company_id, wanted, lo, hi, RotaRecord.kind):
        by_emp.setdefault(eno, {})[day] = kind

    # Each day in lo..hi with its week's Monday, worked out once rather than per employee.
    week_of: dict[str, str] = {}
    d = week_from
    while d <= week_to:
        week_of[d.isoformat()] = (d - timedelta(days=d.weekday())).isoformat()
        d += timedelta(days=1)
    span = [day for day in week_of if start.isoformat() <= day <= end.isoformat()]

    out: dict[tuple[str, str], str] = {}
    for eno, days in by_emp.items():
        weeks_with_rota = {week_of[day] for day, kind in days.items() if kind in ("shift", "off")}
        for iso in span:
            kind = days.get(iso)
            if kind is not None:
                if kind != "shift":
                    out[(eno, iso)] = kind
            elif week_of[iso] in weeks_with_rota:
                out[(eno, iso)] = "off"
    return out


# Rota "Mark As: Extra shift" (shown as "OT" before 2026-10-20) is saved with this code.
EXTRA_SHIFT_CODE = "OT"

# Same defaults the rota screen uses when an assignment has no break saved (ROTA_EDIT_DEFAULTS).
_DEFAULT_BREAK_MINUTES = {"M": 60, "E": 60, "N": 60}


_minutes = minutes


def rota_break_seconds(db: Session, company_id: str, employee_nos, start: date, end: date) -> dict[tuple[str, str], int]:
    """{(employee_no, 'YYYY-MM-DD'): unpaid break seconds} for days the rota gives a shift.
    The assignment's own break wins, then its shift's (Shift Setup), then the rota default."""
    wanted = set(employee_nos)
    if not wanted or end < start:
        return {}
    lo, hi = start.isoformat(), end.isoformat()
    shifts: dict[str, float] = {}
    for row in db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "rotaShifts",
    ).all():
        try:
            rec = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("code"):
            mins = _minutes(rec.get("break_minutes", rec.get("break")))
            if mins is not None:
                shifts[str(rec["code"]).upper()] = mins
    out: dict[tuple[str, str], int] = {}
    for eno, day, kind, code, mins in _employee_days(db, company_id, wanted, lo, hi, RotaRecord.kind,
                                                     RotaRecord.code, RotaRecord.break_minutes):
        if kind != "shift":
            continue
        code = (code or "").upper()   # Shift Setup codes are keyed upper-case
        if mins is None:
            mins = shifts.get(code, _DEFAULT_BREAK_MINUTES.get(code, 0))
        if mins:
            out[(eno, day)] = int(mins * 60)
    return out


class DayShift:
    """One employee's rota shift on one day: local "HH:MM" start/end (end may be past
    midnight, i.e. not after start), unpaid break and late grace. `extra`: an Extra shift
    (rota code OT), whose worked hours are all overtime."""
    __slots__ = ("start", "end", "break_seconds", "grace_minutes", "extra")

    def __init__(self, start: str | None, end: str | None, break_seconds: int, grace_minutes: int | None,
                 extra: bool = False):
        self.start, self.end, self.break_seconds, self.grace_minutes = start, end, break_seconds, grace_minutes
        self.extra = extra

    def length_seconds(self) -> int | None:
        """Scheduled paid time: end - start - break (an overnight shift wraps past midnight)."""
        s, e = _hm_minutes(self.start), _hm_minutes(self.end)
        if s is None or e is None:
            return None
        span = (e - s) % (24 * 60) or 24 * 60
        return max(0, span * 60 - self.break_seconds)


def _hm_minutes(value) -> int | None:
    text = str(value or "").strip()[:5]
    if len(text) == 5 and text[2] == ":" and text[:2].isdigit() and text[3:].isdigit():
        h, m = int(text[:2]), int(text[3:])
        if h < 24 and m < 60:
            return h * 60 + m
    return None


def _grace(value) -> int | None:
    """Shift Setup's grace is free text ("10 minutes", "10"): its first number, in minutes."""
    import re
    match = re.search(r"\d+(\.\d+)?", str(value or ""))
    return int(float(match.group())) if match else None


def rota_day_shifts(db: Session, company_id: str, employee_nos, start: date, end: date) -> dict[tuple[str, str], DayShift]:
    """{(employee_no, 'YYYY-MM-DD'): DayShift} for days the rota gives a shift. Start, end and
    break come from the assignment, else its shift in Shift Setup (by code); grace from Shift
    Setup only. Break falls back to the rota default like rota_break_seconds()."""
    wanted = set(employee_nos)
    if not wanted or end < start:
        return {}
    setup: dict[str, dict] = {}
    for row in db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "rotaShifts",
    ).all():
        try:
            rec = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("code"):
            setup[str(rec["code"]).upper()] = rec
    out: dict[tuple[str, str], DayShift] = {}
    for eno, day, kind, code, mins, start_time, end_time in _employee_days(
            db, company_id, wanted, start.isoformat(), end.isoformat(), RotaRecord.kind, RotaRecord.code,
            RotaRecord.break_minutes, RotaRecord.start_time, RotaRecord.end_time):
        if kind != "shift":
            continue
        code = (code or "").upper()
        shift = setup.get(code, {})
        if mins is None:
            mins = _minutes(shift.get("break_minutes", shift.get("break")))
        if mins is None:
            mins = _DEFAULT_BREAK_MINUTES.get(code, 0)
        s = start_time if _hm_minutes(start_time) is not None else shift.get("start")
        e = end_time if _hm_minutes(end_time) is not None else shift.get("end")
        out[(eno, day)] = DayShift(
            str(s)[:5] if _hm_minutes(s) is not None else None,
            str(e)[:5] if _hm_minutes(e) is not None else None,
            int((mins or 0) * 60), _grace(shift.get("grace")),
            extra=code == EXTRA_SHIFT_CODE,
        )
    return out


def clear_rota_leave(db: Session, req) -> int:
    """Leave that is cancelled, rejected or deleted no longer shows on the rota: the rota's own
    Leave entries (Mark As: Leave) on its days are removed, except on a day another pending or
    approved leave of the same employee still covers. Removed, not turned into Off: the day goes
    back to whatever the rota says around it. Returns how many entries were removed; the caller
    commits."""
    from app.models import Employee, LeaveRequest

    emp = db.query(Employee.employee_no).filter(Employee.id == req.employee_id).first()
    start, end = str(req.start_date or "")[:10], str(req.end_date or req.start_date or "")[:10]
    if not emp or not emp[0] or not start or not end or end < start:
        return 0
    others = (
        db.query(LeaveRequest.start_date, LeaveRequest.end_date)
        .filter(LeaveRequest.company_id == req.company_id, LeaveRequest.employee_id == req.employee_id,
                LeaveRequest.id != req.id, LeaveRequest.status.in_(("pending", "approved")),
                LeaveRequest.start_date <= end, LeaveRequest.end_date >= start)
        .all()
    )
    ids = [
        rid for rid, day in db.query(RotaRecord.id, RotaRecord.day).filter(
            RotaRecord.company_id == req.company_id, RotaRecord.employee_no == emp[0],
            RotaRecord.day >= start, RotaRecord.day <= end, RotaRecord.kind == "leave")
        if not any(str(lo)[:10] <= day <= str(hi or lo)[:10] for lo, hi in others)
    ]
    if not ids:
        return 0
    for record in db.query(AppDataRecord).filter(AppDataRecord.id.in_(ids)).all():
        db.delete(record)  # app/record_mirror.py drops the rota_records row with it
    from app.routers.app_data import _invalidate_write_caches
    _invalidate_write_caches("rotaAssignments", req.company_id)
    return len(ids)
