import json
import re
import secrets
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app.cache as cache
import app.timezone_utils as timezone_utils
from app.auth_principal import resolve_department_scope
from app.config import get_settings
from app.database import get_db
from app.dependencies import assert_company_active, company_allows_module
from app.limiter import limiter
from app.rota_days import rota_day_statuses, rota_in_range
from app.models import AppDataRecord, AttendanceDetail, AuditLog, Company, Employee, LeaveRequest, PayrollItem, PayrollRun, Role
from app.routers.attendance import _company_offset, _ot_cooloff_seconds, overtime_eligibility_rows, _late_rules, _local_today, _standard_hours_per_day, _weekend_day_set
from app.routers.leave import (
    _ALLOWED_TYPES,
    _effective_leave_policy_configs,
    _employee_leave_policies,
    _leave_entitlement_days,
    _leave_type_caps,
    _used_days_by_employee_and_type,
)
from app.security import pwd_context, verify_employee_password

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
    # ISO currency code of the employee's company (Company.currency) -- the
    # payslip / net-pay figures were formatted with a hardcoded "AED" prefix
    # regardless of which country/currency the company actually runs in.
    currency: str | None = None
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
    # No `photo` field -- unlike /ess/me's own EssEmployeeOut (whose photo IS
    # rendered, for the logged-in employee's own avatar), grepping ess.js
    # confirmed no team-roster render ever reads a peer's .photo. Compressed
    # but still tens-of-KB-each base64 images for every colleague, on every
    # dashboard load, for a field nothing displays.


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

    if not verify_employee_password(password, emp.password_hash, emp.employee_no):
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
    company_name, currency = db.query(Company.name, Company.currency).filter(Company.id == emp.company_id).one()
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
        currency=currency,
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
    departments = resolve_department_scope(role, emp)
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
            designation=r.designation, status=r.status,
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

    rota_status = rota_day_statuses(db, emp.company_id, peer_nos, today, today)

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
            day_status = rota_status.get((p.employee_no, today_iso)) or "absent"
        check_in = None
        if detail_row and detail_row.clock_in_1:
            check_in = (detail_row.clock_in_1 + offset).strftime("%H:%M")
        out.append({
            "employee_id": p.id,
            "employee_no": p.employee_no,
            "full_name": p.full_name,
            "designation": p.designation,
            # No "photo" -- grepping ess.js confirmed no team-roster render
            # ever reads a colleague's photo (same reasoning as EssTeamMemberOut
            # above), so this was a pure per-colleague base64-blob payload cost
            # on every single dashboard load for a field nothing displays.
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
    used_map = _used_days_by_employee_and_type(db, emp.company_id, emp.id)   # one grouped query
    annual_used = used_map.get((emp.id, "Annual Leave"), 0)
    by_type = {}
    for leave_type, cap in caps.items():
        if leave_type == "Annual Leave":
            entitlement, used = annual_entitlement, annual_used
        else:
            entitlement = cap
            used = used_map.get((emp.id, leave_type), 0)
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
    if not verify_employee_password(payload.current_password, emp.password_hash, emp.employee_no):
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
        .filter(PayrollItem.employee_id == emp.id, PayrollRun.company_id == emp.company_id, PayrollRun.status.in_(("approved", "paid")))
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


def _leave_can_cancel(r: LeaveRequest, today: str) -> bool:
    """Same rule as ess_cancel_leave(): pending any time, approved until it starts."""
    return r.status == "pending" or (r.status == "approved" and r.start_date > today)


def _leave_out(r: LeaveRequest, today: str | None = None) -> dict:
    return {
        "id": r.id, "leave_type": r.leave_type, "start_date": r.start_date, "end_date": r.end_date,
        "days": r.days, "reason": r.reason, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "cancelled_by": r.cancelled_by, "cancel_reason": r.cancel_reason,
        "can_cancel": _leave_can_cancel(r, today) if today else r.status == "pending",
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
    today = _local_today(_company_offset(db, emp.company_id)).isoformat()
    return [_leave_out(r, today) for r in rows]


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
    if payload.start_date < date.today() - timedelta(days=30):
        raise HTTPException(status_code=400, detail="Leave can't be requested for dates more than 30 days in the past — contact HR")
    remaining = ess_leave_balance(request, db)["by_type"].get(payload.leave_type)
    if remaining is not None and "unpaid" not in payload.leave_type.lower() and days > remaining["remaining"]:
        raise HTTPException(status_code=400, detail=f"Only {remaining['remaining']} day(s) of {payload.leave_type} remaining; this request is {days}")
    req = LeaveRequest(
        company_id=emp.company_id, employee_id=emp.id, leave_type=payload.leave_type,
        start_date=payload.start_date.isoformat(), end_date=payload.end_date.isoformat(),
        days=days, reason=payload.reason, status="pending",
    )
    db.add(req)
    db.commit()
    return _leave_out(req)


def _rota_assignments_between(db: Session, company_id: str, lo: str, hi: str) -> list[dict]:
    """rotaAssignments dated lo..hi only, filtered in SQL rather than the whole rota history."""
    out = []
    query = db.query(AppDataRecord.payload).filter(AppDataRecord.company_id == company_id, *rota_in_range(lo, hi))
    for (raw,) in query.order_by(AppDataRecord.created_at, AppDataRecord.id).all():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and lo <= str(parsed.get("date") or "") <= hi:
            out.append(parsed)
    return out


def _employee_app_data_records(db: Session, company_id: str, collection: str) -> list[dict]:
    """Tier-2 collections (Task Management, Rota, and every own-request type:
    overtime/loans/advances/corrections) live in the generic AppDataRecord
    JSON bridge, not their own tables, so there's no SQL column to filter
    "this employee's rows" by -- every row for the collection has to be
    pulled and parsed, same approach attendance.py's Holiday Calendar lookup
    already uses for the same kind of collection. That scan is identical for
    every employee in the company (filtering to "mine" happens after, in
    _own_request_records()/callers), so a short cache means N employees
    loading their dashboards around the same time share one DB round trip +
    JSON-parse pass instead of each paying for their own -- same short-TTL
    pattern already used for dashboard/report queries (reports.py's
    _cached_or_build()). A no-op when Redis isn't configured (cache.py falls
    back silently), same as every other cache.get/set call in this codebase."""
    cache_key = f"ess_appdata:{company_id}:{collection}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
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
    cache.set(cache_key, out, ttl=20)
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
        a for a in _rota_assignments_between(db, emp.company_id, window_start, window_end)
        if a.get("employee_id") == emp.employee_no
    ]
    mine.sort(key=lambda a: a.get("date") or "")
    return mine


@router.get("/team/rota")
def ess_team_rota(
    request: Request,
    week: str = Query(..., description="YYYY-MM-DD -- the Monday starting the week to view"),
    db: Session = Depends(get_db),
) -> dict:
    """This employee's own department's rota for one Mon-Sun week -- the
    Rota tab's "Department Rota" view. Same "own department, everyone, no
    role-scope required" reach as /ess/team/today and /ess/team/leave, so
    a colleague's shift schedule is exactly as visible as their attendance
    and leave already are (not the narrower role-scoped /ess/team).

    Returns the peer roster and the week's raw assignments separately
    (rather than nesting one inside the other) so the frontend can build a
    Mon-Sun grid the same way HRMS's own Department Rota does, keyed by
    (employee_no, date) -- matching rotaAssignments' own id convention
    (see ess_rota()'s docstring on why employee_no, not emp.id, is the key
    every rotaAssignments row actually uses)."""
    emp = ess_bearer(request, db)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", week):
        raise HTTPException(status_code=400, detail="week must be in YYYY-MM-DD format")
    try:
        week_start = date.fromisoformat(week)
    except ValueError:
        raise HTTPException(status_code=400, detail="week must be a valid date")
    week_start_iso = week_start.isoformat()
    week_end_iso = (week_start + timedelta(days=6)).isoformat()

    peers = (
        db.query(Employee)
        .filter(Employee.company_id == emp.company_id, Employee.department == emp.department, Employee.status == "active")
        .order_by(Employee.full_name)
        .all()
    )
    if not peers:
        return {"employees": [], "assignments": []}

    peer_nos = {p.employee_no for p in peers}
    rows = [
        a for a in _rota_assignments_between(db, emp.company_id, week_start_iso, week_end_iso)
        if a.get("employee_id") in peer_nos
    ]
    return {
        "employees": [
            {
                "employee_no": p.employee_no, "full_name": p.full_name,
                "designation": p.designation, "is_me": p.id == emp.id,
            }
            for p in peers
        ],
        "assignments": rows,
    }


# ── Self-service requests, task updates, profile & documents ─────────────────
#
# Everything below reuses the SAME AppDataRecord collections the HRMS screens
# already read and approve (overtimeRequests, employeeLoans, salaryAdvances,
# attendanceCorrections) with the SAME record shape the HRMS "new request"
# modals write (see submitOTRequest()/saveLoan()/saveLoanAdvance()/
# saveCorrectionRequest() in app.js), so a request filed here shows up in the
# HR approval lists untouched and the existing approval side effects (an
# approved correction inserts real punches, approved OT feeds payroll) apply
# without any new HR-side code. The employee identity always comes from the
# ESS token -- never from the request body.

_REQUEST_COLLECTIONS = {
    "overtime": ("overtimeRequests", "OT"),
    "loan": ("employeeLoans", "LN"),
    "advance": ("salaryAdvances", "ADV"),
    "correction": ("attendanceCorrections", "CORR"),
}
_MAX_PENDING_PER_KIND = 10
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ess_audit(db: Session, emp: Employee, action: str, detail: dict) -> None:
    db.add(AuditLog(
        company_id=emp.company_id, employee_id=emp.id, module="ESS",
        action=action[:80], detail=json.dumps(detail, ensure_ascii=False, default=str)[:1000],
    ))


def _is_mine(rec: dict, emp: Employee) -> bool:
    """employee_id (= employee_no) is authoritative; older HRMS rows written
    before that field existed only carry the display name."""
    rid = str(rec.get("employee_id") or "").strip()
    if rid:
        return rid == emp.employee_no
    return str(rec.get("employee") or "").strip() == emp.full_name


def _own_request_records(db: Session, emp: Employee, collection: str) -> list[dict]:
    return [r for r in _employee_app_data_records(db, emp.company_id, collection) if _is_mine(r, emp)]


def _create_request_record(db: Session, emp: Employee, kind: str, fields: dict) -> dict:
    collection, prefix = _REQUEST_COLLECTIONS[kind]
    pending = [r for r in _own_request_records(db, emp, collection) if str(r.get("status") or "").lower() == "pending"]
    if len(pending) >= _MAX_PENDING_PER_KIND:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {len(pending)} pending {kind} requests -- wait for HR to review them first",
        )
    record = {
        "id": f"{prefix}-{int(datetime.now(UTC).timestamp() * 1000)}-{secrets.token_hex(2)}",
        "employee": emp.full_name,
        "employee_id": emp.employee_no,
        "department": emp.department,
        **fields,
        "status": "Pending",
        "submitted": _now_iso(),
        "source": "ess",
    }
    db.add(AppDataRecord(
        company_id=emp.company_id, branch_id=emp.branch_id, collection=collection,
        record_key=record["id"], payload=json.dumps(record, ensure_ascii=False, default=str),
    ))
    _ess_audit(db, emp, f"{kind}_requested", {"id": record["id"]})
    db.commit()
    # Bust the short-TTL scan cache _employee_app_data_records() just read
    # from above (`pending` check) so this employee's own "My Requests" list
    # reflects the request they just submitted immediately, not after
    # waiting out the cache's TTL.
    cache.delete(f"ess_appdata:{emp.company_id}:{collection}")
    return record


def _local_today_for(db: Session, emp: Employee) -> date:
    return _local_today(_company_offset(db, emp.company_id))


# ── quick wins: cancel a pending leave request / update own task status ──────

class EssLeaveCancelIn(BaseModel):
    reason: str | None = None


@router.post("/leave/{leave_id}/cancel")
def ess_cancel_leave(leave_id: str, request: Request, payload: EssLeaveCancelIn | None = None, db: Session = Depends(get_db)) -> dict:
    """An employee can cancel their OWN leave: a pending request any time, and
    approved leave until it starts (once it has started, HR cancels it in HRMS).
    Kept as a "cancelled" status (not a delete) so the history -- and HR's audit
    trail -- survives; balances only count approved leave, so the days return."""
    emp = ess_bearer(request, db)
    req = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.id == leave_id, LeaveRequest.company_id == emp.company_id, LeaveRequest.employee_id == emp.id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if req.status == "approved":
        today = _local_today(_company_offset(db, emp.company_id)).isoformat()
        if req.start_date <= today:
            raise HTTPException(status_code=409, detail="This leave has already started — ask HR to cancel it")
    elif req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Only pending or upcoming approved leave can be cancelled (this one is {req.status})")
    req.status = "cancelled"
    req.cancelled_at = datetime.now(UTC)
    req.cancelled_by = emp.full_name
    req.cancel_reason = ((payload.reason if payload else None) or "").strip()[:300] or None
    _ess_audit(db, emp, "leave_cancelled", {"id": req.id, "start": req.start_date, "end": req.end_date})
    db.commit()
    return _leave_out(req)


class EssTaskStatusIn(BaseModel):
    status: Literal["todo", "progress", "done"]


@router.patch("/tasks/{task_id}")
def ess_update_task(task_id: str, payload: EssTaskStatusIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Lets an employee move a task assigned to THEM between To Do / In
    Progress / Done. Only status (and progress, kept in step with it)
    changes -- title, assignee, due date and the rest stay HR/manager-controlled."""
    emp = ess_bearer(request, db)
    row = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == emp.company_id, AppDataRecord.collection == "tasks", AppDataRecord.record_key == task_id)
        .first()
    )
    data: dict = {}
    if row:
        try:
            data = json.loads(row.payload)
        except (TypeError, ValueError):
            data = {}
    if not row or not isinstance(data, dict) or data.get("assigned_to") != emp.id:
        raise HTTPException(status_code=404, detail="Task not found")
    was_done = data.get("status") == "done"
    data["status"] = payload.status
    # Keep progress in step with HRMS: Done is 100%, reopening a Done task starts again at 0.
    if payload.status == "done":
        data["progress"] = 100
    elif was_done:
        data["progress"] = 0
    row.payload = json.dumps(data, ensure_ascii=False, default=str)
    _ess_audit(db, emp, "task_status_changed", {"id": task_id, "status": payload.status})
    db.commit()
    cache.delete(f"ess_appdata:{emp.company_id}:tasks")
    return data


# ── attendance correction ────────────────────────────────────────────────────

class EssCorrectionIn(BaseModel):
    date: date
    checkin: str | None = None
    checkout: str | None = None
    reason: str


@router.post("/attendance-corrections", status_code=201)
def ess_create_correction(payload: EssCorrectionIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Request a fix for a missing/wrong punch. Goes to the same HRMS
    Attendance Corrections approval list; approving it there is what writes
    the real attendance events."""
    emp = ess_bearer(request, db)
    today = _local_today_for(db, emp)
    if payload.date > today:
        raise HTTPException(status_code=400, detail="You can't request a correction for a future date")
    if payload.date < today - timedelta(days=60):
        raise HTTPException(status_code=400, detail="Corrections can only be requested for the last 60 days -- contact HR for older dates")
    checkin = (payload.checkin or "").strip()
    checkout = (payload.checkout or "").strip()
    if not checkin and not checkout:
        raise HTTPException(status_code=400, detail="Enter the check-in time, the check-out time, or both")
    for label, value in (("check-in", checkin), ("check-out", checkout)):
        if value and not _HHMM.match(value):
            raise HTTPException(status_code=400, detail=f"The {label} time must be in HH:MM format")
    if checkin and checkout and checkout <= checkin:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Please give a short reason")
    date_iso = payload.date.isoformat()
    for r in _own_request_records(db, emp, "attendanceCorrections"):
        if str(r.get("status") or "").lower() == "pending" and str(r.get("date") or "") == date_iso:
            raise HTTPException(status_code=409, detail=f"You already have a pending correction for {date_iso}")
    return _create_request_record(db, emp, "correction", {
        "date": date_iso, "checkin": checkin, "checkout": checkout, "reason": reason[:500],
    })


# ── overtime / loan / salary advance ─────────────────────────────────────────

class EssOvertimeIn(BaseModel):
    date: date
    login: str | None = None
    logout: str | None = None
    ot_hours: float | None = None
    ot_type: Literal["normal", "ramadan", "weekend", "holiday"] = "normal"
    reason: str | None = None


def _ot_multiplier(db: Session, company_id: str, ot_type: str) -> str:
    """The rate the company configured under HR Settings > OT Rules -- looked
    up server-side so the employee can never pick their own multiplier."""
    row = (
        db.query(AppDataRecord.payload)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "hr_settings", AppDataRecord.record_key == "ot-rules-config")
        .first()
    )
    cfg: dict = {}
    if row:
        try:
            parsed = json.loads(row[0])
            cfg = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            cfg = {}

    def rate(key: str, default: float) -> float:
        try:
            v = float(cfg.get(key))
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    normal = rate("multNormal", 1.25)
    weekend = rate("multWeekend", 1.5)
    chosen = {
        "normal": normal,
        "weekend": weekend,
        "holiday": rate("multHoliday", weekend),
        "ramadan": rate("multRamadan", normal),
    }[ot_type]
    return f"{chosen:g}×"


@router.get("/overtime-eligibility")
def ess_overtime_eligibility(request: Request, db: Session = Depends(get_db)) -> dict:
    """The caller's own days worked past the standard day (last 31 days, the
    same window overtime can be requested in), with clock in/out, cool-off
    eligibility and request status."""
    emp = ess_bearer(request, db)
    today = _local_today_for(db, emp)
    start = today - timedelta(days=31)
    return {
        "from": start.isoformat(), "to": today.isoformat(),
        "cooloff_minutes": _ot_cooloff_seconds(db, emp.company_id) // 60,
        "rows": overtime_eligibility_rows(db, emp.company_id, [emp], start, today),
    }


@router.post("/overtime", status_code=201)
def ess_create_overtime(payload: EssOvertimeIn, request: Request, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    today = _local_today_for(db, emp)
    if payload.date > today:
        raise HTTPException(status_code=400, detail="Overtime can only be requested for today or a past date")
    if payload.date < today - timedelta(days=31):
        raise HTTPException(status_code=400, detail="Overtime must be requested within 31 days -- contact HR for older dates")
    login = (payload.login or "").strip()
    logout = (payload.logout or "").strip()
    for label, value in (("start", login), ("end", logout)):
        if value and not _HHMM.match(value):
            raise HTTPException(status_code=400, detail=f"The {label} time must be in HH:MM format")
    hours = payload.ot_hours
    if login and logout:
        start_m = int(login[:2]) * 60 + int(login[3:])
        end_m = int(logout[:2]) * 60 + int(logout[3:])
        if end_m <= start_m:
            end_m += 24 * 60      # shift crossing midnight
        hours = round((end_m - start_m) / 60, 2)
    if hours is None or not (0 < hours <= 12):
        raise HTTPException(status_code=400, detail="Overtime must be between more than 0 and 12 hours")
    return _create_request_record(db, emp, "overtime", {
        "date": payload.date.isoformat(), "shift": "", "login": login, "logout": logout,
        "ot_hours": str(hours), "ot_type": payload.ot_type,
        "multiplier": _ot_multiplier(db, emp.company_id, payload.ot_type),
        "reason": (payload.reason or "").strip()[:500],
    })


_LOAN_TYPES = ("Personal Loan", "Emergency Loan", "Medical Loan", "Home Furnishing Loan", "Education Loan", "Vehicle Loan")


class EssLoanIn(BaseModel):
    type: str = "Personal Loan"
    amount: float
    months: int
    reason: str | None = None


@router.post("/loans", status_code=201)
def ess_create_loan(payload: EssLoanIn, request: Request, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    if payload.type not in _LOAN_TYPES:
        raise HTTPException(status_code=400, detail="Unrecognized loan type")
    if not (0 < payload.amount <= 1_000_000):
        raise HTTPException(status_code=400, detail="Enter a loan amount greater than 0")
    if not (1 <= payload.months <= 60):
        raise HTTPException(status_code=400, detail="Repayment period must be between 1 and 60 months")
    amount = round(payload.amount, 2)
    return _create_request_record(db, emp, "loan", {
        "type": payload.type, "amount": amount, "emi": round(amount / payload.months, 2),
        "months": payload.months, "balance": amount, "deduct_from": "",
        "reason": (payload.reason or "").strip()[:500], "date": _local_today_for(db, emp).isoformat(),
    })


class EssAdvanceIn(BaseModel):
    amount: float
    month: str | None = None      # YYYY-MM the advance should be recovered from
    reason: str | None = None


@router.post("/advances", status_code=201)
def ess_create_advance(payload: EssAdvanceIn, request: Request, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    if not (0 < payload.amount <= 1_000_000):
        raise HTTPException(status_code=400, detail="Enter an advance amount greater than 0")
    month = (payload.month or "").strip() or _local_today_for(db, emp).strftime("%Y-%m")
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    return _create_request_record(db, emp, "advance", {
        "amount": round(payload.amount, 2), "month": month,
        "reason": (payload.reason or "").strip()[:500], "requested": _now_iso(),
    })


@router.get("/requests")
def ess_requests(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    """Every request this employee has filed -- leave (its own table) plus
    overtime, loans, salary advances and attendance corrections (HRMS's
    AppDataRecord collections) -- newest first, in one normalized shape for
    the Requests page, the dashboard list and the notification bell."""
    emp = ess_bearer(request, db)
    out: list[dict] = []
    today = _local_today(_company_offset(db, emp.company_id)).isoformat()
    for r in (
        db.query(LeaveRequest)
        .filter(LeaveRequest.company_id == emp.company_id, LeaveRequest.employee_id == emp.id)
        .order_by(LeaveRequest.created_at.desc())
        .limit(50)
        .all()
    ):
        out.append({
            "kind": "leave", "id": r.id, "status": (r.status or "pending").lower(),
            "submitted": r.created_at.isoformat() if r.created_at else None,
            "leave_type": r.leave_type, "start_date": r.start_date, "end_date": r.end_date, "days": r.days,
            "reason": r.reason, "can_cancel": _leave_can_cancel(r, today),
            "cancelled_by": r.cancelled_by, "cancel_reason": r.cancel_reason,
        })
    for kind, (collection, _prefix) in _REQUEST_COLLECTIONS.items():
        for r in _own_request_records(db, emp, collection):
            item = {
                "kind": kind, "id": r.get("id"), "status": str(r.get("status") or "pending").lower(),
                "submitted": r.get("submitted") or r.get("requested") or r.get("date"), "reason": r.get("reason"),
            }
            if kind == "overtime":
                item.update(date=r.get("date"), ot_hours=r.get("ot_hours"), ot_type=r.get("ot_type"), multiplier=r.get("multiplier"))
            elif kind == "loan":
                item.update(loan_type=r.get("type"), amount=r.get("amount"), months=r.get("months"), emi=r.get("emi"))
            elif kind == "advance":
                item.update(amount=r.get("amount"), month=r.get("month"))
            else:
                item.update(date=r.get("date"), checkin=r.get("checkin"), checkout=r.get("checkout"))
            out.append(item)
    out.sort(key=lambda x: str(x.get("submitted") or ""), reverse=True)
    return out[:100]


# ── profile details & document expiry ────────────────────────────────────────
#
# Contact details and document expiries live in the rich "employees"
# AppDataRecord (the HRMS Add/Edit Employee form), not on the SQL Employee row.

_EDITABLE_PROFILE_FIELDS = {
    "mobile": 30, "address": 300, "emergency_contact": 120, "emergency_mobile": 30,
}
_DOCUMENT_FIELDS = (
    ("Visa / Work Permit", ("visa_expiry", "visaExpiry")),
    ("Passport", ("passport_expiry", "passportExpiry")),
    ("Emirates ID", ("eid_expiry", "eidExpiry")),
    ("Labor Card", ("labor_card_expiry", "laborCardExpiry")),
    ("Insurance", ("insurance_expiry", "insuranceExpiry")),
    ("Driving License", ("driving_expiry", "drivingExpiry")),
)


def _employee_profile_row(db: Session, emp: Employee) -> AppDataRecord | None:
    row = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == emp.company_id, AppDataRecord.collection == "employees", AppDataRecord.record_key == emp.employee_no)
        .first()
    )
    return row


def _profile_payload(row: AppDataRecord | None) -> dict:
    if not row:
        return {}
    try:
        data = json.loads(row.payload)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/profile-details")
def ess_profile_details(request: Request, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    row = _employee_profile_row(db, emp)
    data = _profile_payload(row)
    return {
        "has_record": row is not None,
        "email": data.get("email") or "",
        **{field: data.get(field) or "" for field in _EDITABLE_PROFILE_FIELDS},
    }


class EssProfileDetailsIn(BaseModel):
    mobile: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    emergency_mobile: str | None = None


@router.put("/profile-details")
def ess_update_profile_details(payload: EssProfileDetailsIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Self-service edit of contact details only (phone, address, emergency
    contact). Name, department, salary, IBAN, documents and everything else
    on the employee record stay HR-controlled -- and every change is written
    to the audit log with the old and new values."""
    emp = ess_bearer(request, db)
    row = _employee_profile_row(db, emp)
    if not row:
        raise HTTPException(status_code=409, detail="Your HR profile record isn't set up yet -- please ask HR to complete it")
    data = _profile_payload(row)
    changes: dict[str, dict] = {}
    for field, max_len in _EDITABLE_PROFILE_FIELDS.items():
        new = getattr(payload, field)
        if new is None:
            continue
        new = new.strip()
        if len(new) > max_len:
            raise HTTPException(status_code=400, detail=f"{field.replace('_', ' ').capitalize()} is too long (max {max_len} characters)")
        if field.endswith("mobile") and new and not re.match(r"^[0-9+()\-\s]{5,30}$", new):
            raise HTTPException(status_code=400, detail="Phone numbers can only contain digits, spaces, + ( ) and -")
        old = str(data.get(field) or "")
        if new != old:
            changes[field] = {"old": old, "new": new}
            data[field] = new
    if changes:
        row.payload = json.dumps(data, ensure_ascii=False, default=str)
        _ess_audit(db, emp, "profile_contact_updated", {"changes": changes})
        db.commit()
    return {"ok": True, "changed": sorted(changes)}


@router.get("/documents")
def ess_documents(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    """The employee's own visa / passport / ID / insurance expiry dates, with
    the same thresholds HRMS's Expiry Alerts uses: <=30 days critical, <=90
    days due soon, blank = missing."""
    emp = ess_bearer(request, db)
    data = _profile_payload(_employee_profile_row(db, emp))
    today = _local_today_for(db, emp)
    out = []
    for label, keys in _DOCUMENT_FIELDS:
        raw = next((str(data.get(k)) for k in keys if data.get(k)), "")
        days = None
        if raw:
            try:
                days = (date.fromisoformat(raw[:10]) - today).days
            except ValueError:
                raw = ""
        if days is None:
            state = "missing"
        elif days < 0:
            state = "expired"
        elif days <= 30:
            state = "critical"
        elif days <= 90:
            state = "soon"
        else:
            state = "valid"
        out.append({"label": label, "expiry": raw[:10] if raw else None, "days_left": days, "state": state})
    return out
