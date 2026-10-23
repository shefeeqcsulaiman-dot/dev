"""Dates for leave tests that are always real leave days: a weekday that is not a Holiday
Calendar day saved by any test in the shared test database (e.g. UAE National Day 2-3 Dec,
test_leave_accrual.py). Picking "today + N days" alone broke the day the calendar moved
(2026-10-23: today + 40 landed on 2 Dec)."""
import json
from datetime import date, timedelta


def _saved_holidays() -> set[str]:
    from app.database import SessionLocal
    from app.models import AppDataRecord

    out: set[str] = set()
    with SessionLocal() as db:
        for row in db.query(AppDataRecord).filter(AppDataRecord.collection == "hrHolidays").all():
            try:
                day = str((json.loads(row.payload or "{}") or {}).get("date") or "")[:10]
            except (TypeError, ValueError):
                continue
            if day:
                out.add(day)
    return out


def leave_day(n: int) -> date:
    """The first weekday on or after today + n that no test has saved as a public holiday."""
    holidays = _saved_holidays()
    d = date.today() + timedelta(days=n)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d += timedelta(days=1)
    return d
