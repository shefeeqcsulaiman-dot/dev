"""Shared attendance-event storage.

The single write path for every punch source (webhook/ADMS/CSV/manual entry
via attendance.py, BioTime pulls via biotime_sync.py, approved corrections
via app_data.py) — one concurrency-safe upsert instead of three independent
insert/dedupe implementations. Backs AttendanceDetail, one row per
company/employee/calendar day, with each individual scan retained in
raw_events for per-event listing/delete and real in/out pairing.

Deliberately a leaf module (only app.models/app.timezone_utils + stdlib) so
both attendance.py and biotime_sync.py can import it without a cycle.

The legacy one-row-per-scan-event AttendancePunch table (and this module's
former rollback-safety mirror/backfill functions targeting it) was retired
once attendance_details had run as the sole source for every live endpoint
without issue -- see git history for that table's schema and the migration
that preceded this file.
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import timezone_utils
from app.models import AttendanceDetail, Company

_MAX_STORED_SESSIONS = 5
# Anti-flood guard only -- no real employee approaches 60 genuine scans in a
# single day; this just bounds how large one day's raw_events blob can grow.
_MAX_RAW_EVENTS_PER_DAY = 60

def _pair_day_punches(events: list[tuple[datetime, str]]) -> list[tuple[datetime, datetime | None]]:
    """Direction-aware in/out pairing for one day's already-time-sorted
    punches. A session opens on an "in" and closes on the next "out".

    While a session is already open, any further "in" before the closing
    "out" is IGNORED -- it's a re-scan (dwell/turnstile devices re-read the
    same entry) or a forgotten checkout followed by re-entry; either way
    the FIRST "in" stays the session start, and no spurious incomplete
    session is created for the extra "in"s. (This replaces an earlier
    5-minute "sensor bounce" window that only collapsed rapid re-reads and
    still emitted an incomplete (in, None) pair for anything slower.)

    An "out" with no open session is not returned as a pair: it cannot form
    a session, and slotting it into clock_out_1 would blank clock_in_1 and
    hide an overnight-shift worker (whose "in" belongs to the prior day)
    from every "who is present" view. It is still retained verbatim in
    raw_events and the per-event Sync Activity Log.

    A single unclosed "in" left at day-end yields a trailing
    (clock_in, None). That is a GENUINE attendance event and
    _recompute_day_fields always surfaces it in a clock_in_N column -- but
    it is NOT a completed session, so it never counts toward session_count,
    working time or overtime."""
    pairs: list[tuple[datetime, datetime | None]] = []
    open_in: datetime | None = None
    for ts, direction in events:
        if direction == "in":
            if open_in is None:
                open_in = ts
            # else: already inside a session -- ignore this redundant "in".
        elif open_in is not None:
            pairs.append((open_in, ts))
            open_in = None
    if open_in is not None:
        pairs.append((open_in, None))
    return pairs


def _company_offset(db: Session, company_id: str) -> timedelta:
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    return timezone_utils.company_utc_offset(country)


def _local_date(punch_time_utc: datetime, offset: timedelta) -> str:
    return (punch_time_utc + offset).strftime("%Y-%m-%d")


def _recompute_day_fields(row: AttendanceDetail, events: list[dict], standard_hours: float) -> None:
    """Re-derive every derived column on `row` from its full raw_events
    list. Always re-runs pairing over the whole day rather than patching
    incrementally, so a late/out-of-order event (e.g. a CSV backfill row
    landing after live punches) always produces a correct day.

    Two separate concepts, kept strictly apart:

    * ATTENDANCE EVENTS -- every genuine scan. Preserved in full in
      raw_events, and surfaced in the clock_in_N / clock_out_N columns as
      far as the 5 display slots allow, in chronological order. A trailing
      unmatched "in" (checkout never arrived) stays in clock_in_N with
      clock_out_N NULL and work_seconds_N 0. It is NOT dropped just because
      its "out" is missing.

    * COMPLETED SESSIONS -- an "in" + "out" pair, nothing less. ONLY these
      contribute to work_seconds_N, total_seconds, ot_seconds and
      session_count. An unmatched event contributes zero to every one of
      them.

    created_at is the DB row-creation timestamp only and is NEVER read
    here -- every clock value comes from a raw_events entry's punch_time."""
    parsed = sorted(
        ((datetime.fromisoformat(e["punch_time"]), e.get("direction") or "in") for e in events),
        key=lambda t: t[0],
    )
    pairs = _pair_day_punches(parsed)
    # A completed session needs BOTH ends. _pair_day_punches only ever
    # leaves the LAST pair possibly half-open (a trailing unmatched "in").
    complete = [(cin, cout) for cin, cout in pairs if cin is not None and cout is not None]

    # Display every pair in chronological order, up to the 5 stored slots --
    # a trailing unmatched "in" is a real event and must stay visible even
    # when an earlier session already completed.
    display = pairs[:_MAX_STORED_SESSIONS]
    for i in range(_MAX_STORED_SESSIONS):
        n = i + 1
        if i < len(display):
            cin, cout = display[i]
            setattr(row, f"clock_in_{n}", cin)
            setattr(row, f"clock_out_{n}", cout)
            # Real duration only for a completed pair. An unmatched event
            # gets 0 -- a true value ("no working time"), never NULL, which
            # is reserved for "this slot holds no event at all".
            complete_pair = cin is not None and cout is not None
            setattr(row, f"work_seconds_{n}", int((cout - cin).total_seconds()) if complete_pair else 0)
        else:
            setattr(row, f"clock_in_{n}", None)
            setattr(row, f"clock_out_{n}", None)
            setattr(row, f"work_seconds_{n}", None)

    # Total/OT/Under and session_count are from COMPLETE sessions only --
    # every closed pair counts (not just the first _MAX_STORED_SESSIONS
    # shown), an unmatched "in" counts for nothing.
    total_seconds = sum(int((cout - cin).total_seconds()) for cin, cout in complete)
    standard_seconds = int(standard_hours * 3600)
    row.total_seconds = total_seconds
    row.ot_seconds = max(0, total_seconds - standard_seconds)
    row.under_seconds = max(0, standard_seconds - total_seconds)
    row.session_count = len(complete)
    row.raw_events = json.dumps(events)


def upsert_attendance_event(
    db: Session,
    *,
    company_id: str,
    employee_id: str,
    punch_time: datetime,
    direction: str = "in",
    employee_name: str | None = None,
    device_id: str | None = None,
    device_name: str | None = None,
    source: str = "manual",
    standard_hours: float = 8.0,
    authoritative_direction: bool = False,
) -> dict:
    """The single write path for a punch event.

    Finds-or-creates the one AttendanceDetail row for (company_id,
    employee_id, the event's local calendar date) under a row lock, skips
    the append if an identical (punch_time, device_id) pair is already
    recorded in that day's raw_events, re-pairs the whole day, and writes
    every derived column.

    Returns {"ok": True, "id", "event_id", "duplicate": bool} on success,
    or {"ok": False, "error": ...} — same shape family as attendance.py's
    existing _ingest_device_punch, so call sites need minimal changes.
    """
    employee_id = employee_id.strip()
    offset = _company_offset(db, company_id)
    work_date = _local_date(punch_time, offset)
    punch_time_key = punch_time.isoformat()

    for _attempt in range(3):
        row = (
            db.query(AttendanceDetail)
            .filter(
                AttendanceDetail.company_id == company_id,
                AttendanceDetail.employee_id == employee_id,
                AttendanceDetail.work_date == work_date,
            )
            .with_for_update()
            .first()
        )
        created = False
        if row is None:
            row = AttendanceDetail(
                company_id=company_id,
                employee_id=employee_id,
                employee_name=employee_name,
                work_date=work_date,
                raw_events="[]",
            )
            db.add(row)
            try:
                db.flush()
                created = True
            except IntegrityError:
                # Lost a race with a concurrent first-punch-of-the-day
                # insert for the same employee — retry; the SELECT above
                # will find the row the other transaction just created.
                db.rollback()
                continue

        events = json.loads(row.raw_events or "[]")
        same = [e for e in events if e.get("punch_time") == punch_time_key and e.get("device_id") == device_id]
        if same:
            # A dated report states in/out explicitly; it corrects a direction guessed earlier
            # (e.g. a partial day where the evening scan was taken as a check-in).
            if authoritative_direction and direction in ("in", "out") and any(e.get("direction") != direction for e in same):
                for e in same:
                    e["direction"] = direction
                _recompute_day_fields(row, events, standard_hours)
                db.commit()
                return {"ok": True, "id": row.id, "duplicate": True, "corrected": True}
            db.commit()
            return {"ok": True, "id": row.id, "duplicate": True}

        if len(events) >= _MAX_RAW_EVENTS_PER_DAY:
            db.commit()
            return {"ok": False, "error": "too_many_events_today"}

        event_id = str(uuid4())
        events.append({
            "id": event_id,
            "punch_time": punch_time_key,
            "direction": direction,
            "device_id": device_id,
            "device_name": device_name,
            "source": source,
            "employee_name": employee_name,
        })
        if employee_name and not row.employee_name:
            row.employee_name = employee_name
        _recompute_day_fields(row, events, standard_hours)
        db.commit()
        return {"ok": True, "id": row.id, "event_id": event_id, "duplicate": False, "created_day_row": created}

    return {"ok": False, "error": "conflict"}


def ensure_absent_day_row(
    db: Session, *, company_id: str, employee_id: str, work_date: str,
    employee_name: str | None = None, standard_hours: float = 8.0,
) -> dict:
    """Record an explicit ABSENT marker for (company_id, employee_id,
    work_date): a row with every clock_in/out NULL, total/OT 0,
    under_seconds = a full standard day, session_count 0, raw_events [].

    Creates the row ONLY if none exists yet -- if a row is already there
    (an employee who genuinely clocked in, or a marker already written)
    this is a no-op and never touches its events or derived fields. That
    makes it safe to call unconditionally for every roster member a daily
    report covers: present employees keep their real data, absent ones
    get a positive "sync ran, no punches" record instead of just being
    missing from the table.

    A later real punch for the same day upgrades the marker to present
    naturally -- upsert_attendance_event() finds this row, appends the
    event, and _recompute_day_fields() rebuilds clock_in_1 etc. from
    raw_events, so the NULLs are replaced with real times."""
    employee_id = employee_id.strip()
    for _attempt in range(3):
        row = (
            db.query(AttendanceDetail)
            .filter(
                AttendanceDetail.company_id == company_id,
                AttendanceDetail.employee_id == employee_id,
                AttendanceDetail.work_date == work_date,
            )
            .with_for_update()
            .first()
        )
        if row is not None:
            return {"ok": True, "created": False, "id": row.id}
        row = AttendanceDetail(
            company_id=company_id,
            employee_id=employee_id,
            employee_name=employee_name,
            work_date=work_date,
            raw_events="[]",
            total_seconds=0,
            ot_seconds=0,
            under_seconds=int(standard_hours * 3600),
            session_count=0,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Raced a concurrent insert (real punch or another marker) --
            # retry; the SELECT above will now find it.
            db.rollback()
            continue
        db.commit()
        return {"ok": True, "created": True, "id": row.id}
    return {"ok": False, "error": "conflict"}


def remove_events_by_source(
    db: Session, company_id: str, employee_id: str, work_date: str, source: str, standard_hours: float = 8.0,
) -> None:
    """Strip every raw_events entry matching `source` from the day's row
    and recompute — the delete-then-recreate idempotency app_data.py's
    attendanceCorrections approval flow relies on, so re-approving after an
    edit (or double-approving) converges instead of accumulating rows."""
    employee_id = employee_id.strip()
    row = (
        db.query(AttendanceDetail)
        .filter(
            AttendanceDetail.company_id == company_id,
            AttendanceDetail.employee_id == employee_id,
            AttendanceDetail.work_date == work_date,
        )
        .with_for_update()
        .first()
    )
    if row is None:
        return
    events = json.loads(row.raw_events or "[]")
    kept = [e for e in events if e.get("source") != source]
    if len(kept) == len(events):
        return
    _recompute_day_fields(row, kept, standard_hours)
    db.commit()


def delete_event(db: Session, company_id: str, event_id: str) -> bool:
    """Remove a single scan event by id, wherever its day-row lives —
    powers the Sync Activity Log's per-punch delete button, which used to
    be a plain db.delete() on an AttendancePunch row. Returns False if no
    row in this company holds an event with this id."""
    rows = db.query(AttendanceDetail).filter(
        AttendanceDetail.company_id == company_id,
        AttendanceDetail.raw_events.like(f'%"{event_id}"%'),
    ).all()
    for row in rows:
        events = json.loads(row.raw_events or "[]")
        kept = [e for e in events if e.get("id") != event_id]
        if len(kept) == len(events):
            continue
        if kept:
            _recompute_day_fields(row, kept, 8.0)
        else:
            db.delete(row)
        db.commit()
        return True
    return False


def flatten_events(rows: list[AttendanceDetail], offset: timedelta) -> list[dict]:
    """Explode AttendanceDetail rows' raw_events into individual per-punch
    dicts with local time applied — the shape /punches (Sync Activity Log)
    and ess.py's /attendance endpoints need, previously read directly off
    AttendancePunch rows. "punch_time" is a raw datetime, not pre-formatted
    -- the two callers previously serialized it differently
    (.isoformat() vs str()), so formatting is left to each caller rather
    than baking one format in here."""
    out: list[dict] = []
    for row in rows:
        try:
            events = json.loads(row.raw_events or "[]")
        except (ValueError, TypeError):
            events = []
        for e in events:
            try:
                punch_time = datetime.fromisoformat(e["punch_time"])
            except (KeyError, ValueError, TypeError):
                continue
            out.append({
                "id": e.get("id"),
                "employee_id": row.employee_id,
                "employee_name": e.get("employee_name") or row.employee_name,
                "punch_date": row.work_date,
                "punch_time": punch_time + offset,
                "direction": e.get("direction"),
                "device_id": e.get("device_id"),
                "device_name": e.get("device_name"),
                "source": e.get("source"),
            })
    return out
