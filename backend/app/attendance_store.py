"""Shared attendance-event storage.

This is meant to become the single write path for every punch source
(webhook/ADMS/CSV/manual entry via attendance.py, BioTime pulls via
biotime_sync.py, approved corrections via app_data.py), consolidating what
today are three divergent insert/dedupe implementations onto one
concurrency-safe upsert. It backs AttendanceDetail (one row per
company/employee/calendar day, replacing AttendancePunch's one-row-per-scan-
event shape) while, during the migration's safety-net window, also keeping
the legacy AttendancePunch table updated in parallel via _mirror_legacy_punch
so a rollback to pre-cutover code loses no data.

Deliberately a leaf module (only app.models/app.timezone_utils + stdlib) so
both attendance.py and biotime_sync.py can import it without a cycle.

Nothing calls into this module yet -- see the attendance migration plan for
the staged cutover (this file lands first, on its own, before any router is
rewired to use it).
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import timezone_utils
from app.models import AttendanceDetail, AttendancePunch, Company

_MAX_STORED_SESSIONS = 5
# Anti-flood guard only -- no real employee approaches 60 genuine scans in a
# single day; this just bounds how large one day's raw_events blob can grow.
_MAX_RAW_EVENTS_PER_DAY = 60

# Some entry-only devices (a dwell/proximity sensor, a simple turnstile that
# only signals "someone passed through") re-read the same physical entry
# several times within seconds of each other. Without this, each re-read
# would close the still-open session with no checkout and open a new one --
# a repeat "in" within this window of the currently-open one is treated as
# the same entry and collapsed instead. (Mirrors the constant of the same
# name in attendance.py, which still owns the live endpoints until cutover.)
_DWELL_DUPLICATE_WINDOW = timedelta(minutes=5)


def _pair_day_punches(events: list[tuple[datetime, str]]) -> list[tuple[datetime, datetime | None]]:
    """Direction-aware in/out pairing for one day's already-time-sorted
    punches -- same state machine as attendance.py's function of the same
    name (and zk_bridge.py/daily_attendance_report.py's pair_punches()).
    A repeat "in" within _DWELL_DUPLICATE_WINDOW of an already-open one is
    a sensor bounce and is dropped; otherwise the open "in" closes with no
    checkout and a new one opens. A trailing unclosed "in" at day-end
    yields (clock_in, None)."""
    pairs: list[tuple[datetime, datetime | None]] = []
    open_in: datetime | None = None
    for ts, direction in events:
        if direction == "in":
            if open_in is not None:
                if ts - open_in <= _DWELL_DUPLICATE_WINDOW:
                    continue
                pairs.append((open_in, None))
            open_in = ts
        else:
            if open_in is not None:
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
    landing after live punches) always produces a correct day."""
    parsed = sorted(
        ((datetime.fromisoformat(e["punch_time"]), e.get("direction") or "in") for e in events),
        key=lambda t: t[0],
    )
    pairs = _pair_day_punches(parsed)

    for i in range(_MAX_STORED_SESSIONS):
        n = i + 1
        if i < len(pairs):
            cin, cout = pairs[i]
            setattr(row, f"clock_in_{n}", cin)
            setattr(row, f"clock_out_{n}", cout)
            setattr(row, f"work_seconds_{n}", int((cout - cin).total_seconds()) if cout else None)
        else:
            setattr(row, f"clock_in_{n}", None)
            setattr(row, f"clock_out_{n}", None)
            setattr(row, f"work_seconds_{n}", None)

    # Total/OT are summed from EVERY closed pair, not just the first
    # _MAX_STORED_SESSIONS shown -- mirrors the precedent already in
    # daily_attendance_report.py and attendance.py's own employee-daily
    # endpoint (total_seconds = sum(... for cin, cout in pairs if cout)).
    total_seconds = sum(int((cout - cin).total_seconds()) for cin, cout in pairs if cout)
    standard_seconds = int(standard_hours * 3600)
    row.total_seconds = total_seconds
    row.ot_seconds = max(0, total_seconds - standard_seconds)
    row.under_seconds = max(0, standard_seconds - total_seconds)
    row.session_count = len(pairs)
    row.raw_events = json.dumps(events)


def _mirror_legacy_punch(
    db: Session, *, company_id: str, employee_id: str, employee_name: str | None,
    punch_time: datetime, punch_date: str, direction: str,
    device_id: str | None, device_name: str | None, source: str,
) -> None:
    """Rollback-safety write-through: keeps the legacy AttendancePunch table
    populated during the migration's safety-net window so a `git revert`
    back to pre-cutover code loses no data. Remove this call (and this
    function) in the final cleanup migration once attendance_details has
    run clean in production for the agreed observation window."""
    existing = db.query(AttendancePunch).filter(
        AttendancePunch.company_id == company_id,
        AttendancePunch.employee_id == employee_id,
        AttendancePunch.punch_time == punch_time,
        AttendancePunch.device_id == device_id,
    ).first()
    if existing:
        return
    db.add(AttendancePunch(
        company_id=company_id, employee_id=employee_id, employee_name=employee_name,
        punch_time=punch_time, punch_date=punch_date, direction=direction,
        device_id=device_id, device_name=device_name, source=source,
    ))
    try:
        db.commit()
    except IntegrityError:
        # Same event already mirrored by a concurrent request -- fine.
        db.rollback()


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
) -> dict:
    """The single write path for a punch event.

    Finds-or-creates the one AttendanceDetail row for (company_id,
    employee_id, the event's local calendar date) under a row lock, skips
    the append if an identical (punch_time, device_id) pair is already
    recorded in that day's raw_events (the same replay-safety semantics as
    the old uq_attendance_punch_dedup constraint, just scoped inside this
    row's JSON instead of a table scan), re-pairs the whole day, and writes
    every derived column. Also mirrors the event into the legacy
    AttendancePunch table for the migration's rollback-safety window.

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
        if any(e.get("punch_time") == punch_time_key and e.get("device_id") == device_id for e in events):
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

        _mirror_legacy_punch(
            db, company_id=company_id, employee_id=employee_id, employee_name=employee_name,
            punch_time=punch_time, punch_date=work_date, direction=direction,
            device_id=device_id, device_name=device_name, source=source,
        )
        return {"ok": True, "id": row.id, "event_id": event_id, "duplicate": False, "created_day_row": created}

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


def backfill_from_attendance_punches(db: Session, standard_hours: float = 8.0) -> dict:
    """One-time (but safe-to-repeat) backfill: reconstructs AttendanceDetail
    rows from the legacy AttendancePunch table by grouping punches into
    (company_id, employee_id, punch_date) and running each group through
    the same _pair_day_punches used for live writes — reusing the pairing
    logic rather than reimplementing it. Idempotent and safe to run on
    every startup during the migration's safety-net window: it skips any
    group whose AttendanceDetail row is already at least as fresh as the
    newest punch in that group, and otherwise rebuilds the row from
    scratch — this doubles as ongoing reconciliation, topping up any row
    that's gone stale relative to punches inserted via a path not yet cut
    over to attendance_store.

    Called from main.py's ensure_schema_updates(), on its own background
    thread so a large punch history (tens of thousands of groups) can never
    block the app from becoming ready / passing health checks — an earlier
    version ran this synchronously in-line and, at real production scale,
    took minutes due to the one-query-per-group design below; that risked
    every deploy either hanging past the platform's startup timeout or
    silently never finishing (0 attendance_details rows despite tens of
    thousands of punches existing, exactly what happened before this fix)."""
    groups: dict[tuple[str, str, str], list[AttendancePunch]] = {}
    for punch in db.query(AttendancePunch).order_by(AttendancePunch.punch_time.asc()).yield_per(1000):
        key = (punch.company_id, punch.employee_id, punch.punch_date)
        groups.setdefault(key, []).append(punch)

    # Bulk-fetch every existing row's freshness in ONE query instead of one
    # SELECT per group -- on a reconciliation run (the common case after the
    # first backfill) almost every group is already up to date, so this
    # turns tens of thousands of round trips into a single query plus work
    # only for groups that actually changed.
    existing_updated_at: dict[tuple[str, str, str], object] = {
        (r.company_id, r.employee_id, r.work_date): r.updated_at
        for r in db.query(
            AttendanceDetail.company_id, AttendanceDetail.employee_id,
            AttendanceDetail.work_date, AttendanceDetail.updated_at,
        )
    }

    created = updated = skipped = 0
    for key, punches in groups.items():
        company_id, employee_id, work_date = key
        latest_punch_update = max((p.updated_at or p.created_at) for p in punches)
        existing_ts = existing_updated_at.get(key)
        if existing_ts is not None and latest_punch_update is not None and existing_ts >= latest_punch_update:
            skipped += 1
            continue

        row = None
        if key in existing_updated_at:
            row = db.query(AttendanceDetail).filter(
                AttendanceDetail.company_id == company_id,
                AttendanceDetail.employee_id == employee_id,
                AttendanceDetail.work_date == work_date,
            ).first()

        sorted_punches = sorted(punches, key=lambda p: p.punch_time)
        events = [
            {
                "id": p.id,
                "punch_time": p.punch_time.isoformat(),
                "direction": p.direction or "in",
                "device_id": p.device_id,
                "device_name": p.device_name,
                "source": p.source,
                "employee_name": p.employee_name,
            }
            for p in sorted_punches
        ]
        employee_name = next((p.employee_name for p in sorted_punches if p.employee_name), None)

        if row is None:
            row = AttendanceDetail(
                company_id=company_id, employee_id=employee_id,
                employee_name=employee_name, work_date=work_date, raw_events="[]",
            )
            db.add(row)
            created += 1
        else:
            if employee_name and not row.employee_name:
                row.employee_name = employee_name
            updated += 1

        _recompute_day_fields(row, events, standard_hours)

        if (created + updated) % 500 == 0:
            db.commit()

    db.commit()
    return {"groups": len(groups), "created": created, "updated": updated, "skipped": skipped}


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
