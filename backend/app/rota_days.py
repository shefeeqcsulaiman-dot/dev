"""Rota days off: which (employee, date) pairs the rota says are not working days.

A day is off when the rota marks it Off, or when the employee has no rota entry that
day but does have entries elsewhere in the same Mon-Sun week (the rota is published
for them, so the gap is a day off). A week with no entries at all means the rota
isn't set yet, so nothing is inferred. Rota Leave / Holiday marks are reported too.
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
        weeks_with_rota = {week_of[day] for day in days}
        for iso in span:
            kind = days.get(iso)
            if kind is not None:
                if kind != "shift":
                    out[(eno, iso)] = kind
            elif week_of[iso] in weeks_with_rota:
                out[(eno, iso)] = "off"
    return out


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
        code = code or ""
        if mins is None:
            mins = shifts.get(code, _DEFAULT_BREAK_MINUTES.get(code, 0))
        if mins:
            out[(eno, day)] = int(mins * 60)
    return out
