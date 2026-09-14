import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_principal import resolve_active_branch
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


def _out(r: LeaveRequest, emp: Employee) -> LeaveRequestOut:
    return LeaveRequestOut(
        id=r.id, employee_id=r.employee_id, employee_name=emp.full_name if emp else "—",
        leave_type=r.leave_type, start_date=r.start_date, end_date=r.end_date,
        days=r.days, reason=r.reason, status=r.status,
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


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
    rows = query.order_by(LeaveRequest.created_at.desc()).all()
    return [_out(r, e) for r, e in rows]


def _assert_employee_branch_access(principal: Principal, employee: Employee) -> None:
    """Mirrors the query-level filter above for the single-request action
    endpoints (create/approve/reject/delete), which act on one specific
    employee_id rather than a list — a branch-scoped principal must not be
    able to touch a leave request for an employee outside their own
    branch(es) just because they know its id."""
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
    days = (payload.end_date - payload.start_date).days + 1
    # Only Annual Leave is counted against a named policy's Working/Calendar
    # basis — Sick/Emergency/etc. leave types are governed by their own flat
    # per-type caps (see _leave_type_caps), which are always calendar days.
    if payload.leave_type == "Annual Leave":
        policy_name = _employee_leave_policies(db, principal.company_id).get(emp.employee_no, "")
        configs = _effective_leave_policy_configs(db, principal.company_id)
        basis = configs.get(policy_name, {}).get("basis", _DEFAULT_LEAVE_POLICY_BASIS.get(policy_name, "Calendar"))
        if basis == "Working":
            days = _working_days_count(payload.start_date, payload.end_date)
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
        type_caps = _leave_type_caps(db, principal.company_id)
        if req.leave_type == "Annual Leave":
            policies = _employee_leave_policies(db, principal.company_id)
            emp_for_policy = db.query(Employee).filter(Employee.id == req.employee_id).first()
            configs = _effective_leave_policy_configs(db, principal.company_id)
            entitlement = _leave_entitlement_days(
                emp_for_policy.employee_no if emp_for_policy else "", policies, configs,
                default_days=type_caps.get("Annual Leave", 21),
            )
        else:
            entitlement = type_caps.get(req.leave_type)
        if entitlement is not None:
            used = _used_days_for_type(db, principal.company_id, req.employee_id, req.leave_type, exclude_request_id=req.id)
            if used + req.days > entitlement:
                raise HTTPException(
                    status_code=409,
                    detail=f"Approving this would use {used + req.days} days against a {entitlement}-day entitlement ({entitlement - used} remaining)",
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
            detail="An approved leave request cannot be deleted directly — reject or reverse it first so the entitlement change stays on record.",
        )
    db.delete(req)
    db.commit()


def _used_days_for_type(db: Session, company_id: str, employee_id: str, leave_type: str, exclude_request_id: str | None = None) -> int:
    # Attributed entirely to the year the request STARTS in (a request
    # spanning a year boundary, e.g. 28 Dec - 5 Jan, counts fully against
    # the starting year's entitlement — a simple, documented rule, not an
    # attempt to pro-rate across the boundary). Previously had no upper
    # bound at all: an approved request starting in a FUTURE year was
    # still >= this year's Jan 1, so it silently drained the CURRENT
    # year's balance forever.
    year = datetime.now(timezone.utc).year
    year_start = f"{year}-01-01"
    year_end = f"{year + 1}-01-01"
    query = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == "approved",
        LeaveRequest.leave_type == leave_type,
        LeaveRequest.start_date >= year_start,
        LeaveRequest.start_date < year_end,
    )
    if exclude_request_id:
        query = query.filter(LeaveRequest.id != exclude_request_id)
    return sum(r.days for r in query.all())


def _annual_used_days(db: Session, company_id: str, employee_id: str, exclude_request_id: str | None = None) -> int:
    return _used_days_for_type(db, company_id, employee_id, "Annual Leave", exclude_request_id)


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
    employees = employee_query.all()
    policies = _employee_leave_policies(db, principal.company_id)
    configs = _effective_leave_policy_configs(db, principal.company_id)
    caps = _leave_type_caps(db, principal.company_id)
    result = []
    for e in employees:
        annual_entitlement = _leave_entitlement_days(e.employee_no, policies, configs, default_days=caps.get("Annual Leave", 21))
        annual_used = _used_days_for_type(db, principal.company_id, e.id, "Annual Leave")
        by_type = {}
        for leave_type, cap in caps.items():
            used = annual_used if leave_type == "Annual Leave" else _used_days_for_type(db, principal.company_id, e.id, leave_type)
            by_type[leave_type] = {"entitlement": cap, "used": used, "remaining": max(0, cap - used)}
        result.append({
            "employee_id": e.id, "employee_name": e.full_name,
            "annual_entitlement": annual_entitlement,
            "used": annual_used,
            "remaining": max(0, annual_entitlement - annual_used),
            "by_type": by_type,
        })
    return result
