from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Employee, PayrollItem, PayrollRun
from app.security import pwd_context

router = APIRouter(prefix="/ess", tags=["ess"])
settings = get_settings()

_ESS_PREFIX = "emp:"


class EssLoginRequest(BaseModel):
    username: str  # employee_no, or email stored in ext fields
    password: str


class EssToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EssEmployeeOut(BaseModel):
    id: str
    employee_no: str
    full_name: str
    department: str
    designation: str
    status: str


# ── helpers ──────────────────────────────────────────────────────────────────

def _create_ess_token(employee_id: str) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": _ESS_PREFIX + employee_id, "exp": exp},
        settings.secret_key,
        algorithm="HS256",
    )


def _employee_id_from_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        sub: str | None = payload.get("sub")
        if sub and sub.startswith(_ESS_PREFIX):
            return sub[len(_ESS_PREFIX):]
    except JWTError:
        pass
    return None


def _get_employee_from_token(token: str, db: Session) -> Employee:
    emp_id = _employee_id_from_token(token)
    if not emp_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ESS token")
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Employee not found")
    return emp


def ess_bearer(request: Request, db: Session = Depends(get_db)) -> Employee:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ESS token")
    token = auth[7:]
    return _get_employee_from_token(token, db)


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/login", response_model=EssToken)
def ess_login(payload: EssLoginRequest, db: Session = Depends(get_db)) -> EssToken:
    username = payload.username.strip()
    password = payload.password

    # Look up employee by employee_no (case-insensitive)
    emp = db.query(Employee).filter(
        Employee.employee_no.ilike(username)
    ).first()

    # Verify password — default password is the employee_no itself
    if not emp:
        # constant-time dummy check
        pwd_context.verify(password, "$2b$12$Z2HUw9SswHis7rcngsd7iOdXn/b9HafcmcwJx9D39ozeKwrSy22r.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    stored_hash = getattr(emp, "password_hash", None)
    if not stored_hash:
        # default password = employee_no
        if password != emp.employee_no:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        if not pwd_context.verify(password, stored_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return EssToken(access_token=_create_ess_token(emp.id))


@router.get("/me", response_model=EssEmployeeOut)
def ess_me(request: Request, db: Session = Depends(get_db)) -> EssEmployeeOut:
    emp = ess_bearer(request, db)
    return EssEmployeeOut(
        id=emp.id,
        employee_no=emp.employee_no,
        full_name=emp.full_name,
        department=emp.department,
        designation=emp.designation,
        status=emp.status,
    )


@router.get("/attendance")
def ess_attendance(request: Request, db: Session = Depends(get_db)) -> list:
    emp = ess_bearer(request, db)
    from app.models import AttendancePunch
    punches = (
        db.query(AttendancePunch)
        .filter(AttendancePunch.employee_id == emp.employee_no)
        .order_by(AttendancePunch.punch_time.desc())
        .limit(90)
        .all()
    )
    return [
        {
            "punch_date": p.punch_date,
            "punch_time": str(p.punch_time),
            "direction": p.direction,
            "source": p.source,
        }
        for p in punches
    ]


@router.get("/payslips")
def ess_payslips(request: Request, db: Session = Depends(get_db)) -> list:
    emp = ess_bearer(request, db)
    items = (
        db.query(PayrollItem, PayrollRun.period, PayrollRun.status)
        .join(PayrollRun, PayrollItem.run_id == PayrollRun.id)
        .filter(PayrollItem.employee_id == emp.id)
        .order_by(PayrollRun.period.desc())
        .limit(24)
        .all()
    )
    return [
        {
            "period": period,
            "run_status": run_status,
            "basic": float(item.basic),
            "allowances": float(item.allowances),
            "overtime": float(item.overtime),
            "deductions": float(item.deductions),
            "net_pay": float(item.net_pay),
        }
        for item, period, run_status in items
    ]
