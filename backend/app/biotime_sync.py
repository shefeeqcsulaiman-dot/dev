"""Bridges the BioTime REST client (biotime_client.py) and TaxFlow's own
attendance tables. Used identically by the manual "Sync Now" endpoint
(routers/attendance.py) and the periodic Celery beat task (worker.py) — one
code path, so isolation and dedupe behave the same regardless of trigger.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

import app.timezone_utils as timezone_utils
from app import attendance_store, biotime_client, crypto
from app.models import BiometricDevice, Company

_FIRST_SYNC_LOOKBACK = timedelta(hours=24)


def _parse_punch_time(raw: str, offset: timedelta) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(raw, fmt)
            return (naive - offset).replace(tzinfo=UTC)
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
    first run) and writes each one through attendance_store's shared
    upsert_attendance_event() — the same concurrency-safe dedup/pairing/
    legacy-mirroring used by the device webhook path and approved
    corrections, replacing this function's own in-memory `seen`-set
    preload and manual IntegrityError recovery (both now redundant: the
    shared upsert does its own per-event dedup under a row lock). Every DB
    operation here is scoped to device.company_id / device.id — callers
    must never invoke this with a device belonging to a different company
    than the caller's own session."""
    if not device.biotime_base_url or not device.biotime_username or not device.biotime_password_enc:
        raise ValueError("BioTime connection is not fully configured")

    company_country = db.query(Company.country).filter(Company.id == device.company_id).scalar()
    offset = timezone_utils.company_utc_offset(company_country)

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

    inserted = 0
    for row in rows:
        emp_code = str(row.get("emp_code") or row.get("employee") or "").strip()
        raw_time = row.get("punch_time") or row.get("upload_time")
        if not emp_code or not raw_time:
            continue
        punch_time = _parse_punch_time(str(raw_time), offset)
        if not punch_time:
            continue
        result = attendance_store.upsert_attendance_event(
            db,
            company_id=device.company_id,
            employee_id=emp_code,
            punch_time=punch_time,
            direction=_map_direction(row),
            employee_name=row.get("emp_name") or row.get("first_name"),
            device_id=device.id,
            device_name=row.get("terminal_alias") or row.get("terminal_sn") or device.name,
            source="biotime",
        )
        if result.get("ok") and not result.get("duplicate"):
            inserted += 1

    device.last_sync = now
    db.add(device)
    db.commit()
    return inserted
