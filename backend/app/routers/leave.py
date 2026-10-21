import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, true
from sqlalchemy.orm import Session

from app.auth_principal import resolve_active_branch
from app.department_scope import assert_employee_in_scope, scope_employee_query
from app.database import get_db
from app.dependencies import Principal, require_module, require_principal_permission
from app.models import AppDataRecord, Employee, LeaveRequest

router = APIRouter(prefix="/leave", tags=["leave"], dependencies=[Depends(require_module("hrms"))])

_ALLOWED_TYPES = {
    "Annual Leave", "Sick Leave", "Casual Leave", "Emergency Leave", "Maternity Leave",
    "Paternity Leave", "Unpaid Leave", "Lieu Days", "Hajj Leave", "Work From Home",
}

# Mirrors app.js's _LEAVE_POLICY_DAYS — the frontend maps the real
# #emp-leave-policy <option value="..."> strings to entitlement days
# already; this was never mirrored server-side, so /leave/balance and the
# approval-time entitlement check used a flat 30 for every employee
# regardless of their actual policy. `Employee` (the SQL model) has no
# leave_policy column — it only exists in the "employees" AppDataRecord
# blob the employee form saves — so this reads that blob by employee_no,
# the same cross-reference pattern payroll.py already uses for loans/OT.
_LEAVE_POLICY_DAYS = {
    "UAE 30 Calendar": 30,
    "UAE Standard": 21,
    "Internal 22 Working": 22,
    "Internal 29 Working": 29,
    "Executive 30 Working": 30,
    "Contractor": 14,
}

# Matches each named policy's "Basis" column default in HR Settings > Leave
# Policy > Named Leave Policies. Three of the six are explicitly Working-day
# entitlements (weekends excluded from the count), not Calendar-day ones —
# create_leave_request() below previously counted every leave request's
# days as flat calendar days regardless of the assigned policy's basis, a
# ~40% overcount against entitlement for anyone on a Working-day policy
# (a 22-working-day fortnight off spans 30-31 calendar days).
_DEFAULT_LEAVE_POLICY_BASIS = {
    "UAE 30 Calendar": "Calendar",
    "UAE Standard": "Calendar",
    "Internal 22 Working": "Working",
    "Internal 29 Working": "Working",
    "Executive 30 Working": "Working",
    "Contractor": "Calendar",
}

# UAE Labour Law weekend is Saturday+Sunday for most employers (date.weekday():
# Mon=0 ... Sat=5, Sun=6) — used only for Working-basis policies above.
_UAE_WEEKEND_WEEKDAYS = {5, 6}


def _working_days_count(start: date, end: date) -> int:
    days = 0
    current = start
    while current <= end:
        if current.weekday() not in _UAE_WEEKEND_WEEKDAYS:
            days += 1
        current += timedelta(days=1)
    return days


def _effective_leave_policy_configs(db: Session, company_id: str) -> dict[str, dict]:
    """policy value -> {"days": int, "basis": "Calendar"|"Working"}, merging
    the hardcoded defaults above with whatever an admin has actually saved
    in HR Settings > Leave Policy (the "hrLeavePolicy" blob's leave_policies
    list, written by saveLeavePolicies() in app.js). The frontend's own copy
    of this map already synced with admin edits (_applyLeavePolicyDaysToMap
    in app.js) — this backend copy, the one actually enforced at approval
    time, previously stayed a frozen literal forever, so the UI and the real
    enforcement gate could disagree indefinitely."""
    configs = {
        name: {"days": days, "basis": _DEFAULT_LEAVE_POLICY_BASIS.get(name, "Calendar")}
        for name, days in _LEAVE_POLICY_DAYS.items()
    }
    row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "hrLeavePolicy",
            AppDataRecord.record_key == "leave-policy",
        )
        .first()
    )
    if row:
        try:
            data = json.loads(row.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        for entry in (data.get("leave_policies") or []) if isinstance(data, dict) else []:
            value = str(entry.get("value") or "").strip()
            if not value:
                continue
            cfg = configs.setdefault(value, {"days": 21, "basis": "Calendar"})
            days_raw = entry.get("days")
            if days_raw not in (None, ""):
                try:
                    cfg["days"] = int(float(days_raw))
                except (TypeError, ValueError):
                    pass
            basis = str(entry.get("basis") or "").strip()
            if basis in ("Calendar", "Working"):
                cfg["basis"] = basis
            # Flexible: the whole year's Annual Leave is available from the start of the
            # leave year instead of being earned month by month (leave_entitlement()).
            cfg["flexible"] = str(entry.get("flexible") or "").strip().lower() == "yes"
    return configs


def _employee_leave_policies(db: Session, company_id: str) -> dict[str, str]:
    """employee_no -> leave_policy string, from the employees app-data blob."""
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "employees")
        .all()
    )
    policies: dict[str, str] = {}
    for row in rows:
        try:
            data = json.loads(row.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        emp_no = str(data.get("id") or data.get("employee_no") or "").strip()
        policy = str(data.get("leave_policy") or "").strip()
        if emp_no and policy:
            policies[emp_no] = policy
    return policies


def _leave_entitlement_days(
    employee_no: str, policies: dict[str, str], configs: dict[str, dict] | None = None, default_days: int = 21,
) -> int:
    """An employee with no named leave policy assigned (#emp-leave-policy
    left blank -- the common case for anyone HR hasn't explicitly set it
    for) previously fell back to a hardcoded 21, completely ignoring
    whatever HR actually configured for "Annual Leave" in HR Settings >
    Leave Types & Entitlements. `default_days` -- the caller's own
    _leave_type_caps(...).get("Annual Leave") -- is that real configured
    value; 21 only survives as the final fallback if even that was never
    set."""
    name = policies.get(employee_no, "")
    if configs is not None and name in configs:
        return configs[name]["days"]
    return _LEAVE_POLICY_DAYS.get(name, default_days)


# Only "Annual Leave" ever had its cap enforced at approval time — Sick,
# Emergency, Maternity, Paternity, and Hajj all had configured caps in HR
# Settings > Leave Types that nothing ever actually checked, so an employee
# could be approved for an unlimited number of days of any of them. These
# match that settings screen's own defaults; an admin's saved leave_types
# table (read below) overrides them per company. Unpaid Leave and Work From
# Home are deliberately excluded — neither is a capped entitlement.
_DEFAULT_LEAVE_TYPE_CAPS = {
    "Annual Leave": 21,
    "Sick Leave": 90,
    "Emergency Leave": 5,
    "Maternity Leave": 60,
    "Paternity Leave": 5,
    "Hajj Leave": 30,
}
_UNCAPPED_LEAVE_TYPES = {"Unpaid Leave", "Work From Home"}


def _leave_type_caps(db: Session, company_id: str) -> dict[str, int]:
    """leave_type -> configured day cap, from HR Settings > Leave Types
    (the "hrLeavePolicy" blob's leave_types list) — falls back to the
    defaults above for any type the admin hasn't customized."""
    caps = dict(_DEFAULT_LEAVE_TYPE_CAPS)
    row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "hrLeavePolicy",
            AppDataRecord.record_key == "leave-policy",
        )
        .first()
    )
    if row:
        try:
            data = json.loads(row.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        for entry in (data.get("leave_types") or []) if isinstance(data, dict) else []:
            type_name = str(entry.get("type") or "").strip()
            days_raw = entry.get("days")
            if not type_name or days_raw in (None, ""):
                continue
            try:
                caps[type_name] = int(float(days_raw))
            except (TypeError, ValueError):
                continue
    return caps


# ── Day counting and entitlement by policy (2026-10-17) ─────────────────────
# Leave days: a request counts calendar days, or -- on a Working basis -- skips the
# company's weekend (HR Rules > Weekend Policy), Holiday Calendar days and the employee's
# rota days off. The basis is the employee's named policy for Annual Leave when one is
# assigned, else the leave type's own Basis (HR Settings > Leave Types), else Calendar.
# Entitlement: a policy's yearly days are earned by HR Settings > Accrual from the
# employee's join date, within the leave year set by "Leave year start".

def _leave_settings(db: Session, company_id: str) -> dict:
    row = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "hrLeavePolicy",
        AppDataRecord.record_key == "leave-policy",
    ).first()
    if not row:
        return {}
    try:
        data = json.loads(row.payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _leave_type_basis(settings: dict, leave_type: str) -> str | None:
    for entry in settings.get("leave_types") or []:
        if isinstance(entry, dict) and str(entry.get("type") or "").strip() == leave_type:
            basis = str(entry.get("basis") or "").strip()
            return basis if basis in ("Calendar", "Working") else None
    return None


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _to_float(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def carry_deadline(settings: dict, window_start: date, window_end: date) -> date | None:
    """Last day carried-forward leave can be used, from HR Settings > Carry-Forward Deadline:
    the end of that month in the leave year ("End of March"), the end of the leave year, or
    None for "No deadline"."""
    text = str(settings.get("carry_deadline") or "End of March")
    if text.startswith("No"):
        return None
    if "Leave Year" in text:
        return window_end - timedelta(days=1)
    months = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                          "September", "October", "November", "December"], 1)}
    month = next((n for name, n in months.items() if name in text), 3)
    year = window_start.year if month >= window_start.month else window_start.year + 1
    return _add_months(date(year, month, 1), 1) - timedelta(days=1)


def carry_forward_days(settings: dict, profile: dict, window: tuple[date, date], on: date,
                       used_before_deadline: float = 0) -> tuple[float, float, date | None]:
    """(days counted now, days carried, deadline) of this employee's carried-forward Annual
    Leave in the leave year `window`: what HR entered (capped by the Annual Leave type's Max
    Carry when set), for the leave year it was entered in. After the deadline only the
    carried days already used before it still count; the rest expire."""
    carried = float(profile.get("carry") or 0)
    entered = profile.get("carry_entered")
    ws, we = window
    if carried <= 0 or (entered and not (ws <= entered < we)):
        return 0, 0, None
    for entry in settings.get("leave_types") or []:
        if isinstance(entry, dict) and str(entry.get("type") or "") == "Annual Leave":
            cap = _to_float(entry.get("max_carry"))
            if cap > 0:
                carried = min(carried, cap)
    deadline = carry_deadline(settings, ws, we)
    if deadline is not None and entered and entered > deadline:
        # Entered after this year's deadline (e.g. when the company starts using the app
        # mid-year): usable for the rest of the leave year rather than already expired.
        deadline = we - timedelta(days=1)
    counted = carried if deadline is None or on <= deadline else min(carried, used_before_deadline)
    as_num = lambda v: int(v) if float(v).is_integer() else v
    return as_num(counted), as_num(carried), deadline


def _annual_used_before(db: Session, company_id: str, employee_id: str, window: tuple[date, date], deadline: date | None) -> int:
    if deadline is None:
        return 0
    total = db.query(func.coalesce(func.sum(LeaveRequest.days), 0)).filter(
        LeaveRequest.company_id == company_id, LeaveRequest.employee_id == employee_id,
        LeaveRequest.leave_type == "Annual Leave", LeaveRequest.status == "approved",
        LeaveRequest.start_date >= window[0].isoformat(), LeaveRequest.start_date <= deadline.isoformat(),
    ).scalar()
    return int(total or 0)


def _employee_profiles(db: Session, company_id: str) -> dict[str, dict]:
    """employee_no -> {"policy": str, "join_date": date | None} from the employees records."""
    out: dict[str, dict] = {}
    for row in db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id,
                                              AppDataRecord.collection == "employees").all():
        try:
            data = json.loads(row.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        emp_no = str(data.get("id") or data.get("employee_no") or "").strip()
        if emp_no:
            out[emp_no] = {"policy": str(data.get("leave_policy") or "").strip(),
                           "join_date": _parse_date(data.get("join_date") or data.get("joining_date")),
                           # Unused Annual Leave carried from last year, entered by HR on the employee
                           # (carry_forward_days()); applies to the leave year it was entered in.
                           "carry": _to_float(data.get("leave_carry_forward")),
                           "carry_entered": _parse_date(data.get("leave_carry_entered"))}
    return out


_MONTHS = {"January": 1, "April": 4, "July": 7, "October": 10}


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28) if d.day > 28 else d.day)


def leave_year_window(settings: dict, join_date: date | None, on: date) -> tuple[date, date]:
    """[start, end) of the leave year containing `on`: from "Leave year start" (a month, or
    "Joining Date (rolling)" = each anniversary of the join date)."""
    start_setting = str(settings.get("year_start") or "January")
    if start_setting.startswith("Joining") and join_date:
        anchor = join_date
        years = on.year - anchor.year - ((on.month, on.day) < (anchor.month, anchor.day))
        start = _add_months(anchor, 12 * max(0, years))
        return start, _add_months(start, 12)
    month = _MONTHS.get(start_setting, 1)
    start = date(on.year if on.month >= month else on.year - 1, month, 1)
    return start, _add_months(start, 12)


def _months_between(a: date, b: date) -> int:
    """Months a..b counting both ends' months (1 Aug..5 Oct = 3)."""
    return (b.year - a.year) * 12 + (b.month - a.month) + 1


def accrued_days(settings: dict, yearly_days: float, join_date: date | None, on: date) -> float:
    """Days of a yearly entitlement earned by `on` in its leave year, per HR Settings >
    Accrual: Monthly (1/12 earned at the start of each month of service), Quarterly (1/4
    per quarter), or Upfront (the whole year, prorated by join date in the first year)."""
    if yearly_days <= 0:
        return 0.0
    ws, we = leave_year_window(settings, join_date, on)
    service_start = max(ws, join_date) if join_date else ws
    if join_date and join_date > on:
        return 0.0
    accrual = str(settings.get("accrual") or "Monthly")
    last_month = we - timedelta(days=1)
    if accrual.startswith("Upfront"):
        earned = yearly_days * _months_between(service_start, last_month) / 12
    elif accrual.startswith("Quarterly"):
        def quarter(d: date) -> int:
            return ((d.year - ws.year) * 12 + d.month - ws.month) // 3
        earned = yearly_days * (quarter(on) - quarter(service_start) + 1) / 4
    else:
        earned = yearly_days * _months_between(service_start, on) / 12
    value = round(min(yearly_days, max(0.0, earned)) * 2) / 2   # to the half day
    return int(value) if float(value).is_integer() else value


def leave_basis(db: Session, company_id: str, employee_no: str, leave_type: str,
                settings: dict | None = None, profiles: dict | None = None) -> str:
    settings = settings if settings is not None else _leave_settings(db, company_id)
    if leave_type == "Annual Leave":
        profiles = profiles if profiles is not None else _employee_profiles(db, company_id)
        policy = (profiles.get(employee_no) or {}).get("policy") or ""
        if policy:
            configs = _effective_leave_policy_configs(db, company_id)
            if policy in configs:
                return configs[policy]["basis"]
            if policy in _DEFAULT_LEAVE_POLICY_BASIS:
                return _DEFAULT_LEAVE_POLICY_BASIS[policy]
    return _leave_type_basis(settings, leave_type) or "Calendar"


def leave_day_breakdown(db: Session, company_id: str, employee_no: str, leave_type: str,
                        start: date, end: date) -> dict:
    """The days a request uses, and which days it skipped and why. A rota day off is never
    counted, for every leave type (the employee was not due to work); "Working" types also
    skip the weekend and public holidays, "Calendar" types count those."""
    basis = leave_basis(db, company_id, employee_no, leave_type)
    total = (end - start).days + 1
    if total <= 0:
        return {"basis": basis, "days": 0, "calendar_days": 0, "skipped": {}}
    from app.rota_days import rota_day_statuses
    if basis != "Working":
        from app.routers.attendance import _holiday_dates
        rota = rota_day_statuses(db, company_id, [employee_no], start, end)
        holidays = _holiday_dates(db, company_id) if rota else set()
        # A public holiday with no rota entry is the holiday, which Calendar types count.
        off = sum(1 for (_eno, iso), kind in rota.items() if kind == "off" and iso not in holidays)
        return {"basis": basis, "days": total - off, "calendar_days": total, "skipped": {"day_off": off} if off else {}}
    from app.routers.attendance import _holiday_dates, _weekend_day_set
    weekend = _weekend_day_set(db, company_id)              # JS getDay numbers, None = no weekend
    holidays = _holiday_dates(db, company_id)
    rota = rota_day_statuses(db, company_id, [employee_no], start, end)
    skipped = {"weekend": 0, "holiday": 0, "day_off": 0}
    days, d = 0, start
    while d <= end:
        iso = d.isoformat()
        if iso in holidays or rota.get((employee_no, iso)) == "holiday":
            skipped["holiday"] += 1
        elif rota.get((employee_no, iso)) == "off":
            skipped["day_off"] += 1
        elif weekend is not None and (d.weekday() + 1) % 7 in weekend and (employee_no, iso) not in rota:
            # a rota shift on a weekend day is a working day for that employee
            skipped["weekend"] += 1
        else:
            days += 1
        d += timedelta(days=1)
    return {"basis": basis, "days": days, "calendar_days": total, "skipped": {k: v for k, v in skipped.items() if v}}


def leave_window_for(db: Session, company_id: str, employee_no: str, on: date,
                     settings: dict | None = None, profiles: dict | None = None) -> tuple[str, str]:
    settings = settings if settings is not None else _leave_settings(db, company_id)
    profiles = profiles if profiles is not None else _employee_profiles(db, company_id)
    ws, we = leave_year_window(settings, (profiles.get(employee_no) or {}).get("join_date"), on)
    return ws.isoformat(), we.isoformat()


def inactive_leave_types(db: Session, company_id: str) -> set[str]:
    """Leave types switched off (Status = Inactive) in HR Settings > Leave Policy > Leave
    Types: new requests for them are refused. Existing requests and history are untouched."""
    row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "hrLeavePolicy",
            AppDataRecord.record_key == "leave-policy",
        )
        .first()
    )
    if not row:
        return set()
    try:
        data = json.loads(row.payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {
        str(entry.get("type") or "").strip()
        for entry in ((data.get("leave_types") or []) if isinstance(data, dict) else [])
        if isinstance(entry, dict) and str(entry.get("status") or "").strip().lower() == "inactive"
    } - {""}


def assert_leave_type_offered(db: Session, company_id: str, leave_type: str) -> None:
    if leave_type in inactive_leave_types(db, company_id):
        raise HTTPException(status_code=400, detail=f"{leave_type} is switched off in HR Settings > Leave Policy")


class LeaveRequestOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    leave_type: str
    start_date: str
    end_date: str
    days: int
    reason: str | None
    status: str
    created_at: str | None
    cancelled_at: str | None = None
    cancelled_by: str | None = None
    cancel_reason: str | None = None
    attachment_name: str | None = None
    # The employee's department: the HRMS list flags a pending request that overlaps
    # approved leave of someone in the same department.
    department: str | None = None
    # The employee's number (Employee.employee_no): the Rota shows approved leave on the
    # employee's days, and identifies employees by their number.
    employee_no: str | None = None


def _out(r: LeaveRequest, emp: Employee) -> LeaveRequestOut:
    return LeaveRequestOut(
        id=r.id, employee_id=r.employee_id, employee_name=emp.full_name if emp else "—",
        leave_type=r.leave_type, start_date=r.start_date, end_date=r.end_date,
        days=r.days, reason=r.reason, status=r.status,
        created_at=r.created_at.isoformat() if r.created_at else None,
        cancelled_at=r.cancelled_at.isoformat() if r.cancelled_at else None,
        cancelled_by=r.cancelled_by, cancel_reason=r.cancel_reason,
        attachment_name=r.attachment_name,
        department=emp.department if emp else None,
        employee_no=emp.employee_no if emp else None,
    )


ATTACHMENT_MAX_CHARS = 4_200_000   # a data URL of about 3 MB
_ATTACHMENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf")


def checked_attachment(data_url: str | None, name: str | None) -> tuple[str | None, str | None]:
    """(data URL, file name) of an attachment, or (None, None); 400 for a wrong type or size."""
    if not data_url:
        return None, None
    import re
    m = re.match(r"^data:([\w/+.-]+);base64,", data_url)
    if not m or m.group(1).lower() not in _ATTACHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Attach a photo (JPG, PNG) or a PDF")
    if len(data_url) > ATTACHMENT_MAX_CHARS:
        raise HTTPException(status_code=400, detail="The attachment is too large (3 MB at most)")
    return data_url, (str(name or "").strip()[:200] or ("attachment.pdf" if "pdf" in m.group(1) else "photo.jpg"))


def attachment_response(req: LeaveRequest):
    """The attachment as a file download (inline), or 404."""
    import base64
    import re

    from fastapi.responses import Response
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", req.attachment or "", re.S)
    if not m:
        raise HTTPException(status_code=404, detail="No attachment")
    safe = re.sub(r'[^A-Za-z0-9._ -]', "_", req.attachment_name or "attachment")
    return Response(content=base64.b64decode(m.group(2)), media_type=m.group(1),
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, no-store"})


class LeaveCancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


@router.get("/requests", response_model=list[LeaveRequestOut])
def list_leave_requests(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:view")),
) -> list[LeaveRequestOut]:
    query = (
        db.query(LeaveRequest, Employee)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .filter(LeaveRequest.company_id == principal.company_id)
    )
    # Same two-tier branch scoping payroll.py's list_employees()/list_runs()
    # already use — previously this endpoint (and /balance, and every
    # single-request action below) had no branch check at all, so a
    # branch-locked employee with "leave:view"/"leave:edit" could see and
    # act on every other branch's leave requests.
    resolved_branch_id = branch_id if principal.can_cross_branch("hrms") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        query = query.filter((Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None)))
    query = scope_employee_query(query, principal)  # department-scoped role: only its departments
    rows = query.order_by(LeaveRequest.created_at.desc()).all()
    return [_out(r, e) for r, e in rows]


def _assert_employee_branch_access(principal: Principal, employee: Employee) -> None:
    """Mirrors the query-level filter above for the single-request action
    endpoints (create/approve/reject/delete), which act on one specific
    employee_id rather than a list — a branch-scoped principal must not be
    able to touch a leave request for an employee outside their own
    branch(es) just because they know its id. Also the single choke point
    for department scoping (a department-scoped role can only act on its
    own departments' employees)."""
    assert_employee_in_scope(principal, employee)
    if principal.can_cross_branch("hrms"):
        return
    accessible = principal.accessible_branch_ids
    if employee.branch_id and accessible and employee.branch_id not in accessible:
        raise HTTPException(status_code=404, detail="Employee not found")


class LeaveRequestIn(BaseModel):
    employee_id: str
    leave_type: str
    start_date: date
    end_date: date
    reason: str | None = None


@router.post("/requests", response_model=LeaveRequestOut, status_code=201)
def create_leave_request(
    payload: LeaveRequestIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> LeaveRequestOut:
    if payload.leave_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unrecognized leave type")
    assert_leave_type_offered(db, principal.company_id, payload.leave_type)
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date cannot be before start date")
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.company_id == principal.company_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    _assert_employee_branch_access(principal, emp)
    # Previously only enforced at approval time — a second overlapping
    # request could sit pending indefinitely with no warning until someone
    # tried to approve it. Catching it at creation surfaces the conflict
    # immediately to whoever is filing the request. approve_leave_request()
    # keeps its own copy of this check too, since a request that didn't
    # overlap anything when created can still collide with something
    # approved afterwards.
    overlap = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.company_id == principal.company_id,
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
    # Only Annual Leave is counted against a named policy's Working/Calendar
    # basis — Sick/Emergency/etc. leave types are governed by their own flat
    # per-type caps (see _leave_type_caps), which are always calendar days.
    days = leave_request_days(db, principal.company_id, emp.employee_no, payload.leave_type, payload.start_date, payload.end_date)
    assert_leave_available(db, principal.company_id, emp, payload.leave_type, payload.start_date, days)
    req = LeaveRequest(
        company_id=principal.company_id, employee_id=emp.id, leave_type=payload.leave_type,
        start_date=payload.start_date.isoformat(), end_date=payload.end_date.isoformat(),
        days=days, reason=payload.reason, status="pending",
    )
    db.add(req)
    db.commit()
    return _out(req, emp)


def _get_request(db: Session, request_id: str, principal: Principal) -> LeaveRequest:
    req = db.query(LeaveRequest).filter(LeaveRequest.id == request_id, LeaveRequest.company_id == principal.company_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
    # Same branch check as create_leave_request() — previously a
    # branch-scoped principal could approve/reject/delete any leave
    # request in the company just by knowing (or guessing) its id, since
    # this only ever checked company_id.
    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    if emp:
        _assert_employee_branch_access(principal, emp)
    else:
        assert_employee_in_scope(principal, None)  # orphaned request: hidden from a department-scoped login
    return req


def _set_approver(req: LeaveRequest, principal: Principal) -> None:
    # Exactly one of the two approver columns is set, depending on whether a
    # real admin User or an HRMS Employee sub-user actioned this — see
    # LeaveRequest.approved_by_employee_id in models.py for why this isn't
    # a single shared FK.
    if principal.kind == "user":
        req.approved_by = principal.user.id
    else:
        req.approved_by_employee_id = principal.employee.id


@router.get("/requests/{request_id}/attachment")
def leave_request_attachment(request_id: str, db: Session = Depends(get_db),
                             principal: Principal = Depends(require_principal_permission("leave:view"))):
    """The sick note / photo the employee attached in ESS."""
    return attachment_response(_get_request(db, request_id, principal))


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestOut)
def approve_leave_request(
    request_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> LeaveRequestOut:
    req = _get_request(db, request_id, principal)
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")
    if principal.kind == "employee" and principal.employee and principal.employee.id == req.employee_id:
        raise HTTPException(status_code=403, detail="You cannot approve your own leave request")

    overlap = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.company_id == principal.company_id,
            LeaveRequest.employee_id == req.employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.id != req.id,
            LeaveRequest.start_date <= req.end_date,
            LeaveRequest.end_date >= req.start_date,
        )
        .first()
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail=f"This request overlaps an already-approved leave request ({overlap.start_date} to {overlap.end_date})",
        )

    # Previously only "Annual Leave" had any cap enforced here — Sick,
    # Emergency, Maternity, Paternity, and Hajj all had configured caps in
    # HR Settings > Leave Types that nothing ever checked, so any of them
    # could be approved for an unlimited number of days. Annual Leave keeps
    # its existing per-employee-policy entitlement (_leave_entitlement_days);
    # every other capped type uses the flat company-wide cap from
    # _leave_type_caps() instead, matching how the Leave Types settings
    # screen actually presents them (one cap per type, not per policy).
    if req.leave_type not in _UNCAPPED_LEAVE_TYPES:
        emp_for_policy = db.query(Employee).filter(Employee.id == req.employee_id).first()
        emp_no = emp_for_policy.employee_no if emp_for_policy else ""
        start_day = date.fromisoformat(req.start_date[:10])
        # Earned by the leave's start date, in the leave year it starts in.
        entitlement = leave_entitlement(db, principal.company_id, emp_no, req.leave_type, on=start_day)
        if entitlement is not None:
            used = _used_days_for_type(db, principal.company_id, req.employee_id, req.leave_type, exclude_request_id=req.id,
                                       window=leave_window_for(db, principal.company_id, emp_no, start_day))
            if used + req.days > entitlement:
                raise HTTPException(
                    status_code=409,
                    detail=f"Approving this would use {used + req.days} days against {_fmt_days(entitlement)} earned by {start_day.isoformat()} ({_fmt_days(max(0, entitlement - used))} remaining)",
                )

    req.status = "approved"
    _set_approver(req, principal)
    req.approved_at = datetime.now(timezone.utc)
    db.add(req)
    db.commit()
    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    return _out(req, emp)


@router.post("/requests/{request_id}/reject", response_model=LeaveRequestOut)
def reject_leave_request(
    request_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> LeaveRequestOut:
    req = _get_request(db, request_id, principal)
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")
    req.status = "rejected"
    _set_approver(req, principal)
    req.approved_at = datetime.now(timezone.utc)
    db.add(req)
    db.commit()
    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    return _out(req, emp)


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestOut)
def cancel_leave_request(
    request_id: str,
    payload: LeaveCancelIn | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> LeaveRequestOut:
    """Withdraw a pending request or cancel approved leave (plans changed, employee
    came back early). Kept as status "cancelled" with who/when/why, never deleted;
    balances only count approved leave, so the days return to the employee."""
    req = _get_request(db, request_id, principal)
    if req.status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail=f"Only pending or approved leave can be cancelled (this one is {req.status})")
    req.status = "cancelled"
    req.cancelled_at = datetime.now(timezone.utc)
    req.cancelled_by = principal.display_name or ("HR" if principal.kind == "user" else "HRMS user")
    req.cancel_reason = ((payload.reason if payload else None) or "").strip()[:300] or None
    db.add(req)
    db.commit()
    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    return _out(req, emp)


# ── Leave booked from the rota (HRMS > Rota, Mark As: Leave) ────────────────────
# Marking a rota day "Leave" used to be only a label: no request, no leave type, and the
# balance never went down while the monthly report counted the day as leave. It now books
# real, approved leave for that day through the same checks as any request.

class RotaLeaveIn(BaseModel):
    employee_no: str
    date: date
    leave_type: str = "Annual Leave"


def _rota_employee(db: Session, principal: Principal, employee_no: str) -> Employee:
    emp = db.query(Employee).filter(Employee.company_id == principal.company_id,
                                    Employee.employee_no == employee_no.strip()).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    _assert_employee_branch_access(principal, emp)
    return emp


@router.post("/rota-book", response_model=LeaveRequestOut)
def book_leave_from_rota(
    payload: RotaLeaveIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> LeaveRequestOut:
    """Approved leave for one rota day. The same leave already on that day is returned as
    is; any other leave there is refused (it is changed in Leave)."""
    if payload.leave_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unrecognized leave type")
    assert_leave_type_offered(db, principal.company_id, payload.leave_type)
    emp = _rota_employee(db, principal, payload.employee_no)
    if principal.kind == "employee" and principal.employee and principal.employee.id == emp.id:
        raise HTTPException(status_code=403, detail="You cannot approve your own leave")
    day = payload.date.isoformat()
    existing = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.company_id == principal.company_id, LeaveRequest.employee_id == emp.id,
                LeaveRequest.status.in_(("pending", "approved")),
                LeaveRequest.start_date <= day, LeaveRequest.end_date >= day)
        .first()
    )
    if existing:
        if existing.status == "approved" and existing.leave_type == payload.leave_type:
            return _out(existing, emp)
        raise HTTPException(
            status_code=409,
            detail=f"{emp.full_name} already has {existing.status} {existing.leave_type} on this day "
                   f"({existing.start_date} to {existing.end_date}). Change it in Leave.",
        )
    days = leave_request_days(db, principal.company_id, emp.employee_no, payload.leave_type, payload.date, payload.date)
    assert_leave_available(db, principal.company_id, emp, payload.leave_type, payload.date, days)
    req = LeaveRequest(
        company_id=principal.company_id, employee_id=emp.id, leave_type=payload.leave_type,
        start_date=day, end_date=day, days=days, reason="Booked from the rota", status="approved",
    )
    _set_approver(req, principal)
    req.approved_at = datetime.now(timezone.utc)
    db.add(req)
    db.commit()
    return _out(req, emp)


class RotaUnbookIn(BaseModel):
    employee_no: str
    date: date


@router.post("/rota-unbook")
def unbook_leave_from_rota(
    payload: RotaUnbookIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:edit")),
) -> dict:
    """The rota day is no longer leave: one-day leave on it is cancelled (kept on record, the
    day goes back to the balance). Leave spanning several days is not cut from the rota."""
    emp = _rota_employee(db, principal, payload.employee_no)
    day = payload.date.isoformat()
    req = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.company_id == principal.company_id, LeaveRequest.employee_id == emp.id,
                LeaveRequest.status.in_(("pending", "approved")),
                LeaveRequest.start_date <= day, LeaveRequest.end_date >= day)
        .first()
    )
    if not req:
        return {"ok": True, "cancelled": None}
    if req.start_date[:10] != day or req.end_date[:10] != day:
        raise HTTPException(
            status_code=409,
            detail=f"This day is part of {req.leave_type} from {req.start_date} to {req.end_date}. Change or cancel it in Leave.",
        )
    req.status = "cancelled"
    req.cancelled_at = datetime.now(timezone.utc)
    req.cancelled_by = principal.display_name or ("HR" if principal.kind == "user" else "HRMS user")
    req.cancel_reason = "Removed from the rota"
    db.add(req)
    db.commit()
    return {"ok": True, "cancelled": req.id}


@router.delete("/requests/{request_id}", status_code=204, response_model=None)
def delete_leave_request(
    request_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:delete")),
) -> None:
    req = _get_request(db, request_id, principal)
    # An approved request already reduced the employee's real entitlement
    # for the year — deleting it with no guard silently restored that
    # entitlement (no status check, no trace at all), letting the same
    # employee be approved for a second block of leave against a cap that
    # was never actually available again. Only a pending/rejected request
    # (never actually consumed entitlement) can be deleted outright; an
    # approved one must be rejected/reversed through the request's own
    # status workflow first, which at least leaves the approval and its
    # approver on record.
    if req.status == "approved":
        raise HTTPException(
            status_code=400,
            detail="An approved leave request cannot be deleted directly — cancel it instead so the change stays on record.",
        )
    db.delete(req)
    db.commit()


def _year_bounds(year: int | None) -> tuple[str, str]:
    year = year or datetime.now(timezone.utc).year
    return f"{year}-01-01", f"{year + 1}-01-01"


def _used_days_for_type(
    db: Session, company_id: str, employee_id: str, leave_type: str, exclude_request_id: str | None = None,
    *, year: int | None = None, statuses: tuple[str, ...] = ("approved",),
    window: tuple[str, str] | None = None,
) -> int:
    # Attributed entirely to the year the request STARTS in (a request
    # spanning a year boundary, e.g. 28 Dec - 5 Jan, counts fully against
    # the starting year's entitlement — a simple, documented rule, not an
    # attempt to pro-rate across the boundary). Previously had no upper
    # bound at all: an approved request starting in a FUTURE year was
    # still >= this year's Jan 1, so it silently drained the CURRENT
    # year's balance forever. `year` defaults to this year; pass the year a
    # request starts in to check it against that year's balance.
    # statuses=("pending",) gives the days on hold for requests not yet decided.
    # window=(start, end) uses that leave year instead (leave_window_for()).
    year_start, year_end = window or _year_bounds(year)
    query = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_(statuses),
        LeaveRequest.leave_type == leave_type,
        LeaveRequest.start_date >= year_start,
        LeaveRequest.start_date < year_end,
    )
    if exclude_request_id:
        query = query.filter(LeaveRequest.id != exclude_request_id)
    return sum(r.days for r in query.all())


def _used_days_by_employee_and_type(
    db: Session, company_id: str, employee_id: str | None = None,
    *, year: int | None = None, statuses: tuple[str, ...] = ("approved",),
    window: tuple[str, str] | None = None,
) -> dict[tuple[str, str], int]:
    """Approved leave days used this calendar year, grouped by (employee_id,
    leave_type) in ONE query -- exactly the same filter and year rule as
    _used_days_for_type() (attributed to the year the request STARTS in), which
    is what the balance screens used to call once per employee per leave type
    (1,200+ queries for a 200-employee company). Pass employee_id to limit it
    to one employee; statuses=("pending",) for the days still waiting on a decision."""
    year_start, year_end = window or _year_bounds(year)
    query = db.query(LeaveRequest.employee_id, LeaveRequest.leave_type, func.sum(LeaveRequest.days)).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.status.in_(statuses),
        LeaveRequest.start_date >= year_start,
        LeaveRequest.start_date < year_end,
    )
    if employee_id:
        query = query.filter(LeaveRequest.employee_id == employee_id)
    return {(emp_id, leave_type): int(total or 0) for emp_id, leave_type, total in query.group_by(LeaveRequest.employee_id, LeaveRequest.leave_type).all()}


def _annual_used_days(db: Session, company_id: str, employee_id: str, exclude_request_id: str | None = None) -> int:
    return _used_days_for_type(db, company_id, employee_id, "Annual Leave", exclude_request_id)


def leave_request_days(db: Session, company_id: str, employee_no: str, leave_type: str, start: date, end: date) -> int:
    """Days a request uses (leave_day_breakdown()). Shared by HRMS and ESS so the same dates
    always cost the same number of days."""
    return leave_day_breakdown(db, company_id, employee_no, leave_type, start, end)["days"]


def annual_leave_basis(db: Session, company_id: str, employee_no: str) -> str:
    return leave_basis(db, company_id, employee_no, "Annual Leave")


def leave_entitlement(db: Session, company_id: str, employee_no: str, leave_type: str,
                      on: date | None = None) -> float | None:
    """This employee's entitlement for a leave type in the leave year containing `on`
    (default today); None = not capped (Unpaid Leave, Work From Home, any type without a
    cap). Annual Leave: the policy's yearly days, earned by the Accrual setting from the
    join date (accrued_days()). Other capped types (Sick, Emergency, ...) are statutory or
    per-event allowances and are not accrued."""
    if leave_type in _UNCAPPED_LEAVE_TYPES:
        return None
    caps = _leave_type_caps(db, company_id)
    if leave_type == "Annual Leave":
        profiles = _employee_profiles(db, company_id)
        policy_configs = _effective_leave_policy_configs(db, company_id)
        yearly = _leave_entitlement_days(
            employee_no, {k: v["policy"] for k, v in profiles.items() if v["policy"]},
            policy_configs, default_days=caps.get("Annual Leave", 21),
        )
        settings = _leave_settings(db, company_id)
        prof = profiles.get(employee_no) or {}
        on = on or date.today()
        # Leave in a later leave year (booked in advance) may use that year's whole
        # entitlement -- what will have been earned by the end of that year -- rather than
        # only the days earned by its start date (a January holiday booked in December).
        # A Flexible policy gets the whole year's days at the start of every leave year.
        until = on
        window = leave_year_window(settings, prof.get("join_date"), on)
        flexible = bool((policy_configs.get(prof.get("policy") or "") or {}).get("flexible"))
        if flexible or window[0] > leave_year_window(settings, prof.get("join_date"), date.today())[0]:
            until = window[1] - timedelta(days=1)
        earned = accrued_days(settings, yearly, prof.get("join_date"), until)
        if prof.get("carry"):
            window = leave_year_window(settings, prof.get("join_date"), on)
            emp = db.query(Employee.id).filter(Employee.company_id == company_id, Employee.employee_no == employee_no).first()
            deadline = carry_forward_days(settings, prof, window, window[1])[2]   # this employee's own deadline
            used_before = _annual_used_before(db, company_id, emp[0], window, deadline) if emp else 0
            earned = earned + carry_forward_days(settings, prof, window, on, used_before)[0]
        return earned
    return caps.get(leave_type)


def assert_leave_available(db: Session, company_id: str, employee: Employee, leave_type: str, start: date, days: int) -> None:
    """A new request must fit in what's left for the year it starts in, counting
    pending requests as already on hold -- otherwise several pending requests could
    each pass on their own and add up to more than the entitlement."""
    if days <= 0:
        raise HTTPException(status_code=400, detail="These dates are all weekend, public holiday or rota days off: no leave days to request")
    entitlement = leave_entitlement(db, company_id, employee.employee_no, leave_type, on=start)
    if entitlement is None:
        return
    window = leave_window_for(db, company_id, employee.employee_no, start)
    taken = _used_days_for_type(db, company_id, employee.id, leave_type, window=window, statuses=("approved",))
    pending = _used_days_for_type(db, company_id, employee.id, leave_type, window=window, statuses=("pending",))
    available = max(0, entitlement - taken - pending)
    if days > available:
        on_hold = f" ({pending} more on hold in pending requests)" if pending else ""
        raise HTTPException(
            status_code=400,
            detail=f"Only {_fmt_days(available)} day(s) of {leave_type} available by {start.isoformat()}{on_hold}; this request is {days}",
        )


def _fmt_days(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


@router.get("/days")
def leave_days_preview(
    employee_id: str = Query(...), leave_type: str = Query(...), start: date = Query(...), end: date = Query(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:view")),
) -> dict:
    """How many days a request for these dates would use for this employee, and which
    days it skips -- the count the request will get (leave_day_breakdown())."""
    emp = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, emp)
    if end < start:
        raise HTTPException(status_code=400, detail="End date cannot be before start date")
    return leave_day_breakdown(db, principal.company_id, emp.employee_no, leave_type, start, end)


@router.get("/balance")
def leave_balance(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:view")),
) -> list[dict]:
    """Leave days used (approved, this calendar year) per active employee —
    Annual Leave (top-level annual_entitlement/used/remaining, kept for
    backward compatibility) plus a by_type breakdown for every other capped
    leave type. This was previously dead code — the frontend's own Leave
    Balance Summary table scraped #leave-tbody DOM rows instead of calling
    this endpoint at all, with no leave-year filter, a name-based (not
    employee_id) join that merged two same-named employees' balances
    together, and a hardcoded Sick cap of 90 with no company-setting
    override. Calling this endpoint instead means the number shown always
    matches exactly what approve_leave_request() will actually enforce."""
    employee_query = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.status == "active")
    # Same branch scoping as /requests above — previously company-wide
    # regardless of the caller's own branch assignment.
    resolved_branch_id = branch_id if principal.can_cross_branch("hrms") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        employee_query = employee_query.filter((Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None)))
    employees = scope_employee_query(employee_query, principal).all()
    balances = leave_balances(db, principal.company_id, employees)
    caps = _leave_type_caps(db, principal.company_id)
    result = []
    for e in employees:
        b = balances[e.id]
        annual = b["Annual Leave"]
        annual_entitlement, annual_used, annual_pending = annual["entitlement"], annual["used"], annual["pending"]
        by_type = {}
        for leave_type in caps:
            t = b[leave_type]
            by_type[leave_type] = {"entitlement": t["entitlement"], "used": t["used"], "pending": t["pending"],
                                   "remaining": max(0, t["entitlement"] - t["used"]),
                                   "available": max(0, t["entitlement"] - t["used"] - t["pending"]),
                                   "basis": t["basis"]}
        by_type["Annual Leave"]["yearly_entitlement"] = annual["yearly_entitlement"]
        by_type["Annual Leave"]["carried_forward"] = annual.get("carried_forward")
        # remaining = entitlement - approved; available also takes off pending requests (on hold).
        result.append({
            "employee_id": e.id, "employee_name": e.full_name,
            "annual_entitlement": annual_entitlement,
            "annual_yearly_entitlement": annual["yearly_entitlement"],
            "carried_forward": annual.get("carried_forward"),
            "leave_year": annual["leave_year"],
            "used": annual_used,
            "pending": annual_pending,
            "remaining": max(0, annual_entitlement - annual_used),
            "available": max(0, annual_entitlement - annual_used - annual_pending),
            "by_type": by_type,
        })
    return result


def leave_balances(db: Session, company_id: str, employees: list[Employee], on: date | None = None) -> dict[str, dict]:
    """{employee.id: {leave_type: {entitlement, yearly_entitlement, used, pending, basis,
    leave_year}}} for every capped type, each in the employee's own leave year containing
    `on` (default today); Annual Leave accrued by policy (leave_entitlement())."""
    on = on or date.today()
    settings = _leave_settings(db, company_id)
    profiles = _employee_profiles(db, company_id)
    configs = _effective_leave_policy_configs(db, company_id)
    caps = _leave_type_caps(db, company_id)
    policies = {k: v["policy"] for k, v in profiles.items() if v["policy"]}
    windows = {e.id: leave_window_for(db, company_id, e.employee_no, on, settings, profiles) for e in employees}
    earliest = min((w[0] for w in windows.values()), default=on.isoformat())
    rows = db.query(LeaveRequest.employee_id, LeaveRequest.leave_type, LeaveRequest.start_date, LeaveRequest.days,
                    LeaveRequest.status).filter(
        LeaveRequest.company_id == company_id, LeaveRequest.status.in_(("approved", "pending")),
        LeaveRequest.start_date >= earliest,
        LeaveRequest.employee_id.in_(list(windows)) if len(windows) <= 500 else true(),
    ).all()
    used: dict[tuple[str, str, str], int] = {}
    for emp_id, leave_type, start_date, days, status in rows:
        w = windows.get(emp_id)
        if w and w[0] <= str(start_date) < w[1]:
            used[(emp_id, leave_type, status)] = used.get((emp_id, leave_type, status), 0) + int(days or 0)
    out: dict[str, dict] = {}
    for e in employees:
        prof = profiles.get(e.employee_no) or {}
        yearly = _leave_entitlement_days(e.employee_no, policies, configs, default_days=caps.get("Annual Leave", 21))
        entry = {}
        for leave_type, cap in caps.items():
            carry = None
            if leave_type == "Annual Leave":
                # Flexible policy: the whole leave year's days at once (as leave_entitlement()).
                earn_until = on
                if (configs.get(prof.get("policy") or "") or {}).get("flexible") and e.id in windows:
                    earn_until = date.fromisoformat(windows[e.id][1]) - timedelta(days=1)
                ent, year_ent = accrued_days(settings, yearly, prof.get("join_date"), earn_until), yearly
                if prof.get("carry"):
                    window = (date.fromisoformat(windows[e.id][0]), date.fromisoformat(windows[e.id][1]))
                    deadline = carry_forward_days(settings, prof, window, window[1])[2]
                    counted, carried, deadline = carry_forward_days(
                        settings, prof, window, on, _annual_used_before(db, company_id, e.id, window, deadline))
                    if carried:
                        ent = ent + counted
                        carry = {"days": carried, "counted": counted, "use_by": deadline.isoformat() if deadline else None,
                                 "expired": counted < carried}
            else:
                ent, year_ent = cap, cap
            entry[leave_type] = {
                "carried_forward": carry,
                "entitlement": ent, "yearly_entitlement": year_ent,
                "used": used.get((e.id, leave_type, "approved"), 0), "pending": used.get((e.id, leave_type, "pending"), 0),
                "basis": leave_basis(db, company_id, e.employee_no, leave_type, settings, profiles),
                "leave_year": {"start": windows[e.id][0], "end": windows[e.id][1]},
            }
        out[e.id] = entry
    return out


def recount_leave_requests(db: Session, company_id: str, since: date) -> int:
    """Recount the days of approved and pending requests starting on/after `since` with the
    current rules (leave_day_breakdown()). Returns how many changed."""
    changed = 0
    emp_nos = {e.id: e.employee_no for e in db.query(Employee).filter(Employee.company_id == company_id).all()}
    for req in db.query(LeaveRequest).filter(LeaveRequest.company_id == company_id,
                                             LeaveRequest.status.in_(("approved", "pending")),
                                             LeaveRequest.start_date >= since.isoformat()).all():
        start, end = _parse_date(req.start_date), _parse_date(req.end_date)
        emp_no = emp_nos.get(req.employee_id)
        if not (start and end and emp_no):
            continue
        days = leave_request_days(db, company_id, emp_no, req.leave_type, start, end)
        if days != req.days and days > 0:
            req.days = days
            changed += 1
    db.commit()
    return changed
