"""Attendance & Biometric Device management.

Supported punch sources:
  - ZKTeco TCP/IP (via zk_bridge.py running on-premises)
  - HTTP webhook (Suprema, Hikvision, Anviz)
  - CSV import
  - Manual entry
"""

import csv
import io
import pathlib
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import AttendancePunch, BiometricDevice, User
from app.security import verify_password, hash_password

router = APIRouter(prefix="/attendance", tags=["attendance"])

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
    device_type: str = "ZKTeco F Series"  # ZKTeco F/K/iClock/SpeedFace/ProFace/G/UA/IN/MB Series | ZKTeco ADMS | Suprema | Hikvision | Anviz | Manual
    ip_address: str | None = None
    port: int = 4370
    location: str | None = None


class DeviceOut(BaseModel):
    id: str
    name: str
    device_type: str
    ip_address: str | None
    port: int
    location: str | None
    status: str
    last_sync: str | None


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_time(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(UTC)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(UTC)


def _get_device_company(x_device_key: str, db: Session) -> tuple[str, BiometricDevice]:
    """Resolve company_id from device API key (for ZK bridge / webhook auth)."""
    devices = db.query(BiometricDevice).filter(
        BiometricDevice.status == "active",
        BiometricDevice.api_key_hash.isnot(None),
    ).all()
    for device in devices:
        try:
            if verify_password(x_device_key, device.api_key_hash):
                return device.company_id, device
        except Exception:
            continue
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device key")


# ── Device management ─────────────────────────────────────────────────────────

@router.get("/devices")
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
    ) for d in devices]


@router.post("/devices", status_code=201)
def add_device(
    body: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
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


@router.delete("/devices/{device_id}", status_code=204, response_model=None)
def delete_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    device = db.query(BiometricDevice).filter(
        BiometricDevice.id == device_id,
        BiometricDevice.company_id == current_user.company_id,
    ).first()
    if not device:
        raise HTTPException(404, "Device not found")
    device.status = "deleted"
    db.commit()


@router.post("/devices/{device_id}/test")
def test_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    device = db.query(BiometricDevice).filter(
        BiometricDevice.id == device_id,
        BiometricDevice.company_id == current_user.company_id,
    ).first()
    if not device:
        raise HTTPException(404, "Device not found")
    if not device.ip_address:
        return {"ok": False, "message": "No IP address configured"}
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((device.ip_address, device.port))
        s.close()
        return {"ok": True, "message": f"TCP port {device.port} is open on {device.ip_address}"}
    except socket.timeout:
        return {"ok": False, "message": f"Timeout connecting to {device.ip_address}:{device.port} — device may be offline"}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"Connection refused on {device.ip_address}:{device.port} — check IP/port"}
    except OSError as e:
        return {"ok": False, "message": f"Cannot reach {device.ip_address}:{device.port} — {e.strerror}"}


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

@router.post("/punch", status_code=201)
@limiter.limit("600/minute")   # ZK bridge sends bursts during initial sync
def record_punch(
    request: Request,
    body: PunchIn,
    x_device_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(_optional_user),
) -> dict[str, Any]:
    """Accept a punch from the ZK bridge (X-Device-Key) or an authenticated user (manual entry)."""
    if current_user:
        company_id = current_user.company_id
        device_name = "Manual"
        device_id = None
    elif x_device_key:
        company_id, device = _get_device_company(x_device_key, db)
        device_name = device.name
        device_id = device.id
        device.last_sync = datetime.now(UTC)
    else:
        raise HTTPException(status_code=401, detail="Authentication required")

    punch_time = _parse_time(body.punch_time)
    punch_date = punch_time.strftime("%Y-%m-%d")

    punch = AttendancePunch(
        company_id=company_id,
        employee_id=body.employee_id.strip(),
        employee_name=body.employee_name,
        punch_time=punch_time,
        punch_date=punch_date,
        direction=body.direction,
        device_id=device_id,
        device_name=device_name,
        source="device" if x_device_key else "manual",
    )
    db.add(punch)
    db.commit()
    return {"ok": True, "id": punch.id}


# ── CSV Import ────────────────────────────────────────────────────────────────

@router.post("/import-csv", status_code=201)
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

    inserted = 0
    skipped = 0
    for row in reader:
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        emp_id = norm.get("employee_id", "")
        ts_raw = norm.get("punch_time", "")
        if not emp_id or not ts_raw:
            skipped += 1
            continue
        punch_time = _parse_time(ts_raw)
        punch = AttendancePunch(
            company_id=current_user.company_id,
            employee_id=emp_id,
            employee_name=norm.get("employee_name") or None,
            punch_time=punch_time,
            punch_date=punch_time.strftime("%Y-%m-%d"),
            direction=norm.get("direction", "in"),
            source="csv",
        )
        db.add(punch)
        inserted += 1

    db.commit()
    return {"imported": inserted, "skipped": skipped}


# ── Dashboard data ────────────────────────────────────────────────────────────

@router.get("/today")
def attendance_today(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return today's punch-in count for the Present Today KPI."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    rows = db.query(AttendancePunch).filter(
        AttendancePunch.company_id == current_user.company_id,
        AttendancePunch.punch_date == today,
        AttendancePunch.direction == "in",
    ).all()
    unique_employees = {r.employee_id for r in rows}
    return {
        "date": today,
        "present_count": len(unique_employees),
        "employee_ids": sorted(unique_employees),
    }


@router.get("/trend")
def attendance_trend(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return daily punch-in unique-employee counts for the last N days (for Attendance Trend chart)."""
    days = max(7, min(days, 90))
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)

    rows = db.query(
        AttendancePunch.punch_date,
        func.count(func.distinct(AttendancePunch.employee_id)).label("cnt"),
    ).filter(
        AttendancePunch.company_id == current_user.company_id,
        AttendancePunch.punch_date >= start.isoformat(),
        AttendancePunch.direction == "in",
    ).group_by(AttendancePunch.punch_date).all()

    day_map = {r.punch_date: r.cnt for r in rows}
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    counts = [day_map.get(d, 0) for d in dates]
    return {"dates": dates, "counts": counts}


@router.get("/punches")
def recent_punches(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the most recent punch records for the sync activity log."""
    limit = max(1, min(limit, 200))
    rows = db.query(AttendancePunch).filter(
        AttendancePunch.company_id == current_user.company_id,
    ).order_by(AttendancePunch.punch_time.desc()).limit(limit).all()
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
        }
        for r in rows
    ]}


@router.get("/summary")
def attendance_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Recent 7-day attendance overview."""
    today = datetime.now(UTC).date()
    week_start = (today - timedelta(days=6)).isoformat()
    rows = db.query(
        AttendancePunch.punch_date,
        func.count(func.distinct(AttendancePunch.employee_id)).label("cnt"),
    ).filter(
        AttendancePunch.company_id == current_user.company_id,
        AttendancePunch.punch_date >= week_start,
        AttendancePunch.direction == "in",
    ).group_by(AttendancePunch.punch_date).all()
    return {"week": {r.punch_date: r.cnt for r in rows}}


# ── Bridge script download ────────────────────────────────────────────────────

@router.get("/bridge-script")
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
