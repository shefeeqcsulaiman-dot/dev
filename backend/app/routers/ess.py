import json
import re
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app.timezone_utils as timezone_utils
from app.auth_principal import _role_department_scope
from app.config import get_settings
from app.database import get_db
from app.dependencies import assert_company_active, company_allows_module
from app.limiter import limiter
from app.models import AppDataRecord, AttendanceDetail, Company, Employee, LeaveRequest, PayrollItem, PayrollRun, Role
from app.routers.attendance import _company_offset, _late_rules, _local_today, _standard_hours_per_day, _weekend_day_set
from app.routers.leave import (
    _ALLOWED_TYPES,
    _effective_leave_policy_configs,
    _employee_leave_policies,
    _leave_entitlement_days,
    _leave_type_caps,
    _used_days_for_type,
)
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
    photo: str | None = None
    company_name: str | None = None
    # The Dashboard hero's "Working Hours" chip -- shift_end is derived
    # (start time + standard hours/day), not a real per-employee shift
    # end time (no such column/setting exists yet); good enough for a
    # single at-a-glance chip, not something to treat as authoritative.
    shift_start: str | None = None
    shift_end: str | None = None


class EssChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class EssTeamMemberOut(BaseModel):
    id: str
    employee_no: str
    full_name: str
    department: str
    designation: str
    status: str
    photo: str | None = None


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
    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == emp.company_id).scalar()
    assert_company_active(expires_at)
    return emp


def ess_bearer(request: Request, db: Session = Depends(get_db)) -> Employee:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ESS token")
    token = auth[7:]
    return _get_employee_from_token(token, db)


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/login", response_model=EssToken)
@limiter.limit("10/minute")
def ess_login(request: Request, payload: EssLoginRequest, db: Session = Depends(get_db)) -> EssToken:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")

    stored_hash = emp.password_hash
    if not stored_hash:
        # default password = employee_no
        if password != emp.employee_no:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")
    else:
        if not pwd_context.verify(password, stored_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")

    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Portal access has been disabled for this account")

    # Superadmin's per-company Module Permissions — gated at login (there's no
    # per-request principal to hang a require_module check off of here, same
    # reasoning as hr_access.py's /login staying on the ungated router).
    modules_enabled = db.query(Company.modules_enabled).filter(Company.id == emp.company_id).scalar()
    if not company_allows_module(modules_enabled, "ess"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The ESS portal is not enabled for your company")
    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == emp.company_id).scalar()
    assert_company_active(expires_at)

    return EssToken(access_token=_create_ess_token(emp.id))


@router.get("/me", response_model=EssEmployeeOut)
def ess_me(request: Request, db: Session = Depends(get_db)) -> EssEmployeeOut:
    emp = ess_bearer(request, db)
    company_name = db.query(Company.name).filter(Company.id == emp.company_id).scalar()
    start_time_str, _grace_minutes = _late_rules(db, emp.company_id)
    standard_hours = _standard_hours_per_day(db, emp.company_id)
    start_h, start_m = (int(part) for part in start_time_str.split(":"))
    shift_end = (datetime(2000, 1, 1, start_h, start_m) + timedelta(hours=standard_hours)).strftime("%H:%M")
    return EssEmployeeOut(
        id=emp.id,
        employee_no=emp.employee_no,
        full_name=emp.full_name,
        department=emp.department,
        designation=emp.designation,
        status=emp.status,
        photo=emp.photo,
        company_name=company_name,
        shift_start=start_time_str,
        shift_end=shift_end,
    )


@router.get("/team", response_model=list[EssTeamMemberOut])
def ess_team(request: Request, db: Session = Depends(get_db)) -> list[EssTeamMemberOut]:
    """Employee roster for the departments the logged-in employee's role is
    scoped to (Add Custom Role's "Departments" field). Granting a role with
    one or more departments assigned -- then giving that employee ESS Portal
    Access -- is what turns this on; there's no separate permission
    checkbox. Empty scope (every role created before this existed, and any
    role left unscoped on purpose) means no team visibility here."""
    emp = ess_bearer(request, db)
    role = db.get(Role, emp.role_id) if emp.role_id else None
    departments = _role_department_scope(role)
    if not departments:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role is not scoped to any department")
    # Active only -- same "Inactive leaking through" bug class already
    # fixed for Rota (currentRotaStaff()) and Monthly Staff Overview: a
    # former employee has no business appearing in a live "who's on my
    # team" roster, even though the SQL row itself is deliberately kept
    # around for history elsewhere (payroll, past attendance/rota).
    rows = (
        db.query(Employee)
        .filter(
            Employee.company_id == emp.company_id,
            Employee.department.in_(departments),
            Employee.status == "active",
        )
        .order_by(Employee.full_name)
        .all()
    )
    return [
        EssTeamMemberOut(
            id=r.id, employee_no=r.employee_no, full_name=r.full_name, department=r.department,
            designation=r.designation, status=r.status, photo=r.photo,
        )
        for r in rows
    ]


@router.get("/team/today")
def ess_team_today(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    """Today's attendance status (Present/Absent/On Leave/Holiday/Weekend)
    for every active employee in the logged-in employee's OWN department --
    visible to every ESS user, unlike /ess/team above (which needs a
    department-scoped role and can span several departments at once). This
    is deliberately just "my immediate colleagues, today", the same
    present/leave/holiday/absent classification attendance_employee_daily()
    (attendance.py) computes for one employee's history, applied across a
    department for just today."""
    emp = ess_bearer(request, db)
    offset = _company_offset(db, emp.company_id)
    today = _local_today(offset)
    today_iso = today.isoformat()

    peers = (
        db.query(Employee)
        .filter(Employee.company_id == emp.company_id, Employee.department == emp.department, Employee.status == "active")
        .order_by(Employee.full_name)
        .all()
    )
    if not peers:
        return []

    weekend_days = _weekend_day_set(db, emp.company_id)
    js_dow = (today.weekday() + 1) % 7
    is_weekend = weekend_days is not None and js_dow in weekend_days

    holiday_dates: set[str] = set()
    for (raw,) in db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == emp.company_id, AppDataRecord.collection == "hrHolidays",
    ).all():
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        raw_date = str(payload.get("date") or payload.get("holiday_date") or "")
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            holiday_dates.add(raw_date)
    is_holiday = today_iso in holiday_dates

    on_leave_emp_ids = {
        lr.employee_id
        for lr in db.query(LeaveRequest).filter(
            LeaveRequest.company_id == emp.company_id,
            LeaveRequest.employee_id.in_([p.id for p in peers]),
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= today_iso,
            LeaveRequest.end_date >= today_iso,
        ).all()
    }

    peer_nos = [p.employee_no for p in peers]
    details_by_emp_no = {
        r.employee_id: r
        for r in db.query(AttendanceDetail).filter(
            AttendanceDetail.company_id == emp.company_id,
            AttendanceDetail.work_date == today_iso,
            AttendanceDetail.employee_id.in_(peer_nos),
        ).all()
    }

    out = []
    for p in peers:
        detail_row = details_by_emp_no.get(p.employee_no)
        has_punches = detail_row is not None and bool(json.loads(detail_row.raw_events or "[]"))
        if is_weekend:
            day_status = "weekend"
        elif is_holiday:
            day_status = "holiday"
        elif p.id in on_leave_emp_ids:
            day_status = "leave"
        elif has_punches:
            day_status = "present"
        else:
            day_status = "absent"
        check_in = None
        if detail_row and detail_row.clock_in_1:
            check_in = (detail_row.clock_in_1 + offset).strftime("%H:%M")
        out.append({
            "employee_id": p.id,
            "employee_no": p.employee_no,
            "full_name": p.full_name,
            "designation": p.designation,
            "photo": p.photo,
            "status": day_status,
            "check_in": check_in,
            "is_me": p.id == emp.id,
        })
    return out


@router.get("/holidays")
def ess_holidays(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    """The company Holiday Calendar (Settings > HR Settings > Holiday
    Calendar, the same hrHolidays collection) -- read-only here, visible to
    every ESS employee. No branch/department filtering: a holiday row's
    `location` field is free text typed into a prompt() (see addHoliday()
    in app.js), not a resolvable Branch/department reference, so there's
    nothing reliable to filter on yet -- every employee sees the same full
    list HR configured."""
    emp = ess_bearer(request, db)
    rows = _employee_app_data_records(db, emp.company_id, "hrHolidays")
    out = []
    for r in rows:
        raw_date = str(r.get("date") or r.get("holiday_date") or "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            continue
        out.append({
            "id": r.get("id"),
            "date": raw_date,
            "name": r.get("name") or "Holiday",
            "location": r.get("location") or "All branches",
            "paid": r.get("paid") if r.get("paid") is not None else True,
        })
    out.sort(key=lambda h: h["date"])
    return out


@router.get("/team/leave")
def ess_team_leave(
    request: Request,
    month: str = Query(..., description="YYYY-MM -- the month to check for approved leave"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Approved leave for this employee's own department peers within the
    given month -- for the Holiday Calendar's calendar-view overlay (shows
    who's already booked off, alongside company holidays). Same "own
    department, everyone, no role-scope required" reach as
    /ess/team/today, not the narrower role-scoped /ess/team -- every
    employee should be able to see when their immediate colleagues are
    out, the same way they can already see who's present today."""
    emp = ess_bearer(request, db)
    m = re.match(r"^(\d{4})-(\d{2})$", month)
    if not m:
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    year, mon = int(m.group(1)), int(m.group(2))
    if not (1 <= mon <= 12):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    month_start = date(year, mon, 1).isoformat()
    month_end = date(year, mon, monthrange(year, mon)[1]).isoformat()

    peers = (
        db.query(Employee)
        .filter(Employee.company_id == emp.company_id, Employee.department == emp.department, Employee.status == "active")
        .all()
    )
    peer_by_id = {p.id: p for p in peers}
    if not peer_by_id:
        return []

    rows = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.company_id == emp.company_id,
            LeaveRequest.employee_id.in_(list(peer_by_id.keys())),
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= month_end,
            LeaveRequest.end_date >= month_start,
        )
        .all()
    )
    return [
        {
            "employee_id": r.employee_id,
            "employee_name": peer_by_id[r.employee_id].full_name,
            "leave_type": r.leave_type,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "is_me": r.employee_id == emp.id,
        }
        for r in rows
    ]


@router.get("/leave-balance")
def ess_leave_balance(request: Request, db: Session = Depends(get_db)) -> dict:
    """This employee's own leave balance for the Dashboard's Leave Balance
    widget -- the same entitlement/used/remaining math /leave/balance
    (HRMS's admin-only Leave Balance Summary, gated on leave:view) already
    computes for every employee, scoped here to just the caller via the ESS
    bearer token instead. Annual Leave uses the policy-driven entitlement
    (_leave_entitlement_days, which can differ per employee's working-day
    policy); every other type uses its configured HR Settings > Leave
    Types cap -- both folded into one by_type dict, unlike /leave/balance's
    top-level annual_entitlement + a separate (flat-cap) by_type entry kept
    there only for that endpoint's own backward compatibility."""
    emp = ess_bearer(request, db)
    policies = _employee_leave_policies(db, emp.company_id)
    configs = _effective_leave_policy_configs(db, emp.company_id)
    caps = _leave_type_caps(db, emp.company_id)
    annual_entitlement = _leave_entitlement_days(emp.employee_no, policies, configs, default_days=caps.get("Annual Leave", 21))
    annual_used = _used_days_for_type(db, emp.company_id, emp.id, "Annual Leave")
    by_type = {}
    for leave_type, cap in caps.items():
        if leave_type == "Annual Leave":
            entitlement, used = annual_entitlement, annual_used
        else:
            entitlement = cap
            used = _used_days_for_type(db, emp.company_id, emp.id, leave_type)
        by_type[leave_type] = {"entitlement": entitlement, "used": used, "remaining": max(0, entitlement - used)}
    return {"by_type": by_type}


class EssAnnouncementOut(BaseModel):
    id: str
    title: str
    message: str
    date: str | None = None


@router.get("/announcements", response_model=list[EssAnnouncementOut])
def ess_announcements(request: Request, db: Session = Depends(get_db)) -> list[EssAnnouncementOut]:
    """Company-wide announcements for the Dashboard's Latest Announcements
    card. Read-only here -- posting one is an HR Settings action (Manager
    Portal > Post Announcement in HRMS), stored in the same
    companyAnnouncements AppDataRecord collection via the generic
    /app-data?action=save bridge every other HR setting already uses; an
    ESS token has no admin permission to post through this router."""
    emp = ess_bearer(request, db)
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == emp.company_id, AppDataRecord.collection == "companyAnnouncements")
        .order_by(AppDataRecord.created_at.desc())
        .limit(10)
        .all()
    )
    out: list[EssAnnouncementOut] = []
    for row in rows:
        try:
            payload = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        title = str(payload.get("title") or "").strip()
        if not title:
            continue
        out.append(EssAnnouncementOut(
            id=str(payload.get("id") or row.id),
            title=title,
            message=str(payload.get("message") or "").strip(),
            date=str(payload.get("date") or "").strip() or None,
        ))
    return out


@router.post("/change-password")
@limiter.limit("10/minute")
def ess_change_password(
    request: Request,
    payload: EssChangePasswordRequest,
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
    from app import attendance_store
    from app.models import AttendanceDetail
    # 60 days-with-a-row is a generous upper bound for "the last ~90
    # individual punches" -- a day rarely has more than a couple of scans.
    rows = (
        db.query(AttendanceDetail)
        .filter(
            AttendanceDetail.company_id == emp.company_id,
            AttendanceDetail.employee_id == emp.employee_no,
        )
        .order_by(AttendanceDetail.work_date.desc())
        .limit(60)
        .all()
    )
    # Same company-local offset attendance.py's /today endpoint already
    # applies (see _company_offset() there) — previously this returned
    # punch_time in raw UTC with no adjustment at all, so the same punch
    # showed a different clock time on the ESS portal than it did on the
    # HRMS Today's Attendance screen.
    country = db.query(Company.country).filter(Company.id == emp.company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)
    flattened = attendance_store.flatten_events(rows, offset)
    flattened.sort(key=lambda e: e["punch_time"], reverse=True)
    return [
        {
            "punch_date": e["punch_date"],
            "punch_time": str(e["punch_time"]),
            "direction": e["direction"],
            "source": e["source"],
        }
        for e in flattened[:90]
    ]


@router.get("/payslips")
def ess_payslips(request: Request, db: Session = Depends(get_db)) -> list:
    emp = ess_bearer(request, db)
    # PayrollItem.employee_id alone isn't practically exploitable (Employee.id
    # is a globally-unique UUID, never reused across companies), but an
    # explicit company_id filter costs nothing and matches the
    # defense-in-depth scoping every other cross-tenant query in this app
    # already uses rather than relying on an incidental UUID property.
    items = (
        db.query(PayrollItem, PayrollRun.period, PayrollRun.status)
        .join(PayrollRun, PayrollItem.run_id == PayrollRun.id)
        .filter(PayrollItem.employee_id == emp.id, PayrollRun.company_id == emp.company_id)
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


def _leave_out(r: LeaveRequest) -> dict:
    return {
        "id": r.id, "leave_type": r.leave_type, "start_date": r.start_date, "end_date": r.end_date,
        "days": r.days, "reason": r.reason, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/leave")
def ess_leave(request: Request, db: Session = Depends(get_db)) -> list:
    """The employee's own leave request history -- scoped by emp.id from the
    ESS token, the same "you can only ever see your own record" model
    /ess/attendance and /ess/payslips already use. Deliberately does not
    reuse GET /leave/requests (that endpoint lists the whole company for an
    HR/Manager Principal and is gated by leave:view, which an ESS token,
    not being a Principal at all, could never satisfy anyway)."""
    emp = ess_bearer(request, db)
    rows = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.company_id == emp.company_id, LeaveRequest.employee_id == emp.id)
        .order_by(LeaveRequest.created_at.desc())
        .limit(50)
        .all()
    )
    return [_leave_out(r) for r in rows]


class EssLeaveRequestIn(BaseModel):
    leave_type: str
    start_date: date
    end_date: date
    reason: str | None = None


@router.post("/leave", status_code=201)
def ess_create_leave(payload: EssLeaveRequestIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Lets an employee file their own leave request directly from the
    portal, rather than needing HR to enter it on their behalf in HRMS --
    previously the only leave-related thing ESS could do was nothing at
    all, leave was entirely HR-side. Mirrors create_leave_request()
    (leave.py)'s validation (allowed types, no overlapping pending/approved
    request) but always targets emp.id from the ESS token itself -- there
    is no employee_id field to trust from the request body here."""
    emp = ess_bearer(request, db)
    if payload.leave_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unrecognized leave type")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date cannot be before start date")
    overlap = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.company_id == emp.company_id,
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.status.in_(("pending", "approved")),
            LeaveRequest.start_date <= payload.end_date.isoformat(),
            LeaveRequest.end_date >= payload.start_date.isoformat(),
        )
        .first()
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail=f"This overlaps an existing {overlap.status} leave request ({overlap.start_date} to {overlap.end_date})",
        )
    days = (payload.end_date - payload.start_date).days + 1
    req = LeaveRequest(
        company_id=emp.company_id, employee_id=emp.id, leave_type=payload.leave_type,
        start_date=payload.start_date.isoformat(), end_date=payload.end_date.isoformat(),
        days=days, reason=payload.reason, status="pending",
    )
    db.add(req)
    db.commit()
    return _leave_out(req)


def _employee_app_data_records(db: Session, company_id: str, collection: str) -> list[dict]:
    """Tier-2 collections (Task Management, Rota) live in the generic
    AppDataRecord JSON bridge, not their own tables, so there's no SQL
    column to filter "this employee's rows" by -- every row for the
    collection has to be pulled and parsed, same approach attendance.py's
    Holiday Calendar lookup already uses for the same kind of collection."""
    rows = db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == collection,
    ).all()
    out = []
    for (raw,) in rows:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


@router.get("/tasks")
def ess_tasks(request: Request, db: Session = Depends(get_db)) -> list:
    """Only tasks assigned to THIS employee (payload.assigned_to == emp.id,
    the same Employee.id the Task Management "Assign To" dropdown saves --
    see populateTaskAssigneeSelect() in app.js) -- never the whole board."""
    emp = ess_bearer(request, db)
    tasks = _employee_app_data_records(db, emp.company_id, "tasks")
    mine = [t for t in tasks if t.get("assigned_to") == emp.id]
    mine.sort(key=lambda t: t.get("due_date") or "9999-99-99")
    return mine


@router.get("/rota")
def ess_rota(
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM -- view a specific month instead of the rolling default window"),
    db: Session = Depends(get_db),
) -> list:
    """Only this employee's own rota assignments. Two windows:

    - Default (no `month`): a recent-past-to-near-future rolling window --
      an employee's full rota history could be large and nobody needs to
      see last year's shifts on their phone by default. Matches the
      30-day window most of the rest of HRMS already defaults to for
      "recent" data. This is what the Dashboard's Today's Schedule card
      and the initial Rota tab load both use.
    - `month=YYYY-MM`: the Rota tab's own month picker -- an employee
      checking a specific past or future month isn't asking for "recent",
      they're asking for that exact month, so the rolling window doesn't
      apply here at all.

    Matched by employee_no, NOT emp.id -- confirmed against live data that
    Rota (app.js's currentRotaStaff()/employeeFromDirectoryRow()) keys
    every rotaAssignments row's employee_id off the Employee Directory
    table's visible "ID" column, which displays employee_no, not the
    Employee.id UUID. This is a genuinely different convention from Task
    Management (populateTaskAssigneeSelect() keys "tasks".assigned_to off
    the real Employee.id UUID) -- the two Tier 2 collections are NOT
    consistent with each other. An earlier version of this endpoint
    (2026-09-02) assumed the Tasks convention here too and silently
    returned empty for every real employee with real rota data."""
    emp = ess_bearer(request, db)
    assignments = _employee_app_data_records(db, emp.company_id, "rotaAssignments")
    if month:
        m = re.match(r"^(\d{4})-(\d{2})$", month)
        if not m:
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
        year, mon = int(m.group(1)), int(m.group(2))
        if not (1 <= mon <= 12):
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
        window_start = date(year, mon, 1).isoformat()
        window_end = date(year, mon, monthrange(year, mon)[1]).isoformat()
    else:
        today = date.today()
        window_start = (today - timedelta(days=7)).isoformat()
        window_end = (today + timedelta(days=30)).isoformat()
    mine = [
        a for a in assignments
        if a.get("employee_id") == emp.employee_no and window_start <= (a.get("date") or "") <= window_end
    ]
    mine.sort(key=lambda a: a.get("date") or "")
    return mine
