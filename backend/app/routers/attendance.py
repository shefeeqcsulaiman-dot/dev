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
import pathlib
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.cache as cache
import app.timezone_utils as timezone_utils
from app import biotime_client, biotime_sync, crypto
from app.database import get_db
from app.auth_principal import resolve_active_branch
from app.dependencies import Principal, get_current_user, require_module, require_principal_permission
from app.limiter import limiter
from app.models import AttendancePunch, BiometricDevice, Company, Employee, User
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
    device_type: str = "ZKTeco F Series"  # ZKTeco F/K/iClock/SpeedFace/ProFace/G/UA/IN/MB Series | ZKTeco ADMS | Suprema | Hikvision | Anviz | ZKTeco BioTime Server | Manual
    ip_address: str | None = None
    port: int = 4370
    location: str | None = None
    # BioTime server connection only (device_type == "ZKTeco BioTime Server")
    biotime_base_url: str | None = None
    biotime_username: str | None = None
    biotime_password: str | None = None


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
    if device.device_type in _PUSH_TYPES:
        week_ago = now - timedelta(days=7)
        recent = db.query(func.count(AttendancePunch.id)).filter(
            AttendancePunch.device_id == device.id,
            AttendancePunch.punch_time >= week_ago,
        ).scalar() or 0

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
        total = db.query(func.count(AttendancePunch.id)).filter(
            AttendancePunch.company_id == current_user.company_id,
            AttendancePunch.source == "csv",
        ).scalar() or 0
        return {"ok": True, "message": f"CSV import device — {total} records imported total"}

    # ── TCP/IP devices: socket reachability check ──────────────────────────────
    if not device.ip_address:
        return {"ok": False, "message": "No IP address configured — add the device IP to test connectivity"}

    week_ago = now - timedelta(days=7)
    recent = db.query(func.count(AttendancePunch.id)).filter(
        AttendancePunch.device_id == device.id,
        AttendancePunch.punch_time >= week_ago,
    ).scalar() or 0

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
    try:
        body = PunchIn(**raw_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    offset = _company_offset(db, company_id)
    punch_time = _parse_time(body.punch_time, offset)

    # Reject punches more than 5 minutes in the future (prevents date manipulation)
    now = datetime.now(UTC)
    if punch_time > now + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Punch time is in the future")

    # Reject punches older than 90 days (prevents replay / mass backdating attacks)
    if device_key and punch_time < now - timedelta(days=90):
        raise HTTPException(status_code=422, detail="Punch time is too old (>90 days)")

    punch_date = _local_date(punch_time, offset)
    employee_id = body.employee_id.strip()

    # Idempotency guard: a bridge restart replays its whole in-memory backlog
    # (last-synced marker is best-effort), so the same device punch can arrive
    # more than once. Treat an identical (device, employee, timestamp) as the
    # same punch instead of inserting a duplicate row.
    if device_id:
        existing = db.query(AttendancePunch).filter(
            AttendancePunch.company_id == company_id,
            AttendancePunch.employee_id == employee_id,
            AttendancePunch.punch_time == punch_time,
            AttendancePunch.device_id == device_id,
        ).first()
        if existing:
            return {"ok": True, "id": existing.id, "duplicate": True}

    punch = AttendancePunch(
        company_id=company_id,
        employee_id=employee_id,
        employee_name=body.employee_name,
        punch_time=punch_time,
        punch_date=punch_date,
        direction=body.direction,
        device_id=device_id,
        device_name=device_name,
        source="device" if device_key else "manual",
    )
    db.add(punch)
    try:
        db.commit()
    except IntegrityError:
        # The SELECT-based check above raced with another concurrent
        # request for the identical punch and lost — uq_attendance_punch_dedup
        # (models.py) caught what the app-level check couldn't. Same
        # idempotent response as the check above, not an error.
        db.rollback()
        existing = db.query(AttendancePunch).filter(
            AttendancePunch.company_id == company_id,
            AttendancePunch.employee_id == employee_id,
            AttendancePunch.punch_time == punch_time,
            AttendancePunch.device_id == device_id,
        ).first()
        if existing:
            return {"ok": True, "id": existing.id, "duplicate": True}
        raise
    return {"ok": True, "id": punch.id}


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
    for row in reader:
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        emp_id = norm.get("employee_id", "")
        ts_raw = norm.get("punch_time", "")
        if not emp_id or not ts_raw:
            skipped += 1
            continue
        punch_time = _parse_time(ts_raw, offset)
        punch = AttendancePunch(
            company_id=current_user.company_id,
            employee_id=emp_id,
            employee_name=norm.get("employee_name") or None,
            punch_time=punch_time,
            punch_date=_local_date(punch_time, offset),
            direction=norm.get("direction", "in"),
            source="csv",
        )
        db.add(punch)
        inserted += 1

    db.commit()
    return {"imported": inserted, "skipped": skipped}


# ── Dashboard data ────────────────────────────────────────────────────────────

def _branch_scope_punches(query, principal: Principal, branch_id: str | None):
    """Same two-tier branch scoping as accounting.py's list_journals()/
    payroll.py's list_runs() — previously these 4 endpoints filtered only by
    company_id, so a branch-scoped principal saw every branch's attendance
    data. AttendancePunch has no branch_id column of its own (employee_id
    here is the employee's business-facing employee_no, not a branch-aware
    FK), so scoping joins through Employee.branch_id instead of a direct
    column filter — hr_access.py's own _scope_attendance_to_branch() already
    does the equivalent for AttendanceSession, which does have branch_id."""
    resolved_branch_id = branch_id if principal.can_cross_branch("attendance") else resolve_active_branch(principal, branch_id)
    if not resolved_branch_id:
        return query
    return query.join(Employee, Employee.employee_no == AttendancePunch.employee_id).filter(
        (Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None))
    )


@gated_router.get("/today")
def attendance_today(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return today's punch-in count for the Present Today KPI."""
    today = _local_today(_company_offset(db, principal.company_id)).isoformat()
    rows = _branch_scope_punches(db.query(AttendancePunch), principal, branch_id).filter(
        AttendancePunch.company_id == principal.company_id,
        AttendancePunch.punch_date == today,
        AttendancePunch.direction == "in",
    ).all()
    unique_employees = {r.employee_id for r in rows}
    return {
        "date": today,
        "present_count": len(unique_employees),
        "employee_ids": sorted(unique_employees),
    }


@gated_router.get("/trend")
def attendance_trend(
    days: int = 30,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return daily punch-in unique-employee counts for the last N days (for Attendance Trend chart)."""
    days = max(7, min(days, 90))
    today = _local_today(_company_offset(db, principal.company_id))
    start = today - timedelta(days=days - 1)

    query = db.query(
        AttendancePunch.punch_date,
        func.count(func.distinct(AttendancePunch.employee_id)).label("cnt"),
    )
    rows = _branch_scope_punches(query, principal, branch_id).filter(
        AttendancePunch.company_id == principal.company_id,
        AttendancePunch.punch_date >= start.isoformat(),
        AttendancePunch.direction == "in",
    ).group_by(AttendancePunch.punch_date).all()

    day_map = {r.punch_date: r.cnt for r in rows}
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    counts = [day_map.get(d, 0) for d in dates]
    return {"dates": dates, "counts": counts}


@gated_router.get("/punches")
def recent_punches(
    limit: int = 50,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("attendance:view")),
) -> dict[str, Any]:
    """Return the most recent punch records for the sync activity log."""
    limit = max(1, min(limit, 200))
    rows = _branch_scope_punches(db.query(AttendancePunch), principal, branch_id).filter(
        AttendancePunch.company_id == principal.company_id,
    ).order_by(AttendancePunch.punch_time.desc()).limit(limit).all()
    # A punch's employee_id is matched against Employee.employee_no by plain
    # string equality (see _branch_scope_punches' own docstring) — nothing
    # validates this at ingestion time, so a device enrolled with the wrong
    # ID silently never attaches to anyone. Surface that here so an admin
    # troubleshooting a "punch arrived but employee shows absent" report has
    # something to look at, rather than every punch looking equally valid.
    valid_employee_nos = {
        e.employee_no for e in db.query(Employee.employee_no).filter(Employee.company_id == principal.company_id).all()
    }
    return {"punches": [
        {
            "id": r.id,
            "employee_id": r.employee_id,
            "employee_name": r.employee_name,
            "punch_time": r.punch_time.isoformat(),
            "punch_date": r.punch_date,
            "direction": r.direction,
            "device_name": r.device_name,
            "source": r.source,
            "matched": r.employee_id in valid_employee_nos,
        }
        for r in rows
    ]}


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
        AttendancePunch.punch_date,
        func.count(func.distinct(AttendancePunch.employee_id)).label("cnt"),
    )
    rows = _branch_scope_punches(query, principal, branch_id).filter(
        AttendancePunch.company_id == principal.company_id,
        AttendancePunch.punch_date >= week_start,
        AttendancePunch.direction == "in",
    ).group_by(AttendancePunch.punch_date).all()
    return {"week": {r.punch_date: r.cnt for r in rows}}


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
