"""Employee Self-Service (ESS) portal — backend API.

Employees log in with their username + password (set by HR admin in the
employee form).  After login they receive a short-lived JWT whose `sub`
is  "ess:<company_id>:<employee_key>"  and can use it to read their own
attendance, payslips, leave history and submit requests.
"""

from __future__ import annotations

import json
import time as _time
from datetime import UTC, date as _date, datetime, timedelta
from typing import Any

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


def _auth(request_headers: dict, db: Session) -> tuple[str, str, dict]:
    auth_header = request_headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    company_id, emp_key = _resolve_ess_token(token)
    emp = _get_emp_record(db, company_id, emp_key)
    return company_id, emp_key, emp


# ── module resolution ─────────────────────────────────────────────────────────

# Exact checkbox values from the HRMS role modal → ESS tab IDs
_HRMS_TO_ESS: dict[str, str] = {
    "Attendance":       "attendance",
    "Leave Management": "leave",
    "Overtime":         "overtime",
    "Payroll":          "payslips",
    "Corrections":      "corrections",
    "Loans & Advances": "loans",
    "Expiry Alerts":    "expiry",
    "Biometric":        "biometric",
    "Recruitment":      "recruitment",
    "Performance":      "performance",
    "Rota":             "rota",
    "Assets":           "assets",
}
_ALL_ESS: list[str] = [
    "attendance", "payslips", "leave", "overtime",
    "corrections", "loans", "expiry", "biometric",
    "recruitment", "performance", "documents", "rota", "assets",
]


def _resolve_ess_modules(company_id: str, emp: dict[str, Any], db: Session) -> list[str]:
    emp_role_id   = (emp.get("role_id")   or "").strip()
    emp_role_name = (emp.get("role_name") or "").strip()
    if not emp_role_id and not emp_role_name:
        return _ALL_ESS[:]

    settings_row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "hr_settings",
            AppDataRecord.record_key == "dept-branch-role-config",
        )
        .first()
    )
    if not settings_row:
        return _ALL_ESS[:]

    hr_cfg   = _decode_payload(settings_row)
    roles    = hr_cfg.get("roles") or []
    role_obj = next(
        (r for r in roles if
         (emp_role_id   and r.get("id")       == emp_role_id) or
         (emp_role_name and r.get("roleName") == emp_role_name)),
        None,
    )
    if not role_obj:
        return _ALL_ESS[:]

    hrms_mods = role_obj.get("modules") or []
    if "ESS Portal" in hrms_mods or "Full Access" in hrms_mods:
        return _ALL_ESS[:]

    mapped = [_HRMS_TO_ESS[m] for m in hrms_mods if m in _HRMS_TO_ESS]
    if not mapped:
        return _ALL_ESS[:]

    if "documents" not in mapped:
        mapped.append("documents")
    return mapped


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

class CorrectionApplyBody(BaseModel):
    date: str
    in_time: str = ""
    out_time: str = ""
    reason: str = ""

class LoanApplyBody(BaseModel):
    type: str = "Salary Advance"
    amount: str
    purpose: str = ""
    period: str = "1 Month"


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
def ess_login(body: LoginBody, db: Session = Depends(get_db)) -> dict:
    username = (body.username or "").strip().lower()
    password = (body.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    rows = db.query(AppDataRecord).filter(AppDataRecord.collection == "employees").all()

    username_matches: list[tuple[AppDataRecord, dict]] = []
    for row in rows:
        emp = _decode_payload(row)
        emp_username = (emp.get("username") or "").strip().lower()
        emp_email    = (emp.get("email")    or "").strip().lower()
        if emp_username == username or emp_email == username:
            username_matches.append((row, emp))

    if not username_matches:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    for row, emp in username_matches:
        if (emp.get("password") or "") != password:
            continue
        emp_key = row.record_key or (emp.get("id") or emp.get("name") or "")
        token   = _ess_token(row.company_id, emp_key, emp.get("name") or "")
        return {
            "access_token":    token,
            "token_type":      "bearer",
            "employee": {
                "id":          emp_key,
                "name":        emp.get("name")        or "",
                "designation": emp.get("designation") or "",
                "department":  emp.get("department")  or "",
                "employee_no": emp.get("id")          or "",
            },
            "allowed_modules": _resolve_ess_modules(row.company_id, emp, db),
        }

    raise HTTPException(status_code=401, detail="Invalid username or password")


@router.get("/profile")
def ess_profile(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    safe = {k: v for k, v in emp.items() if k not in ("password", "hashed_password")}
    return {
        "ok":              True,
        "employee":        safe,
        "allowed_modules": _resolve_ess_modules(company_id, emp, db),
    }


@router.get("/attendance")
def ess_attendance(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_id = emp.get("id") or emp_key
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
                "date":      p.punch_time[:10]    if p.punch_time else "",
                "time":      p.punch_time[11:16]  if p.punch_time and len(p.punch_time) > 10 else "",
                "direction": p.direction,
            }
            for p in punches
        ],
    }


@router.get("/payslips")
def ess_payslips(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_id   = emp.get("id") or emp_key
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
        for item in (run.get("items") or []):
            if (item.get("employee_id") or "").lower() == emp_id.lower() or \
               (item.get("employee") or "").strip().lower() == emp_name:
                slips.append({
                    "period":     run.get("period") or "",
                    "basic":      item.get("basic")      or 0,
                    "allowances": item.get("allowances") or 0,
                    "overtime":   item.get("overtime")   or 0,
                    "deductions": item.get("deductions") or 0,
                    "net_pay":    item.get("net_pay") or item.get("net") or 0,
                    "status":     run.get("status") or "draft",
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
    records = [r for rec in [_decode_payload(r) for r in rows]
               if (rec.get("employee") or "").strip().lower() == emp_name
               for r in [rec]]
    return {"ok": True, "records": records}


@router.post("/leave/apply")
def ess_leave_apply(body: LeaveApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""
    try:
        days = max(1, (_date.fromisoformat(body.to_date) - _date.fromisoformat(body.from_date)).days + 1)
    except Exception:
        days = 1
    record_id = f"LVE-{int(_time.time() * 1000)}"
    record = {
        "id": record_id, "employee": emp_name, "type": body.type,
        "from": body.from_date, "to": body.to_date, "days": days,
        "reason": body.reason, "status": "Pending",
        "submitted": datetime.now(UTC).isoformat(), "source": "ess",
    }
    db.add(AppDataRecord(company_id=company_id, collection="leaveRequests",
                         record_key=record_id, payload=json.dumps(record)))
    db.commit()
    return {"ok": True, "record": record}


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
    records = [rec for rec in [_decode_payload(r) for r in rows]
               if (rec.get("employee") or "").strip().lower() == emp_name]
    return {"ok": True, "records": records}


@router.post("/overtime/apply")
def ess_overtime_apply(body: OTApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""
    record_id = f"OT-{int(_time.time() * 1000)}"
    record = {
        "id": record_id, "employee": emp_name,
        "department": body.department or (emp.get("department") or ""),
        "date": body.date, "ot_hours": body.ot_hours, "reason": body.reason,
        "status": "Pending", "submitted": datetime.now(UTC).isoformat(), "source": "ess",
    }
    db.add(AppDataRecord(company_id=company_id, collection="overtimeRequests",
                         record_key=record_id, payload=json.dumps(record)))
    db.commit()
    return {"ok": True, "record": record}


@router.get("/documents")
def ess_documents(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    return {"ok": True, "documents": emp.get("documents") or {}}


@router.get("/corrections")
def ess_corrections(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "attendanceCorrections",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(50)
        .all()
    )
    records = [rec for rec in [_decode_payload(r) for r in rows]
               if (rec.get("employee") or "").strip().lower() == emp_name]
    return {"ok": True, "records": records}


@router.post("/corrections/apply")
def ess_corrections_apply(body: CorrectionApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""
    record_id = f"COR-{int(_time.time() * 1000)}"
    record = {
        "id": record_id, "employee": emp_name,
        "date": body.date, "in_time": body.in_time, "out_time": body.out_time,
        "reason": body.reason, "status": "Pending",
        "submitted": datetime.now(UTC).isoformat(), "source": "ess",
        "department": emp.get("department") or "",
    }
    db.add(AppDataRecord(company_id=company_id, collection="attendanceCorrections",
                         record_key=record_id, payload=json.dumps(record)))
    db.commit()
    return {"ok": True, "record": record}


@router.get("/loans")
def ess_loans(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "loanRequests",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(50)
        .all()
    )
    records = [rec for rec in [_decode_payload(r) for r in rows]
               if (rec.get("employee") or "").strip().lower() == emp_name]
    return {"ok": True, "records": records}


@router.post("/loans/apply")
def ess_loans_apply(body: LoanApplyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = emp.get("name") or ""
    record_id = f"LOAN-{int(_time.time() * 1000)}"
    record = {
        "id": record_id, "employee": emp_name, "type": body.type,
        "amount": body.amount, "purpose": body.purpose, "period": body.period,
        "status": "Pending", "submitted": datetime.now(UTC).isoformat(), "source": "ess",
        "department": emp.get("department") or "",
    }
    db.add(AppDataRecord(company_id=company_id, collection="loanRequests",
                         record_key=record_id, payload=json.dumps(record)))
    db.commit()
    return {"ok": True, "record": record}


@router.get("/expiry")
def ess_expiry(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    today = _date.today()
    doc_fields = [
        ("Passport",          emp.get("passport_expiry")    or ""),
        ("Emirates ID",       emp.get("eid_expiry")         or ""),
        ("Work Permit / Visa",emp.get("work_permit_expiry") or emp.get("visa_expiry") or ""),
        ("Driving License",   emp.get("driving_expiry")     or ""),
        ("Health Insurance",  emp.get("insurance_expiry")   or ""),
        ("Labor Card",        emp.get("labor_card_expiry")  or ""),
    ]
    result = []
    for label, expiry in doc_fields:
        if not expiry:
            continue
        try:
            exp_date  = _date.fromisoformat(expiry)
            days_left = (exp_date - today).days
            status    = "Expired" if days_left < 0 else "Expiring Soon" if days_left <= 30 else "Due Soon" if days_left <= 90 else "Valid"
        except Exception:
            days_left = None
            status    = "Unknown"
        result.append({"label": label, "expiry": expiry, "status": status, "days_left": days_left})
    return {"ok": True, "documents": result}


@router.get("/recruitment")
def ess_recruitment(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "recruitment",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(30)
        .all()
    )
    return {"ok": True, "jobs": [_decode_payload(r) for r in rows]}


@router.get("/rota")
def ess_rota(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()
    emp_id   = (emp.get("id") or emp_key).strip().lower()
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "rotaAssignments",
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(90)
        .all()
    )
    records = []
    for rec in [_decode_payload(r) for r in rows]:
        n = (rec.get("employeeName") or rec.get("employee_name") or rec.get("employee") or "").strip().lower()
        i = (rec.get("employeeId")   or rec.get("employee_id")   or "").strip().lower()
        if n == emp_name or i == emp_id:
            records.append(rec)
    return {"ok": True, "records": records}


@router.get("/assets")
def ess_assets(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()
    emp_id   = (emp.get("id") or emp_key).strip().lower()
    rows = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection.in_(["hrAssets", "employeeAssets", "assets"]),
        )
        .order_by(AppDataRecord.created_at.desc())
        .limit(50)
        .all()
    )
    records = []
    for rec in [_decode_payload(r) for r in rows]:
        n = (rec.get("employee") or rec.get("assignedTo") or rec.get("assigned_to") or "").strip().lower()
        i = (rec.get("employee_id") or rec.get("employeeId") or "").strip().lower()
        if n == emp_name or i == emp_id:
            records.append(rec)
    return {"ok": True, "assets": records}


@router.get("/performance")
def ess_performance(request: Request, db: Session = Depends(get_db)) -> dict:
    company_id, emp_key, emp = _auth(dict(request.headers), db)
    emp_name = (emp.get("name") or "").strip().lower()
    result = []
    for col in ("performanceReviews", "trainingRecords"):
        rows = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == company_id,
                AppDataRecord.collection == col,
            )
            .order_by(AppDataRecord.created_at.desc())
            .limit(30)
            .all()
        )
        for row in rows:
            rec = _decode_payload(row)
            if (rec.get("employee") or "").strip().lower() == emp_name:
                result.append({**rec, "_type": col})
    return {"ok": True, "records": result}
