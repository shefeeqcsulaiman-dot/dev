from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import company_allows_module
from app.models import Company, Employee, PayrollItem, PayrollRun
from app.security import pwd_context

router = APIRouter(prefix="/ess", tags=["ess"])
settings = get_settings()

_ESS_PREFIX = "emp:"


class EssLoginRequest(BaseModel):
    username: str  # employee_no (requires company_id) or a globally-unique portal username
    password: str
    # Optional: portal usernames are unique platform-wide (see
    # uq_employees_username in main.py), so a username-based login can
    # resolve the company on its own. company_id is still required when
    # logging in with employee_no, which is only unique *within* a company —
    # without it, "Employee #1" at two unrelated companies would collide.
    company_id: str | None = None


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


class EssChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Portal access has been disabled for this account")
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
    company_id = (payload.company_id or "").strip()

    if company_id:
        # Company-scoped link (?c=<company_id>): match employee_no OR
        # username within that company. employee_no has no uniqueness
        # guarantee across different tenant companies (e.g. two unrelated
        # companies can each have an "Employee #1") — without this filter,
        # an employee at one company could log in as a same-numbered
        # employee at a different one.
        emp = db.query(Employee).filter(
            Employee.company_id == company_id,
            (Employee.employee_no.ilike(username)) | (Employee.username.ilike(username)),
        ).first()
    else:
        # No company reference — only the portal username can resolve this
        # safely, since uq_employees_username enforces it's unique across
        # every company on the platform. employee_no is NOT unique
        # platform-wide, so it cannot be used to log in without company_id.
        emp = db.query(Employee).filter(Employee.username.ilike(username)).first()

    # Verify password — default password is the employee_no itself, until the
    # employee sets a real one via POST /ess/change-password, or HR sets one
    # directly via HRMS > Users & Roles.
    if not emp:
        # constant-time dummy check
        pwd_context.verify(password, "$2b$12$Z2HUw9SswHis7rcngsd7iOdXn/b9HafcmcwJx9D39ozeKwrSy22r.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    stored_hash = emp.password_hash
    if not stored_hash:
        # default password = employee_no
        if password != emp.employee_no:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        if not pwd_context.verify(password, stored_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Portal access has been disabled for this account")

    # Superadmin's per-company Module Permissions — gated at login (there's no
    # per-request principal to hang a require_module check off of here, same
    # reasoning as hr_access.py's /login staying on the ungated router).
    modules_enabled = db.query(Company.modules_enabled).filter(Company.id == emp.company_id).scalar()
    if not company_allows_module(modules_enabled, "ess"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The ESS portal is not enabled for your company")

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


@router.post("/change-password")
def ess_change_password(
    payload: EssChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    emp = ess_bearer(request, db)
    stored_hash = emp.password_hash
    current_ok = (
        pwd_context.verify(payload.current_password, stored_hash)
        if stored_hash
        else payload.current_password == emp.employee_no
    )
    if not current_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 6 characters")
    emp.password_hash = pwd_context.hash(payload.new_password)
    db.add(emp)
    db.commit()
    return {"ok": True}


@router.get("/attendance")
def ess_attendance(request: Request, db: Session = Depends(get_db)) -> list:
    emp = ess_bearer(request, db)
    from app.models import AttendancePunch
    punches = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.company_id == emp.company_id,
            AttendancePunch.employee_id == emp.employee_no,
        )
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
