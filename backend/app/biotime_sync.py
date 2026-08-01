"""Bridges the BioTime REST client (biotime_client.py) and TaxFlow's own
attendance tables. Used identically by the manual "Sync Now" endpoint
(routers/attendance.py) and the periodic Celery beat task (worker.py) — one
code path, so isolation and dedupe behave the same regardless of trigger.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app import biotime_client, crypto
from app.models import AttendancePunch, BiometricDevice

# Matches the fixed UAE-offset convention already used in routers/attendance.py
# (_DEVICE_UTC_OFFSET) — BioTime, like the other device sources, reports
# local wall-clock time with no timezone info attached.
_DEVICE_UTC_OFFSET = timedelta(hours=4)
_FIRST_SYNC_LOOKBACK = timedelta(hours=24)


def _local_date(punch_time_utc: datetime) -> str:
    return (punch_time_utc + _DEVICE_UTC_OFFSET).strftime("%Y-%m-%d")


def _parse_punch_time(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(raw, fmt)
            return (naive - _DEVICE_UTC_OFFSET).replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
    return None


def _map_direction(row: dict) -> str:
    """BioTime's punch_state convention varies by firmware/config; this
    covers the common ZKTeco codes (0=Check In, 1=Check Out) plus common
    human-readable variants, defaulting to "unknown" rather than guessing
    wrong when the real Transaction API shape gets reconciled later."""
    state = str(row.get("punch_state", row.get("punch_state_display", ""))).strip().lower()
    if state in ("0", "check in", "checkin", "in"):
        return "in"
    if state in ("1", "check out", "checkout", "out"):
        return "out"
    return "unknown"


def sync_biotime_device(db: Session, device: BiometricDevice) -> int:
    """Pulls transactions since the device's last sync (or the last 24h on
    first run), inserts new AttendancePunch rows, and advances last_sync.
    Every DB operation here is scoped to device.company_id / device.id —
    callers must never invoke this with a device belonging to a different
    company than the caller's own session."""
    if not device.biotime_base_url or not device.biotime_username or not device.biotime_password_enc:
        raise ValueError("BioTime connection is not fully configured")

    password = crypto.decrypt_secret(device.biotime_password_enc)
    token, expires_at = biotime_client.get_valid_token(
        device.biotime_base_url, device.biotime_username, password,
        device.biotime_token, device.biotime_token_expires_at,
    )
    if token != device.biotime_token:
        device.biotime_token = token
        device.biotime_token_expires_at = expires_at

    now = datetime.now(UTC)
    start = device.last_sync or (now - _FIRST_SYNC_LOOKBACK)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)

    rows = biotime_client.list_transactions(device.biotime_base_url, token, start, now)

    # De-dupe against this device's full punch history rather than only
    # what's >= `start` — `start` is `last_sync`, which advances every run,
    # so a transaction BioTime re-returns from an overlapping window (or a
    # backfilled older punch) would otherwise fall outside the dedupe check
    # on a later sync and get inserted twice.
    # Keyed on isoformat strings, not raw datetimes — SQLite round-trips
    # DateTime(timezone=True) columns as naive (tzinfo stripped), so comparing
    # a freshly-parsed tz-aware datetime against one read back from the DB
    # would silently never match and defeat the dedupe entirely.
    def _key(emp_id: str, dt: datetime) -> tuple[str, str]:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (emp_id, dt.isoformat())

    existing = db.query(AttendancePunch.employee_id, AttendancePunch.punch_time).filter(
        AttendancePunch.company_id == device.company_id,
        AttendancePunch.device_id == device.id,
    ).all()
    seen = {_key(emp_id, punch_time) for emp_id, punch_time in existing}

    inserted = 0
    for row in rows:
        emp_code = str(row.get("emp_code") or row.get("employee") or "").strip()
        raw_time = row.get("punch_time") or row.get("upload_time")
        if not emp_code or not raw_time:
            continue
        punch_time = _parse_punch_time(str(raw_time))
        if not punch_time:
            continue
        key = _key(emp_code, punch_time)
        if key in seen:
            continue
        seen.add(key)
        db.add(AttendancePunch(
            company_id=device.company_id,
            employee_id=emp_code,
            employee_name=row.get("emp_name") or row.get("first_name"),
            punch_time=punch_time,
            punch_date=_local_date(punch_time),
            direction=_map_direction(row),
            device_id=device.id,
            device_name=row.get("terminal_alias") or row.get("terminal_sn") or device.name,
            source="biotime",
        ))
        inserted += 1

    device.last_sync = now
    db.add(device)
    db.commit()
    return inserted
