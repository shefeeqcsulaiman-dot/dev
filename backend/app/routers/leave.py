import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

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


def _leave_entitlement_days(employee_no: str, policies: dict[str, str]) -> int:
    return _LEAVE_POLICY_DAYS.get(policies.get(employee_no, ""), 21)


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
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:view")),
) -> list[LeaveRequestOut]:
    rows = (
        db.query(LeaveRequest, Employee)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .filter(LeaveRequest.company_id == principal.company_id)
        .order_by(LeaveRequest.created_at.desc())
        .all()
    )
    return [_out(r, e) for r, e in rows]


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
    days = (payload.end_date - payload.start_date).days + 1
    req = LeaveRequest(
        company_id=principal.company_id, employee_id=emp.id, leave_type=payload.leave_type,
        start_date=payload.start_date.isoformat(), end_date=payload.end_date.isoformat(),
        days=days, reason=payload.reason, status="pending",
    )
    db.add(req)
    db.commit()
    return _out(req, emp)


def _get_request(db: Session, request_id: str, company_id: str) -> LeaveRequest:
    req = db.query(LeaveRequest).filter(LeaveRequest.id == request_id, LeaveRequest.company_id == company_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
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
    req = _get_request(db, request_id, principal.company_id)
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

    if req.leave_type == "Annual Leave":
        policies = _employee_leave_policies(db, principal.company_id)
        emp_for_policy = db.query(Employee).filter(Employee.id == req.employee_id).first()
        entitlement = _leave_entitlement_days(emp_for_policy.employee_no if emp_for_policy else "", policies)
        used = _annual_used_days(db, principal.company_id, req.employee_id, exclude_request_id=req.id)
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
    req = _get_request(db, request_id, principal.company_id)
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
    req = db.query(LeaveRequest).filter(LeaveRequest.id == request_id, LeaveRequest.company_id == principal.company_id).first()
    if req:
        db.delete(req)
        db.commit()


def _annual_used_days(db: Session, company_id: str, employee_id: str, exclude_request_id: str | None = None) -> int:
    year_start = f"{datetime.now(timezone.utc).year}-01-01"
    query = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == "approved",
        LeaveRequest.leave_type == "Annual Leave",
        LeaveRequest.start_date >= year_start,
    )
    if exclude_request_id:
        query = query.filter(LeaveRequest.id != exclude_request_id)
    return sum(r.days for r in query.all())


@router.get("/balance")
def leave_balance(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("leave:view")),
) -> list[dict]:
    """Annual-leave days used (approved, non-rejected) per employee this calendar year."""
    employees = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.status == "active").all()
    policies = _employee_leave_policies(db, principal.company_id)
    return [
        {
            "employee_id": e.id, "employee_name": e.full_name,
            "annual_entitlement": (entitlement := _leave_entitlement_days(e.employee_no, policies)),
            "used": (used := _annual_used_days(db, principal.company_id, e.id)),
            "remaining": max(0, entitlement - used),
        }
        for e in employees
    ]
