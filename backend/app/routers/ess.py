"""Employee Self-Service (ESS) portal — backend API.

Employees log in with their username + password (set by HR admin in the
employee form).  After login they receive a short-lived JWT whose `sub`
is  "ess:<company_id>:<employee_key>"  and can use it to read their own
attendance, payslips, leave history and submit new leave / OT requests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any  # used in _decode_payload and _get_emp_record

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AppDataRecord, AttendancePunch

router = APIRouter(prefix="/ess", tags=["ess"])
settings = get_settings()
_ALGORITHM = "HS256"
_ESS_PREFIX = "ess:"


# ── helpers ──────────────────────────────────────────────────────────────────

def _decode_payload(rec: AppDataRecord) -> dict[str, Any]:
    try:
        return json.loads(rec.payload)
    except Exception:
        return {}


def _ess_token(company_id: str, emp_key: str, emp_name: str) -> str:
    expires = datetime.now(UTC) + timedelta(hours=12)
    return jwt.encode(
        {"sub": f"{_ESS_PREFIX}{company_id}:{emp_key}", "name": emp_name, "exp": expires},
        settings.secret_key,
        algorithm=_ALGORITHM,
    )


def _resolve_ess_token(token: str) -> tuple[str, str]:
    """Return (company_id, emp_key) from a valid ESS JWT, or raise 401."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
        sub: str = payload.get("sub", "")
        if not sub.startswith(_ESS_PREFIX):
            raise ValueError
        _, rest = sub.split(":", 1)
        company_id, emp_key = rest.split(":", 1)
        return company_id, emp_key
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ESS token")


def _get_emp_record(db: Session, company_id: str, emp_key: str) -> dict[str, Any]:
    row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "employees",
            AppDataRecord.record_key == emp_key,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Employee record not found")
    return _decode_payload(row)


# ── schemas ───────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


class LeaveApplyBody(BaseModel):
    type: str = "Annual"
    from_date: str
    to_date: str
    reason: str = ""


class OTApplyBody(BaseModel):
    date: str
    ot_hours: str
    reason: str = ""
    department: str = ""


# ── auth helper (used by all protected routes) ────────────────────────────────

def _auth(request_headers: dict, db: Session) -> tuple[str, str, dict]:
    auth_header = request_headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    company_id, emp_key = _resolve_ess_token(token)
    emp = _get_emp_record(db, company_id, emp_key)
    return company_id, emp_key, emp


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
def ess_login(body: LoginBody, db: Session = Depends(get_db)) -> dict:
    """Find the employee by username+password. Scans per-company; correct match wins."""
    username = (body.username or "").strip().lower()
    password = (body.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    # Load all employee records that carry a username field — scoped per company
    # so we check the password before touching any cross-company data.
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.collection == "employees")
        .all()
    )

    # Collect ALL rows whose username matches, then check password on all of them.
    # This prevents an early-exit from the wrong company's record blocking a valid login.
    username_matches: list[tuple[AppDataRecord, dict]] = []
    for row in rows:
        emp = _decode_payload(row)
        stored_username = (emp.get("username") or "").strip().lower()
        if stored_username == username:
            username_matches.append((row, emp))

    if not username_matches:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    for row, emp in username_matches:
        stored_password = emp.get("password") or ""
        if stored_password != password:
            continue
        emp_key = row.record_key or (emp.get("id") or emp.get("name") or "")
        token = _ess_token(row.company_id, emp_key, emp.get("name") or "")
        return {
            "access_token": token,
            "token_type": "bearer",
            "employee": {
                "id": emp_key,
                "name": emp.get("name") or "",
                "designation": emp.get("designation") or "",
                "department": emp.get("department") or "",
                "employee_no": emp.get("id") or "",
            },
        }

    raise HTTPException(status_code=401, detail="Invalid username or password")


@router.get("/profile")
def ess_profile(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    # Strip sensitive fields
    safe = {k: v for k, v in emp.items() if k not in ("password", "hashed_password")}
    return {"ok": True, "employee": safe}


@router.get("/attendance")
def ess_attendance(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_id = emp.get("id") or emp_key
    emp_name = (emp.get("name") or "").lower()

    punches = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.company_id == company_id,
            AttendancePunch.employee_id.ilike(f"%{emp_id}%"),
        )
        .order_by(AttendancePunch.punch_time.desc())
        .limit(90)
        .all()
    )
    return {
        "ok": True,
        "records": [
            {
                "date": p.punch_time[:10] if p.punch_time else "",
                "time": p.punch_time[11:16] if p.punch_time and len(p.punch_time) > 10 else "",
                "direction": p.direction,
            }
            for p in punches
        ],
    }


@router.get("/payslips")
def ess_payslips(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_id = emp.get("id") or emp_key
    emp_name = (emp.get("name") or "").strip().lower()

    runs = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "payrollRuns",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(24)
        .all()
    )

    slips = []
    for row in runs:
        run = _decode_payload(row)
        items = run.get("items") or []
        if not isinstance(items, list):
            items = []
        for item in items:
            if (item.get("employee_id") or "").lower() == emp_id.lower() or \
               (item.get("employee") or "").strip().lower() == emp_name:
                slips.append({
                    "period": run.get("period") or "",
                    "basic": item.get("basic") or 0,
                    "allowances": item.get("allowances") or 0,
                    "overtime": item.get("overtime") or 0,
                    "deductions": item.get("deductions") or 0,
                    "net_pay": item.get("net_pay") or item.get("net") or 0,
                    "status": run.get("status") or "draft",
                })
    return {"ok": True, "payslips": slips}


@router.get("/leave")
def ess_leave(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()

    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "leaveRequests",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(100)
        .all()
    )

    records = []
    for row in rows:
        rec = _decode_payload(row)
        if (rec.get("employee") or "").strip().lower() == emp_name:
            records.append(rec)

    return {"ok": True, "records": records}


@router.get("/overtime")
def ess_overtime(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()

    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "overtimeRequests",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(100)
        .all()
    )

    records = []
    for row in rows:
        rec = _decode_payload(row)
        if (rec.get("employee") or "").strip().lower() == emp_name:
            records.append(rec)

    return {"ok": True, "records": records}


@router.get("/documents")
def ess_documents(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    docs = emp.get("documents") or {}
    return {"ok": True, "documents": docs}


@router.post("/leave/apply")
def ess_leave_apply(body: LeaveApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""

    from_date = body.from_date
    to_date = body.to_date
    try:
        d1 = datetime.fromisoformat(from_date)
        d2 = datetime.fromisoformat(to_date)
        days = max(1, (d2 - d1).days + 1)
    except Exception:
        days = 1

    import time as _time
    record_id = f"LVE-{int(_time.time() * 1000)}"
    record = {
        "id": record_id,
        "employee": emp_name,
        "type": body.type,
        "from": from_date,
        "to": to_date,
        "days": days,
        "reason": body.reason,
        "status": "Pending",
        "submitted": datetime.now(UTC).isoformat(),
        "source": "ess",
    }

    db.add(AppDataRecord(
        company_id=company_id,
        collection="leaveRequests",
        record_key=record_id,
        payload=json.dumps(record),
    ))
    db.commit()
    return {"ok": True, "record": record}


@router.post("/overtime/apply")
def ess_overtime_apply(body: OTApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""

    import time as _time
    record_id = f"OT-{int(_time.time() * 1000)}"
    record = {
        "id": record_id,
        "employee": emp_name,
        "department": body.department or (emp.get("department") or ""),
        "date": body.date,
        "ot_hours": body.ot_hours,
        "reason": body.reason,
        "status": "Pending",
        "submitted": datetime.now(UTC).isoformat(),
        "source": "ess",
    }

    db.add(AppDataRecord(
        company_id=company_id,
        collection="overtimeRequests",
        record_key=record_id,
        payload=json.dumps(record),
    ))
    db.commit()
    return {"ok": True, "record": record}
