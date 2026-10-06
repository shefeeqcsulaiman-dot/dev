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

from app.models import AppDataRecord


def rota_in_range(lo: str, hi: str):
    """SQL filter for rotaAssignments rows dated lo..hi (plus any row not yet date-stamped).
    Callers still check each payload's own date."""
    return (
        AppDataRecord.collection == "rotaAssignments",
        or_(and_(AppDataRecord.record_date >= lo, AppDataRecord.record_date <= hi), AppDataRecord.record_date.is_(None)),
    )


def _kind(rec: dict) -> str:
    mark = str(rec.get("mark") or rec.get("type") or "").lower()
    code = str(rec.get("code") or "").upper()
    if mark == "off" or code == "OFF":
        return "off"
    if mark == "leave" or code == "L":
        return "leave"
    if mark == "holiday" or code == "PH":
        return "holiday"
    return "shift"


def rota_day_statuses(db: Session, company_id: str, employee_nos, start: date, end: date) -> dict[tuple[str, str], str]:
    """{(employee_no, 'YYYY-MM-DD'): 'off' | 'leave' | 'holiday'} for start..end; working days are omitted."""
    wanted = set(employee_nos)
    if not wanted or end < start:
        return {}
    week_from = start - timedelta(days=start.weekday())
    week_to = end + timedelta(days=6 - end.weekday())
    lo, hi = week_from.isoformat(), week_to.isoformat()

    by_emp: dict[str, dict[str, dict]] = {}
    for (payload,) in db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id, *rota_in_range(lo, hi),
    ).all():
        try:
            rec = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        eno, day = rec.get("employee_id"), str(rec.get("date") or "")
        if eno in wanted and lo <= day <= hi:
            by_emp.setdefault(eno, {})[day] = rec

    out: dict[tuple[str, str], str] = {}
    for eno, days in by_emp.items():
        weeks_with_rota = {(date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())) for d in days}
        d = start
        while d <= end:
            iso = d.isoformat()
            rec = days.get(iso)
            if rec is not None:
                kind = _kind(rec)
                if kind != "shift":
                    out[(eno, iso)] = kind
            elif d - timedelta(days=d.weekday()) in weeks_with_rota:
                out[(eno, iso)] = "off"
            d += timedelta(days=1)
    return out


# Same defaults the rota screen uses when an assignment has no break saved (ROTA_EDIT_DEFAULTS).
_DEFAULT_BREAK_MINUTES = {"M": 60, "E": 60, "N": 60}


def _minutes(value) -> float | None:
    try:
        return max(0.0, float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def rota_break_seconds(db: Session, company_id: str, employee_nos, start: date, end: date) -> dict[tuple[str, str], int]:
    """{(employee_no, 'YYYY-MM-DD'): unpaid break seconds} for days the rota gives a shift.
    The assignment's own break wins, then its shift's (Shift Setup), then the rota default."""
    wanted = set(employee_nos)
    if not wanted or end < start:
        return {}
    lo, hi = start.isoformat(), end.isoformat()
    shifts: dict[str, float] = {}
    for (payload,) in db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "rotaShifts",
    ).all():
        try:
            rec = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("code"):
            mins = _minutes(rec.get("break_minutes", rec.get("break")))
            if mins is not None:
                shifts[str(rec["code"]).upper()] = mins
    out: dict[tuple[str, str], int] = {}
    for (payload,) in db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id, *rota_in_range(lo, hi),
    ).all():
        try:
            rec = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(rec, dict) or _kind(rec) != "shift":
            continue
        eno, day = rec.get("employee_id"), str(rec.get("date") or "")[:10]
        if eno not in wanted or not (lo <= day <= hi):
            continue
        code = str(rec.get("code") or "").upper()
        mins = _minutes(rec.get("break_minutes", rec.get("breakMinutes")))
        if mins is None:
            mins = shifts.get(code, _DEFAULT_BREAK_MINUTES.get(code, 0))
        if mins:
            out[(eno, day)] = int(mins * 60)
    return out
