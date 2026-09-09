"""Attendance & Biometric Device management.

Supported punch sources:
  - ZKTeco TCP/IP (via zk_bridge.py running on-premises)
  - ADMS / Cloud Server / HTTP Push / Web Service (ZKTeco ADMS, Suprema, Hikvision, Anviz —
    exact menu name varies by manufacturer and firmware)
  - CSV import
  - Manual entry
"""

import csv
import hashlib
import io
import ipaddress
import json
import logging
import pathlib
import re
import secrets
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.cache as cache
import app.timezone_utils as timezone_utils
from app import attendance_store, biotime_client, biotime_sync, crypto
from app.attendance_store import _pair_day_punches
from app.database import get_db
from app.auth_principal import resolve_active_branch
from app.dependencies import Principal, get_current_user, require_module, require_principal_permission
from app.limiter import limiter
from app.models import AppDataRecord, AttendanceDetail, BiometricDevice, Company, Employee, LeaveRequest, User
from app.security import verify_password, hash_password

# router carries only the device-facing punch/adms endpoints (auth is via
# _optional_user / device key, not a login — devices never have a company
# session token, so they can't be gated the normal way). Every other
# (logged-in-user) endpoint lives on gated_router, which requires the "hrms"
# module server-side — same reasoning as the hr_access.py router/gated_router
# split.
router = APIRouter(prefix="/attendance", tags=["attendance"])
gated_router = APIRouter(prefix="/attendance", tags=["attendance"], dependencies=[Depends(require_module("hrms"))])

# Prefix-less router for short, device-friendly URL aliases (e.g. /api/v1/punch
# instead of /api/v1/attendance/punch). Physical devices/firmware admin panels
# have this URL manually typed into a small on-device form, so shorter matters
# here in a way it doesn't for the CRUD endpoints our own frontend calls.
short_router = APIRouter(tags=["attendance"])

log = logging.getLogger("taxflow")

CIRC = 238.76  # SVG donut circumference reference (unused here, kept for JS)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PunchIn(BaseModel):
    employee_id: str
    employee_name: str | None = None
    punch_time: str | None = None       # ISO 8601; defaults to now
    direction: str = "in"               # in | out | unknown
    device_name: str | None = None


class DeviceCreate(BaseModel):
    name: str
    device_type: str = "ZKTeco F Series"  # ZKTeco F/K/iClock/SpeedFace/ProFace/G/UA/IN/MB Series | ZKTeco ADMS | ZKTeco ADMS Classic | Suprema | Hikvision | Anviz | ZKTeco BioTime Server | Manual
    ip_address: str | None = None
    port: int = 4370
    location: str | None = None
    # BioTime server connection only (device_type == "ZKTeco BioTime Server")
    biotime_base_url: str | None = None
    biotime_username: str | None = None
    biotime_password: str | None = None
    # ZKTeco ADMS Classic only (device_type == "ZKTeco ADMS Classic") — the
    # device's own hardware serial number, since real ADMS identifies itself
    # this way instead of a bearer key (see iclock_router below).
    serial_number: str | None = None


class DeviceOut(BaseModel):
    id: str
    name: str
    device_type: str
    ip_address: str | None
    port: int
    location: str | None
    status: str
    last_sync: str | None
    biotime_base_url: str | None = None
    biotime_username: str | None = None
    serial_number: str | None = None


# ── helpers ───────────────────────────────────────────────────────────────────

# Devices (ZKTeco/Suprema/Hikvision/Anviz) and CSV imports report their own
# local wall-clock time with no timezone info — treating that naive value as
# if it were already UTC (rather than converting it) makes every punch
# appear hours in the future and get rejected. The conversion offset used to
# be a single hardcoded UAE+4 constant here, silently wrong for any other
# GCC company — see timezone_utils.company_utc_offset(), resolved per
# company at each call site below.
_DEVICE_UTC_OFFSET = timedelta(hours=timezone_utils._DEFAULT_OFFSET_HOURS)


def _parse_time(ts: str | None, offset: timedelta = _DEVICE_UTC_OFFSET) -> datetime:
    if not ts:
        return datetime.now(UTC)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y %H:%M:%S"):
        try:
            naive = datetime.strptime(ts, fmt)
            if fmt.endswith("Z"):
                return naive.replace(tzinfo=UTC)
            return (naive - offset).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(UTC)


def _local_date(punch_time_utc: datetime, offset: timedelta = _DEVICE_UTC_OFFSET) -> str:
    """Calendar date in the company's local time — punches near midnight UTC
    must not bucket into the wrong business day."""
    return (punch_time_utc + offset).strftime("%Y-%m-%d")


def _local_today(offset: timedelta = _DEVICE_UTC_OFFSET):
    """Today's date in the company's local time, matching how punch_date is bucketed."""
    return (datetime.now(UTC) + offset).date()


def _company_offset(db: Session, company_id: str) -> timedelta:
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    return timezone_utils.company_utc_offset(country)


def _get_device_company(x_device_key: str, db: Session) -> tuple[str, BiometricDevice]:
    """Resolve company_id from device API key (for ZK bridge / webhook auth).

    Bcrypt-verifying against every active device across every company on
    every single punch is expensive (~100-300ms each) and can't be scoped
    down before a match is found — a request carrying ANY non-empty key
    string forces this full scan, with no authentication required to
    trigger it. A short-TTL cache (keyed by a one-way SHA-256 hash of the
    actual key, never the key itself) short-circuits the *repeat* lookups a
    legitimate device makes every ~30s; a genuinely invalid/attacker key
    still forces a full scan on every attempt, but that's already bounded
    by the 60/minute per-IP rate limit on the routes that call this."""
    cache_key = f"device_key:{hashlib.sha256(x_device_key.encode()).hexdigest()}"
    cached_device_id = cache.get(cache_key)
    if cached_device_id:
        device = db.query(BiometricDevice).filter(
            BiometricDevice.id == cached_device_id, BiometricDevice.status == "active"
        ).first()
        if device:
            return device.company_id, device
    devices = db.query(BiometricDevice).filter(
        BiometricDevice.status == "active",
        BiometricDevice.api_key_hash.isnot(None),
    ).all()
    for device in devices:
        try:
            if verify_password(x_device_key, device.api_key_hash):
                cache.set(cache_key, device.id, ttl=300)
                return device.company_id, device
        except Exception:
            continue
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device key")


def require_device_admin(current_user: User = Depends(get_current_user)) -> User:
    """Device management issues one-time API keys and decrypts/uses stored
    BioTime passwords (Test Connection, Sync Now) — restrict to company
    admins, matching the pattern already used for other destructive/
    credential-sensitive actions (see app_data.py's wipe-company-data
    check). Read-only listing (list_devices) stays open to any HRMS user;
    DeviceOut never includes a credential."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only company admins can manage biometric devices")
    return current_user


# ── Device management ─────────────────────────────────────────────────────────

@gated_router.get("/devices")
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DeviceOut]:
    devices = db.query(BiometricDevice).filter(
        BiometricDevice.company_id == current_user.company_id,
        BiometricDevice.status != "deleted",
    ).all()
    return [DeviceOut(
        id=d.id,
        name=d.name,
        device_type=d.device_type,
        ip_address=d.ip_address,
        port=d.port,
        location=d.location,
        status=d.status,
        last_sync=d.last_sync.isoformat() if d.last_sync else None,
        biotime_base_url=d.biotime_base_url,
        biotime_username=d.biotime_username,
        serial_number=d.serial_number,
    ) for d in devices]


@gated_router.post("/devices", status_code=201)
def add_device(
    body: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_device_admin),
) -> dict[str, Any]:
    # BioTime Server: a pull connection to the customer's own BioTime
    # install, not a device we issue a push key to — no api_key_hash.
    if body.device_type in _BIOTIME_TYPES:
        if not body.biotime_base_url or not body.biotime_username or not body.biotime_password:
            raise HTTPException(400, "Base URL, username and password are required for a BioTime Server connection")
        device = BiometricDevice(
            company_id=current_user.company_id,
            name=body.name,
            device_type=body.device_type,
            location=body.location,
            biotime_base_url=body.biotime_base_url.strip(),
            biotime_username=body.biotime_username.strip(),
            biotime_password_enc=crypto.encrypt_secret(body.biotime_password),
            status="active",
        )
        db.add(device)
        db.commit()
        db.refresh(device)
        return {
            "id": device.id,
            "name": device.name,
            "device_type": device.device_type,
            "message": "BioTime connection saved. Click Sync Now to pull attendance, or wait for the next automatic sync (every 5 minutes).",
        }

    # ZKTeco ADMS Classic: identified by hardware serial number, not a
    # bearer key — the device's own menu only has a Server IP + Port field,
    # nothing to paste a generated key into.
    if body.device_type in _ADMS_CLASSIC_TYPES:
        serial = (body.serial_number or "").strip()
        if not serial:
            raise HTTPException(400, "Device serial number is required for ZKTeco ADMS Classic")
        existing = db.query(BiometricDevice).filter(
            BiometricDevice.serial_number == serial,
            BiometricDevice.status == "active",
        ).first()
        if existing:
            raise HTTPException(409, "A device with this serial number is already registered")
        device = BiometricDevice(
            company_id=current_user.company_id,
            name=body.name,
            device_type=body.device_type,
            serial_number=serial,
            location=body.location,
            status="active",
        )
        db.add(device)
        db.commit()
        db.refresh(device)
        return {
            "id": device.id,
            "name": device.name,
            "device_type": device.device_type,
            "serial_number": device.serial_number,
            "message": "Device registered. On the device's own menu, set Server IP/Port to point at this server — no API key needed, the device identifies itself by its serial number.",
        }

    raw_key = secrets.token_urlsafe(32)
    device = BiometricDevice(
        company_id=current_user.company_id,
        name=body.name,
        device_type=body.device_type,
        ip_address=body.ip_address,
        port=body.port,
        location=body.location,
        api_key_hash=hash_password(raw_key),
        status="active",
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return {
        "id": device.id,
        "name": device.name,
        "device_type": device.device_type,
        "api_key": raw_key,          # shown only once — store it in zk_bridge.py config
        "message": "Save this API key — it will not be shown again.",
    }


@gated_router.delete("/devices/{device_id}", status_code=204, response_model=None)
def delete_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_device_admin),
) -> None:
    device = db.query(BiometricDevice).filter(
        BiometricDevice.id == device_id,
        BiometricDevice.company_id == current_user.company_id,
    ).first()
    if not device:
        raise HTTPException(404, "Device not found")
    device.status = "deleted"
    db.commit()


_TCP_TYPES = {
    "ZKTeco F Series", "ZKTeco K Series", "ZKTeco iClock",
    "ZKTeco X Face Pro", "ZKTeco SpeedFace", "ZKTeco ProFace",
    "ZKTeco G Series", "ZKTeco UA Series", "ZKTeco IN Series",
    "ZKTeco MB Series", "ZKTeco", "Anviz",
}
_PUSH_TYPES = {"Suprema", "Hikvision", "ZKTeco ADMS"}
_BIOTIME_TYPES = {"ZKTeco BioTime Server"}
# Real ZKTeco ADMS Cloud Server Mode — identified by hardware serial number
# via the iclock_router routes below, not the bearer-key flow _PUSH_TYPES uses.
_ADMS_CLASSIC_TYPES = {"ZKTeco ADMS Classic"}


def _count_matching_events(
    db: Session, company_id: str, *, device_id: str | None = None, source: str | None = None,
    since: datetime | None = None,
) -> int:
    """Scan AttendanceDetail.raw_events for events matching the given
    filters -- used only by test_device()'s rare, admin-triggered
    connectivity diagnostics (not a hot path), so an in-Python scan is an
    acceptable trade-off rather than a dedicated indexed counter."""
    query = db.query(AttendanceDetail.raw_events).filter(AttendanceDetail.company_id == company_id)
    if since is not None:
        query = query.filter(AttendanceDetail.updated_at >= since)
    count = 0
    for (raw,) in query.all():
        try:
            events = json.loads(raw or "[]")
        except (ValueError, TypeError):
            continue
        for e in events:
            if device_id is not None and e.get("device_id") != device_id:
                continue
            if source is not None and e.get("source") != source:
                continue
            if since is not None:
                try:
                    ts = datetime.fromisoformat(e["punch_time"])
                except (KeyError, ValueError, TypeError):
                    continue
                if ts < since:
                    continue
            count += 1
    return count


@gated_router.post("/devices/{device_id}/test")
def test_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_device_admin),
) -> dict[str, Any]:
    device = db.query(BiometricDevice).filter(
        BiometricDevice.id == device_id,
        BiometricDevice.company_id == current_user.company_id,
    ).first()
    if not device:
        raise HTTPException(404, "Device not found")

    now = datetime.now(UTC)

    # ── BioTime Server: authenticate + list terminals as the connectivity check ──
    if device.device_type in _BIOTIME_TYPES:
        if not device.biotime_base_url or not device.biotime_username or not device.biotime_password_enc:
            return {"ok": False, "message": "BioTime connection is not fully configured"}
        try:
            password = crypto.decrypt_secret(device.biotime_password_enc)
            token = biotime_client.get_token(device.biotime_base_url, device.biotime_username, password)
            terminals = biotime_client.list_terminals(device.biotime_base_url, token)
        except (biotime_client.BioTimeError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        device.biotime_token = token
        device.biotime_token_expires_at = now + timedelta(hours=6)
        db.commit()
        names = ", ".join(t.get("alias") or t.get("sn") or "?" for t in terminals[:5])
        more = f" (+{len(terminals) - 5} more)" if len(terminals) > 5 else ""
        return {"ok": True, "message": f"Connected — {len(terminals)} terminal(s) on this BioTime server: {names}{more}"}

    # ── HTTP Push / ADMS devices: they call us, we can't call them ────────────
    if device.device_type in _PUSH_TYPES or device.device_type in _ADMS_CLASSIC_TYPES:
        week_ago = now - timedelta(days=7)
        recent = _count_matching_events(db, current_user.company_id, device_id=device.id, since=week_ago)

        if device.last_sync:
            delta = now - device.last_sync.replace(tzinfo=UTC) if device.last_sync.tzinfo is None else now - device.last_sync
            secs = int(delta.total_seconds())
            if secs < 120:
                age = f"{secs}s ago"
            elif secs < 3600:
                age = f"{secs//60} min ago"
            elif secs < 86400:
                age = f"{secs//3600} hr ago"
            else:
                age = f"{secs//86400} day(s) ago"

            if secs < 86400:
                return {"ok": True, "message": f"Last punch received {age} · {recent} punches in last 7 days"}
            else:
                return {"ok": False, "message": f"No punch in {secs//86400} day(s) — check device push settings and API key"}
        else:
            return {"ok": False, "message": f"{device.device_type}: no punches received yet — open Setup Guide and configure the device to push to this server"}

    # ── Manual / CSV: no connection to test ────────────────────────────────────
    if device.device_type == "Manual":
        total = _count_matching_events(db, current_user.company_id, source="csv")
        return {"ok": True, "message": f"CSV import device — {total} records imported total"}

    # ── TCP/IP devices: socket reachability check ──────────────────────────────
    if not device.ip_address:
        return {"ok": False, "message": "No IP address configured — add the device IP to test connectivity"}

    week_ago = now - timedelta(days=7)
    recent = _count_matching_events(db, current_user.company_id, device_id=device.id, since=week_ago)

    # TCP/IP-mode devices exist specifically because they sit on a private LAN
    # (that's the whole reason zk_bridge.py needs to run locally at all) —
    # this endpoint runs on TaxFlow's own cloud servers, which can never
    # actually reach a private-range IP. Attempting the socket connect for
    # those previously produced a near-guaranteed, actively misleading
    # "Timeout — device offline or wrong IP?" for every correctly-configured
    # device. Fall back to the same recent-punch-count heuristic already used
    # for push-type devices a few lines up instead of a doomed network call.
    try:
        is_private = ipaddress.ip_address(device.ip_address).is_private
    except ValueError:
        is_private = False

    if is_private:
        if recent:
            return {"ok": True, "message": f"{recent} punches synced in the last 7 days — zk_bridge.py is running and reachable on the device's local network"}
        return {
            "ok": False,
            "message": (
                f"No punches synced yet. {device.ip_address} is a private/local network address — "
                "TaxFlow's servers can't test it directly. Make sure zk_bridge.py is running on a PC "
                "on the same network as the device (see Setup Guide)."
            ),
        }

    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect((device.ip_address, device.port))
        s.close()
        sync_note = f" · {recent} punches in last 7 days" if recent else " · no punches synced yet (is zk_bridge.py running?)"
        return {"ok": True, "message": f"Reachable — {device.ip_address}:{device.port} is open{sync_note}"}
    except socket.timeout:
        return {"ok": False, "message": f"Timeout — {device.ip_address}:{device.port} did not respond (device offline or wrong IP?)"}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"Connection refused on {device.ip_address}:{device.port} — verify IP and port"}
    except OSError as e:
        return {"ok": False, "message": f"Cannot reach {device.ip_address}:{device.port} — {e.strerror}"}


@gated_router.post("/devices/{device_id}/biotime/sync")
def sync_biotime_device_now(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_device_admin),
) -> dict[str, Any]:
    """Manual "Sync Now" — pulls attendance from the company's own BioTime
    server. Same code path (biotime_sync.sync_biotime_device) the periodic
    Celery beat task uses, so behavior is identical whether triggered here
    or automatically."""
    device = db.query(BiometricDevice).filter(
        BiometricDevice.id == device_id,
        BiometricDevice.company_id == current_user.company_id,
        BiometricDevice.device_type.in_(_BIOTIME_TYPES),
    ).first()
    if not device:
        raise HTTPException(404, "BioTime device not found")
    try:
        inserted = biotime_sync.sync_biotime_device(db, device)
    except (biotime_client.BioTimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "synced": inserted, "message": f"Synced {inserted} new punch record(s)."}


def _optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """FastAPI dependency: extract user from Bearer token without raising if missing."""
    from app.security import user_id_from_token
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    uid = user_id_from_token(token)
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


# ── Punch recording ───────────────────────────────────────────────────────────
#
# Device compatibility notes:
#   - Not every ADMS/cloud-push device can send a custom HTTP header, so the
#     device key is accepted three ways: header (X-Device-Key), query string
#     (?device_key=... / ?deviceKey=...), or as a URL path segment
#     (/adms/{device_key}) — whichever the device firmware supports.
#   - Not every device sends JSON. The body is sniffed by Content-Type and
#     falls back to form-urlencoded, then to raw "key=value&..." pairs, before
#     giving up. This covers common webhook/ADMS-style integrations; it is
#     NOT an implementation of ZKTeco's native /iclock/cdata SN+ATTLOG wire
#     protocol, which is a materially different (non-HTTP-webhook) protocol.

async def _parse_punch_body(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        return await request.json()
    if "form" in content_type:
        form = await request.form()
        return dict(form)
    raw = (await request.body()).decode("utf-8", errors="ignore").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    pairs = parse_qsl(raw)
    if pairs:
        return dict(pairs)
    raise HTTPException(
        status_code=422,
        detail="Unsupported request body — send JSON, form-urlencoded, or key=value pairs",
    )


# Older/locally-edited copies of daily_attendance_report.py push a wide,
# aggregated daily-report row ({"Date": "DD/MM/YYYY", "Clock In 1": "HH:MM",
# "Clock Out 1": "HH:MM", ...} — the same shape as the downloadable CSV
# report) instead of the raw single-punch shape ({"employee_id",
# "punch_time", "direction"}) the fixed version of that script and
# zk_bridge.py/biotime_agent.py send. Before this, PunchIn's pydantic model
# silently ignored every "Clock In N"/"Date" field it didn't recognize,
# defaulted punch_time to "now" (the push time, not the real scan time),
# and inserted a punch for every row regardless of whether the employee
# actually clocked in that day — an absent employee's blank-"Clock In 1"
# row still became a false "in" punch. Detecting and parsing this shape
# properly here means the office PC's script never needs to be updated for
# this to be fixed.
_WIDE_PAYLOAD_SESSION_COUNT = 5


def _is_wide_report_payload(raw_data: dict[str, Any]) -> bool:
    return "Clock In 1" in raw_data


def _parse_wide_report_events(raw_data: dict[str, Any], offset: timedelta) -> list[tuple[datetime, str]]:
    """Extract individual (punch_time_utc, direction) events from a wide
    daily-report-shaped payload. A row with no "Clock In"/"Clock Out" values
    at all (an absent day) yields zero events — deliberately: that's what
    stops an absent employee's placeholder row from registering as a real
    punch, which is exactly the bug class this parsing exists to avoid."""
    date_raw = str(raw_data.get("Date") or "").strip()
    if not date_raw:
        return []
    try:
        datetime.strptime(date_raw, "%d/%m/%Y")
    except ValueError:
        return []

    events: list[tuple[datetime, str]] = []
    for n in range(1, _WIDE_PAYLOAD_SESSION_COUNT + 1):
        for field, direction in ((f"Clock In {n}", "in"), (f"Clock Out {n}", "out")):
            time_raw = str(raw_data.get(field) or "").strip()
            if not time_raw:
                continue
            try:
                local_dt = datetime.strptime(f"{date_raw} {time_raw}", "%d/%m/%Y %H:%M")
            except ValueError:
                continue
            events.append(((local_dt - offset).replace(tzinfo=UTC), direction))
    return events


def _ingest_device_punch(
    db: Session,
    company_id: str,
    device_id: str | None,
    device_name: str,
    employee_id: str,
    punch_time: datetime,
    direction: str = "unknown",
    employee_name: str | None = None,
    source: str = "device",
) -> dict[str, Any]:
    """Shared punch-insert core used by both the single-punch webhook path
    (_record_punch, below) and the ADMS-classic batch upload handler
    (adms_upload) — one dedupe/idempotency/insert implementation instead of
    two. Returns {"ok": False, "error": "future"|"too_old"} for a rejected
    punch rather than raising, since a batch upload must skip one bad line
    and keep processing the rest instead of failing the whole request.

    Delegates the actual write to attendance_store.upsert_attendance_event()
    — the single, concurrency-safe upsert shared by every ingestion path
    (this one, BioTime sync, approved attendance corrections). The
    returned "id" is the individual scan event's id (not the day-row's),
    matching what /punches (Sync Activity Log) and DELETE /punches/{id}
    already expect an "id" to mean."""
    now = datetime.now(UTC)
    if punch_time > now + timedelta(minutes=5):
        return {"ok": False, "error": "future"}
    # Reject punches older than 90 days (prevents replay / mass backdating
    # attacks). Previously gated behind `if device_id:`, so the CSV import
    # path (device_id always None — an admin file upload, not a device) and
    # manual single punches skipped this entirely; there's nothing
    # device-specific about the risk this guards against.
    if punch_time < now - timedelta(days=90):
        return {"ok": False, "error": "too_old"}

    result = attendance_store.upsert_attendance_event(
        db,
        company_id=company_id,
        employee_id=employee_id,
        punch_time=punch_time,
        direction=direction,
        employee_name=employee_name,
        device_id=device_id,
        device_name=device_name,
        source=source,
    )
    if not result.get("ok"):
        return result
    return {"ok": True, "id": result.get("event_id") or result["id"], "duplicate": result.get("duplicate", False)}


async def _record_punch(request: Request, db: Session, current_user: User | None, device_key: str | None) -> dict[str, Any]:
    if current_user:
        company_id = current_user.company_id
        device_name = "Manual"
        device_id = None
    elif device_key:
        company_id, device = _get_device_company(device_key, db)
        device_name = device.name
        device_id = device.id
        device.last_sync = datetime.now(UTC)
    else:
        raise HTTPException(status_code=401, detail="Authentication required")

    raw_data = await _parse_punch_body(request)
    offset = _company_offset(db, company_id)

    if _is_wide_report_payload(raw_data):
        employee_id = str(raw_data.get("employee_id") or raw_data.get("Emp No.") or "").strip()
        if not employee_id:
            raise HTTPException(status_code=422, detail="employee_id (or 'Emp No.') is required")
        employee_name = raw_data.get("employee_name") or raw_data.get("Name") or None
        events = _parse_wide_report_events(raw_data, offset)
        if not events:
            # Nothing to record (e.g. an absent-day placeholder row with no
            # Clock In/Out at all) — explicitly not an error, and
            # explicitly not a punch.
            return {"ok": True, "inserted": 0, "duplicates": 0, "events": 0}
        inserted = duplicates = rejected = 0
        for punch_time, direction in events:
            result = _ingest_device_punch(
                db, company_id, device_id, device_name,
                employee_id, punch_time, direction, employee_name,
                source="device" if device_key else "manual",
            )
            if not result.get("ok"):
                rejected += 1
            elif result.get("duplicate"):
                duplicates += 1
            else:
                inserted += 1
        return {"ok": True, "inserted": inserted, "duplicates": duplicates, "rejected": rejected, "events": len(events)}

    try:
        body = PunchIn(**raw_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    punch_time = _parse_time(body.punch_time, offset)

    result = _ingest_device_punch(
        db, company_id, device_id, device_name,
        body.employee_id, punch_time, body.direction, body.employee_name,
        source="device" if device_key else "manual",
    )
    if result.get("error") == "future":
        raise HTTPException(status_code=422, detail="Punch time is in the future")
    if result.get("error") == "too_old":
        raise HTTPException(status_code=422, detail="Punch time is too old (>90 days)")
    return result


@router.post("/punch", status_code=201)
@limiter.limit("60/minute")
async def record_punch(
    request: Request,
    x_device_key: str | None = Header(default=None),
    device_key: str | None = Query(default=None),
    deviceKey: str | None = Query(default=None),  # noqa: N803 - matches device-side URL convention
    db: Session = Depends(get_db),
    current_user: User | None = Depends(_optional_user),
) -> dict[str, Any]:
    """Accept a punch via X-Device-Key header, ?device_key=/?deviceKey= query string,
    or an authenticated user (manual entry)."""
    return await _record_punch(request, db, current_user, x_device_key or device_key or deviceKey)


@router.post("/punch/{path_device_key}", status_code=201)
@limiter.limit("60/minute")
async def record_punch_key_in_path(
    request: Request,
    path_device_key: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(_optional_user),
) -> dict[str, Any]:
    """Same as /punch, but the device key travels in the URL path — for firmware
    that can't send a custom header or a query string reliably."""
    return await _record_punch(request, db, current_user, path_device_key)


# Short + descriptive aliases — same handlers, same rate limit and auth, just
# friendlier/shorter URLs for device push-config screens (physical devices
# often have these typed by hand into a small on-device form). The original
# /attendance/punch keeps working for anything already configured with it.
short_router.add_api_route(
    "/punch", record_punch, methods=["POST"], status_code=201,
    summary="Short alias for /attendance/punch",
)
short_router.add_api_route(
    "/punch/{path_device_key}", record_punch_key_in_path, methods=["POST"], status_code=201,
    summary="Short alias for /attendance/punch, device key in the URL path",
)
router.add_api_route(
    "/adms", record_punch, methods=["POST"], status_code=201,
    summary="Descriptive alias for /attendance/punch (ADMS/cloud-push devices)",
)
router.add_api_route(
    "/adms/{path_device_key}", record_punch_key_in_path, methods=["POST"], status_code=201,
    summary="Descriptive alias for /attendance/punch, device key in the URL path",
)
short_router.add_api_route(
    "/adms", record_punch, methods=["POST"], status_code=201,
    summary="Short descriptive alias for /attendance/punch (ADMS/cloud-push devices)",
)
short_router.add_api_route(
    "/adms/{path_device_key}", record_punch_key_in_path, methods=["POST"], status_code=201,
    summary="Short descriptive alias, device key in the URL path",
)


# ── ZKTeco ADMS Classic (real iClock wire protocol) ─────────────────────────
#
# Everything above (record_punch/adms aliases) is a webhook-style endpoint:
# it accepts JSON/form/query bodies at whatever URL the device's own "custom
# server" field is pointed at, authenticated by a bearer-style device key.
# That only works for devices whose firmware actually lets you type a custom
# URL and a key/header. A device limited to genuine ZKTeco "ADMS Cloud
# Server Mode" instead has just a fixed Server IP + Port field — no
# custom path, no header, no key. Its firmware hardcodes three request
# shapes it will always make to whatever host:port you give it:
#   GET  /iclock/cdata?SN=<serial>&...      (handshake / option negotiation)
#   POST /iclock/cdata?SN=<serial>&table=ATTLOG   (tab-separated punch batch)
#   GET  /iclock/getrequest?SN=<serial>     (poll for pending remote commands)
# identified by the device's own hardware serial number, not a key — so
# this needs its own router mounted at the true server root (main.py),
# not under /api/v1 like everything else, since the device can't be told
# to use any other path.
iclock_router = APIRouter(tags=["attendance"])


def _get_device_by_serial(db: Session, serial_number: str) -> BiometricDevice:
    device = db.query(BiometricDevice).filter(
        BiometricDevice.serial_number == serial_number,
        BiometricDevice.status == "active",
    ).first()
    if not device:
        # Deliberately not auto-registering an unknown serial — same
        # provision-before-connect model as the device-key flow (add_device()
        # above), where an admin must add the device (and here, type in its
        # serial number) before it's allowed to push anything.
        raise HTTPException(status_code=404, detail="Unknown device serial number — add this device in Biometric Integration first")
    return device


@iclock_router.get("/iclock/cdata")
@limiter.limit("60/minute")
def adms_classic_handshake(
    request: Request,
    SN: str = Query(...),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """Handshake the device makes on startup/reconnect before it starts
    POSTing data. Real firmware mostly just needs a 200 with this key=value
    shape to consider the server reachable and move on to uploading —
    exact tuning of these values (poll delay etc.) may need adjusting
    against real hardware; kept deliberately conservative here."""
    device = _get_device_by_serial(db, SN)
    device.last_sync = datetime.now(UTC)
    db.commit()
    lines = [
        f"GET OPTION FROM: {SN}",
        "Stamp=9999",
        "OpStamp=9999",
        "ErrorDelay=30",
        "Delay=30",
        "TransFlag=1111000000",
        "Realtime=1",
        "Encrypt=None",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@iclock_router.post("/iclock/cdata")
@limiter.limit("60/minute")
async def adms_classic_upload(
    request: Request,
    SN: str = Query(...),
    table: str = Query(default="ATTLOG"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """Batch punch upload. Real ADMS ATTLOG bodies are one punch per line,
    tab-separated: <UserID>\\t<Timestamp>\\t<Status>\\t<VerifyMode>\\t...
    (Status 1 = check-out, anything else treated as check-in — firmware
    varies on this, it's a best-effort mapping). Bad/unparseable lines are
    skipped rather than failing the whole batch, since one malformed line
    from a device shouldn't drop the rest of its backlog."""
    device = _get_device_by_serial(db, SN)
    if table.upper() != "ATTLOG":
        # OPERLOG (enrollment/user-management data) or other tables — nothing
        # for us to ingest, just acknowledge so the device doesn't retry forever.
        return PlainTextResponse("OK")

    raw = (await request.body()).decode("utf-8", errors="ignore")
    offset = _company_offset(db, device.company_id)
    count = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        user_id, ts_raw = parts[0].strip(), parts[1].strip()
        status_code = parts[2].strip() if len(parts) > 2 else ""
        if not user_id or not ts_raw:
            continue
        direction = "out" if status_code == "1" else "in"
        punch_time = _parse_time(ts_raw, offset)
        result = _ingest_device_punch(
            db, device.company_id, device.id, device.name,
            user_id, punch_time, direction, source="device",
        )
        if result.get("ok"):
            count += 1

    device.last_sync = datetime.now(UTC)
    db.commit()
    return PlainTextResponse(f"OK: {count}")


@iclock_router.get("/iclock/getrequest")
@limiter.limit("60/minute")
def adms_classic_get_request(
    request: Request,
    SN: str = Query(...),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """Device polls this for pending remote commands (e.g. reboot, sync
    time). We never queue any, so always acknowledge empty — the device
    just moves on and tries again on its own schedule."""
    _get_device_by_serial(db, SN)
    return PlainTextResponse("OK")


# ── CSV Import ────────────────────────────────────────────────────────────────

@gated_router.post("/import-csv", status_code=201)
async def import_csv(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Import attendance from CSV.

    Accepted columns (case-insensitive, order-independent):
      employee_id, employee_name, punch_time (YYYY-MM-DD HH:MM:SS), direction
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    required = {"employee_id", "punch_time"}
    if not required.issubset(headers):
        raise HTTPException(400, f"CSV must contain columns: {', '.join(required)}. Found: {', '.join(headers)}")

    offset = _company_offset(db, current_user.company_id)
    inserted = 0
    skipped = 0
    duplicates = 0
    rejected = 0
    for row in reader:
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        emp_id = norm.get("employee_id", "")
        ts_raw = norm.get("punch_time", "")
        if not emp_id or not ts_raw:
            skipped += 1
            continue
        punch_time = _parse_time(ts_raw, offset)
        # Previously built and inserted an AttendancePunch row directly here,
        # bypassing every check the device/manual ingestion paths go
        # through — a future-dated or 90+ day old row imported without
        # complaint, and re-uploading the same CSV file duplicated every
        # single punch in it forever (no device_id for a NULL-safe DB
        # constraint to catch). Routing through the shared helper gives CSV
        # rows the exact same future/90-day/dedupe guarantees.
        result = _ingest_device_punch(
            db, current_user.company_id, None, "CSV Import",
            emp_id, punch_time, norm.get("direction", "in"),
            norm.get("employee_name") or None, source="csv",
        )
        if result.get("ok"):
            if result.get("duplicate"):
                duplicates += 1
            else:
                inserted += 1
        else:
            rejected += 1

    return {"imported": inserted, "skipped": skipped, "duplicates": duplicates, "rejected": rejected}


class _ImportRowsBody(BaseModel):
    rows: list[dict[str, Any]]


def _parse_import_clock_field(time_raw: str, work_date: str, offset: timedelta) -> datetime | None:
    """Accepts either a full ISO datetime -- "2026-09-09T07:29:00+04:00"
    (offset-aware, converted directly) or "2026-09-09T07:29:00" (naive,
    treated as company-local time) -- or a plain "HH:MM" local-time string
    combined with the row's work_date. Returns None if neither parses."""
    try:
        dt = datetime.fromisoformat(time_raw)
        return dt.astimezone(UTC) if dt.tzinfo is not None else (dt - offset).replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        local_dt = datetime.strptime(f"{work_date} {time_raw}", "%Y-%m-%d %H:%M")
        return (local_dt - offset).replace(tzinfo=UTC)
    except ValueError:
        return None


@router.post("/import-rows", status_code=201)
@limiter.limit("60/minute")
async def import_rows(
    request: Request,
    body: _ImportRowsBody,
    x_device_key: str | None = Header(default=None),
    device_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(_optional_user),
) -> dict[str, Any]:
    """Bulk-import already-computed daily attendance rows -- for migrating
    historical data from another system, or for an unattended remote
    script pushing report-shaped rows on a schedule, NOT the per-scan
    live-ingestion path. This is a separate, additive endpoint precisely
    because a finished row's total_seconds/session_count/etc. are things
    only this server's own pairing computation (_pair_day_punches) can
    correctly produce; no real device or bridge script has that
    information, which is why /punch and /adms deliberately stay on their
    existing raw-event/wide-report shapes and are untouched by this
    endpoint.

    Accepts either an authenticated user (manual/admin import) OR an
    X-Device-Key / ?device_key= (an unattended script running on a
    schedule, same auth as /punch and /adms) -- lives on the un-gated
    `router` rather than `gated_router` for the same reason /punch does:
    a device has no company login session for require_module("hrms") to
    check against.

    Each row's individual scan events (from `raw_events` if given, else
    synthesized from `work_date` + `clock_in_N`/`clock_out_N`, each of
    which accepts either "HH:MM" local time or a full ISO datetime) are
    replayed one at a time through attendance_store.upsert_attendance_
    event() -- the same shared, concurrency-safe upsert every other
    ingestion path uses -- so an imported row gets pairing/totals
    recomputed by the server rather than trusting whatever total_seconds/
    session_count the caller sent. Fields that are always server-derived
    (total_seconds, ot_seconds, under_seconds, session_count) are accepted
    in the request shape for symmetry with a raw AttendanceDetail row, but
    are ignored -- only employee_id, employee_name, work_date,
    clock_in_1..5, clock_out_1..5, and raw_events are actually read.

    Logged at INFO/WARNING (logger "taxflow") so a DigitalOcean deploy's
    runtime log shows exactly what was accepted and how it was processed,
    per request and per row -- this endpoint deals with data imported from
    outside the app, where "why didn't this row show up" needs a paper
    trail, unlike the high-volume device webhook paths that don't log per
    punch.
    """
    key = x_device_key or device_key
    if current_user:
        company_id = current_user.company_id
        actor = f"user:{current_user.id}"
    elif key:
        company_id, _device = _get_device_company(key, db)
        actor = f"device:{_device.id}"
    else:
        raise HTTPException(status_code=401, detail="Authentication required")

    offset = _company_offset(db, company_id)
    log.info(
        "import-rows: received company=%s actor=%s rows=%d",
        company_id, actor, len(body.rows),
    )
    rows_processed = 0
    events_inserted = events_duplicate = events_rejected = 0
    row_errors: list[dict[str, Any]] = []

    for idx, row in enumerate(body.rows):
        employee_id = str(row.get("employee_id") or "").strip()
        work_date = str(row.get("work_date") or "").strip()
        if not employee_id:
            log.warning("import-rows: row %d rejected: employee_id is required", idx)
            row_errors.append({"row": idx, "error": "employee_id is required"})
            continue
        employee_name = row.get("employee_name") or None

        events: list[tuple[datetime, str]] = []
        raw_events = row.get("raw_events")
        if raw_events:
            # Two supported shapes: a genuine event log (list of {punch_time,
            # direction, ...} dicts, e.g. transplanted from another
            # AttendanceDetail row's export -- UTC ISO timestamps, no offset
            # conversion needed) OR a bare list of ISO timestamp strings with
            # no explicit direction, which alternates in/out/in/out... by
            # position (the only signal available) and is treated as
            # company-local time via _parse_import_clock_field the same way
            # clock_in_N/clock_out_N below are, since a bare string has no
            # way to signal it's already UTC.
            if isinstance(raw_events, str):
                try:
                    raw_events = json.loads(raw_events)
                except (ValueError, TypeError):
                    raw_events = []
            for e in raw_events if isinstance(raw_events, list) else []:
                if isinstance(e, dict):
                    try:
                        events.append((datetime.fromisoformat(e["punch_time"]), e.get("direction") or "in"))
                    except (KeyError, ValueError, TypeError):
                        continue
                elif isinstance(e, str) and e.strip():
                    parsed = _parse_import_clock_field(e.strip(), work_date, offset)
                    if parsed is not None:
                        events.append((parsed, "in" if len(events) % 2 == 0 else "out"))
            if not events:
                log.warning("import-rows: row %d employee=%s raw_events present but yielded no usable events, falling back to clock_in/out fields", idx, employee_id)

        # Falls back to (or is the only path, if raw_events wasn't given)
        # work_date + clock_in_N/clock_out_N -- also covers a caller that
        # sends both raw_events and the flattened fields but in a raw_events
        # shape we couldn't parse, so a recognizable clock_in_1/clock_out_1
        # pair in the same payload isn't wasted.
        if not events and work_date:
            try:
                datetime.strptime(work_date, "%Y-%m-%d")
            except ValueError:
                log.warning("import-rows: row %d rejected: work_date %r must be YYYY-MM-DD", idx, work_date)
                row_errors.append({"row": idx, "error": "work_date must be YYYY-MM-DD"})
                continue
            for n in range(1, 6):
                for field, direction in ((f"clock_in_{n}", "in"), (f"clock_out_{n}", "out")):
                    time_raw = str(row.get(field) or "").strip()
                    if not time_raw:
                        continue
                    parsed = _parse_import_clock_field(time_raw, work_date, offset)
                    if parsed is None:
                        log.warning("import-rows: row %d field %s=%r could not be parsed, skipped", idx, field, time_raw)
                        continue
                    events.append((parsed, direction))

        if not events:
            if not work_date and not raw_events:
                log.warning("import-rows: row %d rejected: work_date or raw_events is required", idx)
                row_errors.append({"row": idx, "error": "work_date or raw_events is required"})
            else:
                log.warning("import-rows: row %d employee=%s rejected: no usable clock_in/out or raw_events found", idx, employee_id)
                row_errors.append({"row": idx, "error": "no usable clock_in/out or raw_events found"})
            continue

        rows_processed += 1
        row_inserted = row_duplicate = row_rejected = 0
        for punch_time, direction in events:
            result = attendance_store.upsert_attendance_event(
                db, company_id=company_id, employee_id=employee_id,
                punch_time=punch_time, direction=direction, employee_name=employee_name,
                source="import",
            )
            if not result.get("ok"):
                events_rejected += 1
                row_rejected += 1
            elif result.get("duplicate"):
                events_duplicate += 1
                row_duplicate += 1
            else:
                events_inserted += 1
                row_inserted += 1
        log.info(
            "import-rows: row %d employee=%s work_date=%s events_found=%d inserted=%d duplicate=%d rejected=%d",
            idx, employee_id, work_date or "(from raw_events)", len(events), row_inserted, row_duplicate, row_rejected,
        )

    log.info(
        "import-rows: complete company=%s rows_received=%d rows_processed=%d events_inserted=%d "
        "events_duplicate=%d events_rejected=%d row_errors=%d",
        company_id, len(body.rows), rows_processed,
        events_inserted, events_duplicate, events_rejected, len(row_errors),
    )
    return {
        "ok": True, "rows_processed": rows_processed,
        "events_inserted": events_inserted, "events_duplicate": events_duplicate, "events_rejected": events_rejected,
        "row_errors": row_errors,
    }


# ── Dashboard data ────────────────────────────────────────────────────────────

def _branch_scope_details(query, principal: Principal, branch_id: str | None):
    """Same two-tier branch scoping as accounting.py's list_journals()/
    payroll.py's list_runs() — previously these endpoints filtered only by
    company_id, so a branch-scoped principal saw every branch's attendance
    data. AttendanceDetail has no branch_id column of its own (employee_id
    here is the employee's business-facing employee_no, not a branch-aware
    FK), so scoping joins through Employee.branch_id instead of a direct
    column filter — hr_access.py's own _scope_attendance_to_branch() already
    does the equivalent for AttendanceSession, which does have branch_id."""
    resolved_branch_id = branch_id if principal.can_cross_branch("attendance") else resolve_active_branch(principal, branch_id)
    if not resolved_branch_id:
        return query
    return query.join(Employee, Employee.employee_no == AttendanceDetail.employee_id).filter(
        (Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None))
    )


def _first_event_source(row: AttendanceDetail) -> str:
    """Resolve the "source" of a day-row's clock_in_1 by matching it back
    against the individual scan events in raw_events -- the flattened
    clock_in_1/etc. columns don't carry source themselves."""
    if row.clock_in_1 is None:
        return "manual"
    try:
        events = json.loads(row.raw_events or "[]")
    except (ValueError, TypeError):
        return "manual"
    for e in events:
        try:
            ts = datetime.fromisoformat(e["punch_time"])
        except (KeyError, ValueError, TypeError):
            continue
        if e.get("direction") == "in" and ts == row.clock_in_1:
            return e.get("source") or "manual"
    return "manual"


@gated_router.get("/today")
def attendance_today(
    branch_id: str | None = Query(default=None),
    date: str | None = Query(default=None, description="YYYY-MM-DD; defaults to today"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return punch-in details for a given day (defaults to today) --
    originally just today's count for the Present Today KPI, widened to
    accept an explicit `date` so the Attendance Calendar can show the same
    per-employee breakdown for whichever day was clicked, not only today."""
    offset = _company_offset(db, principal.company_id)
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
        today = date
    else:
        today = _local_today(offset).isoformat()
    rows = _branch_scope_details(db.query(AttendanceDetail), principal, branch_id).filter(
        AttendanceDetail.company_id == principal.company_id,
        AttendanceDetail.work_date == today,
        AttendanceDetail.clock_in_1.isnot(None),
    ).all()
    # clock_in_1 IS the earliest "in" of the day by construction (pairing
    # always fills session 1 first) -- no need to scan/sort punches here
    # the way the old AttendancePunch-backed version had to.
    first_punch_by_employee: dict[str, datetime] = {r.employee_id: r.clock_in_1 for r in rows}
    first_source_by_employee: dict[str, str] = {r.employee_id: _first_event_source(r) for r in rows}
    unique_employees = sorted(first_punch_by_employee.keys())
    # A punch's employee_id is matched against Employee.employee_no by plain
    # string equality (same rule the Sync Activity Log's "Unmatched" badge
    # uses, recent_punches() above) — Today's Attendance previously showed
    # the raw device employee_id with no name lookup against Staff ->
    # Employees at all.
    employees_by_no = {
        e.employee_no: e
        for e in db.query(Employee).filter(
            Employee.company_id == principal.company_id, Employee.employee_no.in_(unique_employees)
        ).all()
    }
    # A punch matching a real employee record whose status isn't "active"
    # (terminated/inactive) must never count as present — e.g. a
    # since-deactivated employee's old badge still triggering a scan, or a
    # device-side bulk sync bug hitting every enrolled ID regardless of
    # employment status. Every other Employee query in this codebase
    # (payroll.py, leave.py, hr_access.py, this file's own /monthly-report)
    # already filters on Employee.status == "active" — this endpoint was
    # the one place that didn't. Unmatched employee_ids (no Employee record
    # at all) are deliberately left alone: that's the separate, intentional
    # "Unmatched" signal recent_punches()/the Sync Activity Log already use
    # to flag a device ID that doesn't correspond to any employee_no.
    unique_employees = [
        emp_id for emp_id in unique_employees
        if emp_id not in employees_by_no or employees_by_no[emp_id].status == "active"
    ]
    employees = [
        {
            "employee_id": emp_id,
            "employee_name": employees_by_no[emp_id].full_name if emp_id in employees_by_no else None,
            "matched": emp_id in employees_by_no,
            # Seconds included deliberately -- at minute precision, several
            # employees queued at the same scanner around shift start (e.g.
            # 09:08:03, 09:08:19, 09:08:41) all display as the identical
            # "09:08" and read as a bug even though the underlying punches
            # are genuinely distinct. Showing seconds is also the fastest
            # way to tell that case apart from a real duplicate-timestamp
            # ingestion bug, if one ever turns up (literally identical to
            # the second, not just the same minute).
            "check_in_time": (first_punch_by_employee[emp_id] + offset).strftime("%H:%M:%S"),
            # Real punch source ("device"/"biotime" = an actual biometric
            # scanner, "csv"/"manual"/"correction" = not). The frontend
            # previously printed a hardcoded "Biometric" badge on every row
            # regardless of this value, which misrepresented CSV-imported
            # or manually-entered punches as device taps.
            "source": first_source_by_employee[emp_id],
        }
        for emp_id in unique_employees
    ]
    return {
        "date": today,
        "present_count": len(unique_employees),
        "employee_ids": unique_employees,
        "employees": employees,
    }


@gated_router.get("/trend")
def attendance_trend(
    days: int = 30,
    period: str | None = Query(default=None, description="YYYY-MM -- overrides `days`, returns exactly that month"),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return daily punch-in unique-employee counts for the last N days
    (for Attendance Trend chart), or for one explicit calendar month when
    `period` is given -- the Attendance Calendar previously had no way to
    view any month but the current one, since this endpoint could only
    ever answer "the last N days ending today"."""
    offset = _company_offset(db, principal.company_id)
    today = _local_today(offset)
    if period:
        m = re.match(r"^(\d{4})-(\d{2})$", period)
        if not m:
            raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")
        year, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12):
            raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")
        start = date(year, month, 1)
        last_day = date(year, month, monthrange(year, month)[1])
        days = (last_day - start).days + 1
    else:
        days = max(7, min(days, 90))
        start = today - timedelta(days=days - 1)

    # One AttendanceDetail row IS one employee/day, so a plain COUNT(*) here
    # replaces the old COUNT(DISTINCT employee_id) over individual punches.
    query = db.query(
        AttendanceDetail.work_date,
        func.count(AttendanceDetail.id).label("cnt"),
    )
    rows = _branch_scope_details(query, principal, branch_id).filter(
        AttendanceDetail.company_id == principal.company_id,
        AttendanceDetail.work_date >= start.isoformat(),
        AttendanceDetail.work_date <= (start + timedelta(days=days - 1)).isoformat(),
        AttendanceDetail.clock_in_1.isnot(None),
    ).group_by(AttendanceDetail.work_date).all()

    day_map = {r.work_date: r.cnt for r in rows}
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    counts = [day_map.get(d, 0) for d in dates]
    return {"dates": dates, "counts": counts}


def _weekend_day_set(db: Session, company_id: str) -> set[int] | None:
    """Reads the same hr_settings weekend-policy-config record app.js's
    _companyWeekendDaySet() writes (HR Settings > HR Rules > Weekend
    Policy) — {0,6} (Sat/Sun) by default, {5,6} for Fri/Sat, or None for
    "custom" (don't guess; every day counts as a working day). Returned
    values use JS getDay() convention (0=Sun..6=Sat) to stay consistent
    with the frontend, converted from Python's date.weekday() (0=Mon)
    below via (weekday+1)%7."""
    row = db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "weekend-policy-config",
    ).first()
    mode = None
    if row:
        try:
            mode = json.loads(row[0]).get("mode")
        except (ValueError, TypeError, AttributeError):
            mode = None
    if mode == "fri_sat":
        return {5, 6}
    if mode == "custom":
        return None
    return {0, 6}


def _standard_hours_per_day(db: Session, company_id: str) -> float:
    row = db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == "ot-rules-config",
    ).first()
    if row:
        try:
            return float(json.loads(row[0]).get("workHours") or 8)
        except (ValueError, TypeError, AttributeError):
            pass
    return 8.0


@gated_router.get("/monthly-report")
def attendance_monthly_report(
    period: str | None = Query(default=None, description="YYYY-MM, defaults to the current month"),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Per-employee monthly attendance summary for the HRMS Reports page --
    previously "Reports & Analytics" in the HRMS sidebar just opened the
    Payroll page (no reports screen existed, no attendance report anywhere
    in HRMS at all).

    Present/absent/leave are all counted in "working days" (weekend days
    per _weekend_day_set() excluded), so the three numbers are comparable
    and absent_days = working_days - present_days - leave_days lines up.
    Total/OT hours are a first-punch-to-last-punch-per-day approximation
    (not real in/out session pairing) -- adequate for a summary report,
    not a payroll-grade calculation; see zk_bridge.py/daily_attendance_
    report.py for the real direction-aware pairing used elsewhere."""
    offset = _company_offset(db, principal.company_id)
    today = _local_today(offset)
    if period:
        m = re.match(r"^(\d{4})-(\d{2})$", period)
        if not m:
            raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")
        year, month = int(m.group(1)), int(m.group(2))
    else:
        year, month = today.year, today.month
        period = f"{year:04d}-{month:02d}"
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")

    start = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    period_end = min(last_day, today)

    weekend_days = _weekend_day_set(db, principal.company_id)
    standard_hours = _standard_hours_per_day(db, principal.company_id)

    working_days: list[date] = []
    d = start
    while d <= period_end:
        js_dow = (d.weekday() + 1) % 7
        if weekend_days is None or js_dow not in weekend_days:
            working_days.append(d)
        d += timedelta(days=1)
    working_day_set = {wd.isoformat() for wd in working_days}

    resolved_branch_id = branch_id if principal.can_cross_branch("attendance") else resolve_active_branch(principal, branch_id)
    emp_query = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.status == "active")
    if resolved_branch_id:
        emp_query = emp_query.filter((Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None)))
    employees = emp_query.all()
    if not employees:
        return {"period": period, "working_days": len(working_days), "standard_hours_per_day": standard_hours, "employees": []}

    emp_nos = [e.employee_no for e in employees]
    # Reads real pairing-derived total/OT seconds straight off each day's
    # AttendanceDetail row instead of the old MIN(punch_time)/MAX(punch_time)
    # approximation -- since every row already has this computed by
    # attendance_store at write time (the same pairing engine /employee-daily
    # uses), consuming it here is strictly more accurate, not just simpler.
    detail_rows = db.query(
        AttendanceDetail.employee_id,
        AttendanceDetail.work_date,
        AttendanceDetail.total_seconds,
        AttendanceDetail.ot_seconds,
    ).filter(
        AttendanceDetail.company_id == principal.company_id,
        AttendanceDetail.employee_id.in_(emp_nos),
        AttendanceDetail.work_date >= start.isoformat(),
        AttendanceDetail.work_date <= period_end.isoformat(),
    ).all()

    punch_by_emp: dict[str, dict[str, Any]] = {}
    for r in detail_rows:
        punch_by_emp.setdefault(r.employee_id, {})[r.work_date] = r

    emp_ids = [e.id for e in employees]
    leave_rows = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == principal.company_id,
        LeaveRequest.employee_id.in_(emp_ids),
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= period_end.isoformat(),
        LeaveRequest.end_date >= start.isoformat(),
    ).all()
    leave_days_by_emp: dict[str, int] = {}
    for lr in leave_rows:
        lr_start = max(start, date.fromisoformat(lr.start_date))
        lr_end = min(period_end, date.fromisoformat(lr.end_date))
        d = lr_start
        while d <= lr_end:
            if d.isoformat() in working_day_set:
                leave_days_by_emp[lr.employee_id] = leave_days_by_emp.get(lr.employee_id, 0) + 1
            d += timedelta(days=1)

    result = []
    for emp in employees:
        days = punch_by_emp.get(emp.employee_no, {})
        present_days = sum(1 for pd in days if pd in working_day_set)
        # total_hours from stored total_seconds (real pairing, standard-hours-
        # independent) is safe to trust directly; OT is recomputed here
        # against THIS company's configured standard_hours rather than
        # trusting stored ot_seconds, which attendance_store computes at
        # write time against a generic default that may not match this
        # company's actual setting. OT is a sum of PER-DAY overages (a
        # 10-hour day contributes 2 OT hours even if another day that month
        # was short), not (total_hours - working_days * standard_hours).
        total_hours = sum(row.total_seconds for row in days.values()) / 3600
        ot_hours = sum(max(0.0, row.total_seconds / 3600 - standard_hours) for row in days.values())
        leave_days = leave_days_by_emp.get(emp.id, 0)
        absent_days = max(0, len(working_days) - present_days - leave_days)
        result.append({
            "employee_id": emp.id,
            "employee_no": emp.employee_no,
            "employee_name": emp.full_name,
            "department": emp.department,
            "present_days": present_days,
            "absent_days": absent_days,
            "leave_days": leave_days,
            "total_hours": f"{total_hours:.2f}",
            "ot_hours": f"{ot_hours:.2f}",
        })
    result.sort(key=lambda r: r["employee_name"] or "")

    return {
        "period": period,
        "working_days": len(working_days),
        "standard_hours_per_day": standard_hours,
        "employees": result,
    }


_EMPLOYEE_DAILY_MAX_SESSIONS = 3
# The pairing state machine itself (_pair_day_punches) now lives in
# attendance_store.py (imported at the top of this file) so both the live
# per-request path here and the attendance_details backfill/live-write path
# share one implementation instead of two independently-maintained copies.


@gated_router.get("/employee-daily")
def attendance_employee_daily(
    employee_id: str,
    period: str | None = Query(default=None, description="YYYY-MM, defaults to the current month"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Day-by-day attendance for ONE employee across a month -- the
    drill-down behind the Attendance Report's per-employee rows. Mirrors
    the column set of the downloadable daily_attendance_report.py sheet
    (Emp No./AC-No./Day/Name/Date/up to 3 Clock In-Out-Work Time triples/
    Total/OT/Under Time/Absent/SICK/Holiday) so the on-screen popup and
    that offline report agree on what a day's attendance actually looks
    like, instead of the earlier single first-punch/last-punch summary."""
    emp = db.query(Employee).filter(
        Employee.id == employee_id, Employee.company_id == principal.company_id,
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    offset = _company_offset(db, principal.company_id)
    today = _local_today(offset)
    if period:
        m = re.match(r"^(\d{4})-(\d{2})$", period)
        if not m:
            raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")
        year, month = int(m.group(1)), int(m.group(2))
    else:
        year, month = today.year, today.month
        period = f"{year:04d}-{month:02d}"
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="period must be in YYYY-MM format")

    start = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    weekend_days = _weekend_day_set(db, principal.company_id)
    standard_hours = _standard_hours_per_day(db, principal.company_id)

    # Reads each day's already-paired clock_in/out_N + total_seconds
    # straight off AttendanceDetail (computed by attendance_store at write
    # time using the same _pair_day_punches this endpoint used to call
    # per-request) instead of re-fetching raw punches and re-pairing them
    # here on every request.
    details_by_date: dict[str, AttendanceDetail] = {
        r.work_date: r
        for r in db.query(AttendanceDetail).filter(
            AttendanceDetail.company_id == principal.company_id,
            AttendanceDetail.employee_id == emp.employee_no,
            AttendanceDetail.work_date >= start.isoformat(),
            AttendanceDetail.work_date <= last_day.isoformat(),
        ).all()
    }

    leave_rows = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == principal.company_id,
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= last_day.isoformat(),
        LeaveRequest.end_date >= start.isoformat(),
    ).all()
    leave_dates: set[str] = set()
    sick_dates: set[str] = set()
    for lr in leave_rows:
        lr_start = max(start, date.fromisoformat(lr.start_date))
        lr_end = min(last_day, date.fromisoformat(lr.end_date))
        d = lr_start
        while d <= lr_end:
            leave_dates.add(d.isoformat())
            if "sick" in (lr.leave_type or "").lower():
                sick_dates.add(d.isoformat())
            d += timedelta(days=1)

    # Best-effort: the Holiday Calendar is still a Tier 2 (AppDataRecord)
    # feature (see docs/hrms-architecture.md), so this reads whatever
    # ISO-format date field a saved holiday row actually has -- a holiday
    # saved in some other date format simply won't match here rather than
    # this guessing at parsing it, same "don't fabricate" principle as
    # daily_attendance_report.py leaving this column blank entirely.
    holiday_dates: set[str] = set()
    for rec in db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == principal.company_id,
        AppDataRecord.collection == "hrHolidays",
    ).all():
        try:
            payload = json.loads(rec[0])
        except (ValueError, TypeError):
            continue
        raw_date = str(payload.get("date") or payload.get("holiday_date") or "")
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            holiday_dates.add(raw_date)

    days = []
    d = start
    while d <= last_day:
        iso = d.isoformat()
        js_dow = (d.weekday() + 1) % 7
        is_weekend = weekend_days is not None and js_dow in weekend_days
        is_leave = iso in leave_dates
        is_sick = iso in sick_dates
        is_holiday = iso in holiday_dates

        detail_row = details_by_date.get(iso)
        total_hours = (detail_row.total_seconds / 3600) if detail_row else 0.0
        sessions = []
        if detail_row:
            for n in range(1, _EMPLOYEE_DAILY_MAX_SESSIONS + 1):
                cin = getattr(detail_row, f"clock_in_{n}")
                cout = getattr(detail_row, f"clock_out_{n}")
                work_seconds = getattr(detail_row, f"work_seconds_{n}")
                sessions.append({
                    "clock_in": (cin + offset).strftime("%H:%M") if cin else None,
                    "clock_out": (cout + offset).strftime("%H:%M") if cout else None,
                    "work_time": f"{work_seconds / 3600:.2f}" if work_seconds is not None else None,
                })
        else:
            sessions = [{"clock_in": None, "clock_out": None, "work_time": None} for _ in range(_EMPLOYEE_DAILY_MAX_SESSIONS)]

        # Checking raw_events (not session_count) matches the old semantics
        # exactly: "does any punch event exist that day" -- a lone orphan
        # "out" with no preceding "in" produces zero pairs (session_count
        # would be 0) but is still a real recorded event, not an absence.
        has_punches = detail_row is not None and bool(json.loads(detail_row.raw_events or "[]"))
        if d > today:
            status = "upcoming"
        elif is_weekend:
            status = "weekend"
        elif is_holiday:
            status = "holiday"
        elif is_sick:
            status = "sick"
        elif is_leave:
            status = "leave"
        elif has_punches:
            status = "present"
        else:
            status = "absent"
        is_absent = status == "absent"

        days.append({
            "emp_no": emp.employee_no,
            "ac_no": emp.employee_no,
            "date": iso,
            "day_name": d.strftime("%a"),
            "is_weekend": is_weekend,
            # Lets the UI tell "hasn't checked out yet today" (still possibly
            # coming) apart from "day is over, no checkout was ever recorded"
            # (e.g. an entry-only device) -- both look identical otherwise.
            "is_today": d == today,
            "status": status,
            "sessions": sessions,
            "total_hours": f"{total_hours:.2f}",
            "ot_hours": f"{max(0.0, total_hours - standard_hours):.2f}" if has_punches else "0.00",
            "under_hours": f"{max(0.0, standard_hours - total_hours):.2f}" if has_punches and not (is_weekend or is_leave or is_holiday) else "0.00",
            "absent": "Yes" if is_absent else "",
            "sick": "Yes" if is_sick else "",
            "holiday": "Yes" if is_holiday else "",
        })
        d += timedelta(days=1)

    return {
        "employee_id": emp.id,
        "employee_no": emp.employee_no,
        "employee_name": emp.full_name,
        "department": emp.department,
        "period": period,
        "standard_hours_per_day": standard_hours,
        "max_sessions": _EMPLOYEE_DAILY_MAX_SESSIONS,
        "days": days,
    }


@gated_router.get("/punches")
def recent_punches(
    limit: int = 50,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return the most recent punch records for the sync activity log."""
    limit = max(1, min(limit, 200))
    # Fetch a bounded window of the most-recently-touched day-rows and
    # flatten each one's raw_events into individual per-punch dicts, then
    # sort/slice in Python -- AttendanceDetail stores a day's worth of scans
    # per row, so "most recent N individual punches" can no longer be a
    # single indexed SQL ORDER BY + LIMIT the way it was against the old
    # one-row-per-punch AttendancePunch table. Acceptable trade-off at HRMS
    # scale (limit capped at 200).
    fetch_rows = _branch_scope_details(db.query(AttendanceDetail), principal, branch_id).filter(
        AttendanceDetail.company_id == principal.company_id,
    ).order_by(AttendanceDetail.updated_at.desc()).limit(min(limit * 5, 500)).all()
    flattened = attendance_store.flatten_events(fetch_rows, timedelta(0))
    flattened.sort(key=lambda e: e["punch_time"], reverse=True)
    flattened = flattened[:limit]
    # A punch's employee_id is matched against Employee.employee_no by plain
    # string equality (see _branch_scope_details' own docstring) — nothing
    # validates this at ingestion time, so a device enrolled with the wrong
    # ID silently never attaches to anyone. Surface that here so an admin
    # troubleshooting a "punch arrived but employee shows absent" report has
    # something to look at, rather than every punch looking equally valid.
    valid_employee_nos = {
        e.employee_no for e in db.query(Employee.employee_no).filter(Employee.company_id == principal.company_id).all()
    }
    return {"punches": [
        {
            "id": e["id"],
            "employee_id": e["employee_id"],
            "employee_name": e["employee_name"],
            "punch_time": e["punch_time"].isoformat(),
            "punch_date": e["punch_date"],
            "direction": e["direction"],
            "device_name": e["device_name"],
            "source": e["source"],
            "matched": e["employee_id"] in valid_employee_nos,
        }
        for e in flattened
    ]}


@gated_router.delete("/punches/{punch_id}", status_code=204, response_model=None)
def delete_punch(
    punch_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:edit")),
) -> None:
    """Remove a single erroneous/test punch from the Sync Activity Log —
    previously there was no way to clean up a bad row at all (a mis-scanned
    device punch, an unmatched employee_id typo, a diagnostic test punch)
    short of a superadmin wiping the entire company's attendance history."""
    if not attendance_store.delete_event(db, principal.company_id, punch_id):
        raise HTTPException(status_code=404, detail="Punch record not found")


@gated_router.get("/summary")
def attendance_summary(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Recent 7-day attendance overview."""
    today = _local_today(_company_offset(db, principal.company_id))
    week_start = (today - timedelta(days=6)).isoformat()
    query = db.query(
        AttendanceDetail.work_date,
        func.count(AttendanceDetail.id).label("cnt"),
    )
    rows = _branch_scope_details(query, principal, branch_id).filter(
        AttendanceDetail.company_id == principal.company_id,
        AttendanceDetail.work_date >= week_start,
        AttendanceDetail.clock_in_1.isnot(None),
    ).group_by(AttendanceDetail.work_date).all()
    return {"week": {r.work_date: r.cnt for r in rows}}


# ── Bridge script download ────────────────────────────────────────────────────

@gated_router.get("/bridge-script")
def download_bridge_script(
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:  # auth keeps it scoped to logged-in users
    """Serve zk_bridge.py as a downloadable file."""
    candidates = [
        pathlib.Path(__file__).parent.parent.parent / "zk_bridge.py",  # backend/zk_bridge.py
        pathlib.Path(__file__).parent.parent.parent.parent / "backend" / "zk_bridge.py",
    ]
    for path in candidates:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            return PlainTextResponse(
                content=content,
                headers={"Content-Disposition": "attachment; filename=zk_bridge.py"},
            )
    raise HTTPException(404, "Bridge script not found on server")


@gated_router.get("/report-script")
def download_report_script(
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """Serve daily_attendance_report.py as a downloadable file — unlike
    zk_bridge.py (a continuous live-sync loop), this is a once-a-day batch
    script that also produces a local CSV/Excel attendance report each run."""
    candidates = [
        pathlib.Path(__file__).parent.parent.parent / "daily_attendance_report.py",
        pathlib.Path(__file__).parent.parent.parent.parent / "backend" / "daily_attendance_report.py",
    ]
    for path in candidates:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            return PlainTextResponse(
                content=content,
                headers={"Content-Disposition": "attachment; filename=daily_attendance_report.py"},
            )
    raise HTTPException(404, "Report script not found on server")
