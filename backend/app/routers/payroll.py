import json
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.auth_principal import resolve_active_branch
from app.database import get_db
from app.department_scope import scope_employee_query
from app.dependencies import Principal, get_current_user, require_module, require_principal_permission
from app.models import AppDataRecord, Employee, PayrollItem, PayrollRun, User, WpsBatch
from app.schemas import EmployeeOut, PayrollGenerate, PayrollRunOut, WpsBatchOut


router = APIRouter(prefix="/payroll", tags=["payroll"], dependencies=[Depends(require_module("hrms"))])

# Fallback only — HR Settings > OT Rules (workDays x workHours, saved to the
# "hr_settings" AppDataRecord as record id "ot-rules-config") is the real
# source of truth and is read per-company in generate_payroll() below. This
# 22 x 8 = 176 default matches that screen's own on-page example formula
# (app.js's saveOtRules()/updateOtMultiplier()) exactly, so an unconfigured
# company sees the same number the settings screen itself would show.
# Previously hardcoded to 240 (30 x 8) with a comment claiming no other
# convention existed anywhere else in the codebase — the OT Rules screen
# already showed the admin a 176-hour formula on the same page, so every
# unconfigured-default company was paying OT at roughly 176/240 = 73% of the
# rate its own settings page promised.
_DEFAULT_OT_HOURS_PER_MONTH = Decimal("176")


def _ot_hours_per_month(db: Session, company_id: str) -> Decimal:
    row = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "hr_settings",
            AppDataRecord.record_key == "ot-rules-config",
        )
        .first()
    )
    if row:
        data = _payload(row)
        try:
            work_days = Decimal(str(data.get("workDays") or "22"))
            work_hours = Decimal(str(data.get("workHours") or "8"))
            total = work_days * work_hours
            if total > 0:
                return total
        except Exception:
            pass
    return _DEFAULT_OT_HOURS_PER_MONTH


def money(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _app_records(db: Session, company_id: str, collection: str) -> list[AppDataRecord]:
    return (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
        .all()
    )


def _payload(row: AppDataRecord) -> dict:
    try:
        data = json.loads(row.payload or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _save_payload(row: AppDataRecord, data: dict) -> None:
    row.payload = json.dumps(data, ensure_ascii=False, default=str)


def _name_key(value: object) -> str:
    return str(value or "").strip().lower()


def _employee_loan_deductions(loan_rows: list[AppDataRecord], employee_no: str, employee_name: str, period: str) -> Decimal:
    """Active-loan EMI due for this employee this period. The loan's stored
    balance is decremented and its status flipped to "Closed" once the
    final (true-up) installment is deducted — applied immediately to the
    row (not deferred), so if two employees share the same full name (the
    loan record's only identifying field is free-text employee name — no
    employee_no on it), the second one sees the already-reduced balance
    from the first instead of independently draining the same loan twice.
    Falls back to matching by name only when a row has no employee_id at
    all (records saved before this field existed).

    loan_rows is the company's full "employeeLoans" collection, fetched once
    by the caller before the per-employee loop — was previously re-queried
    fresh from the DB on every single call (3x per employee, every payroll
    run); the mutations below still apply to the same in-session ORM rows
    either way, so passing the pre-fetched list changes nothing about the
    result, only how many times the DB is hit to get it."""
    key = _name_key(employee_name)
    total = Decimal("0.00")
    for row in loan_rows:
        data = _payload(row)
        row_employee_id = str(data.get("employee_id") or "").strip()
        if row_employee_id:
            if row_employee_id != employee_no:
                continue
        elif _name_key(data.get("employee")) != key:
            continue
        if _name_key(data.get("status")) != "approved":
            continue
        balance = money(data.get("balance", data.get("amount")))
        if balance <= 0:
            continue
        deduct_from = str(data.get("deduct_from") or "").strip()
        if deduct_from and period < deduct_from:
            continue
        emi = money(data.get("emi"))
        # True-up the final installment so flat-division rounding never
        # leaves a permanent residual balance.
        installment = balance if balance <= emi or emi <= 0 else emi
        total += installment
        new_balance = balance - installment
        data["balance"] = f"{new_balance:.2f}"
        if new_balance <= 0:
            data["status"] = "Closed"
        _save_payload(row, data)
    return total


def _salary_advance_deductions(advance_rows: list[AppDataRecord], employee_no: str, employee_name: str, period: str) -> Decimal:
    """A salary advance is repaid in full, in the single payroll period it
    was requested against - not spread out - then marked Repaid so it's
    never deducted again (and, like loans, never double-deducted across two
    same-named employees since the write happens immediately).

    advance_rows is the company's full "salaryAdvances" collection,
    pre-fetched once by the caller — see _employee_loan_deductions."""
    key = _name_key(employee_name)
    total = Decimal("0.00")
    for row in advance_rows:
        data = _payload(row)
        row_employee_id = str(data.get("employee_id") or "").strip()
        if row_employee_id:
            if row_employee_id != employee_no:
                continue
        elif _name_key(data.get("employee")) != key:
            continue
        if _name_key(data.get("status")) != "approved":
            continue
        if str(data.get("month") or "").strip() != period:
            continue
        amount = money(data.get("amount"))
        if amount <= 0:
            continue
        total += amount
        data["status"] = "Repaid"
        _save_payload(row, data)
    return total


def _overtime_pay(overtime_rows: list[AppDataRecord], employee_no: str, employee_name: str, period: str, basic_salary: Decimal, ot_hours_per_month: Decimal) -> Decimal:
    """Sum of approved overtime for this employee in this period, at the
    multiplier the requester's own OT-type selection resolved to when they
    submitted it (see updateOtMultiplier() in app.js) - not recomputed here,
    just applied to an hourly rate derived from basic salary. Same
    employee_id-first matching as the deduction helpers, so two employees
    sharing a full name don't each get credited the other's OT hours.

    overtime_rows is the company's full "overtimeRequests" collection,
    pre-fetched once by the caller — see _employee_loan_deductions."""
    key = _name_key(employee_name)
    hourly_rate = money(basic_salary) / ot_hours_per_month
    total = Decimal("0.00")
    for row in overtime_rows:
        data = _payload(row)
        row_employee_id = str(data.get("employee_id") or "").strip()
        if row_employee_id:
            if row_employee_id != employee_no:
                continue
        elif _name_key(data.get("employee")) != key:
            continue
        if _name_key(data.get("status")) != "approved":
            continue
        date = str(data.get("date") or "")
        if date[:7] != period:
            continue
        hours = money(data.get("ot_hours") or data.get("otHours"))
        if hours <= 0:
            continue
        mult_match = re.search(r"[\d.]+", str(data.get("multiplier") or "1.25"))
        multiplier = Decimal(mult_match.group()) if mult_match else Decimal("1.25")
        total += (hours * hourly_rate * multiplier).quantize(Decimal("0.01"))
    return total


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("employees:view")),
) -> list[Employee]:
    # Active only — this feeds employee-picker dropdowns (Leave request
    # assignee, GPS location assignment, Task assignee) that have no reason
    # to offer an Inactive employee as a choice; previously returned every
    # employee regardless of status, the same "Inactive leaking through"
    # bug class already fixed for Rota/Attendance elsewhere.
    query = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.status == "active")
    # Same two-tier branch scoping as accounting.py's list_journals()/
    # reports.py's trial_balance_rows() — previously this returned the
    # entire company's roster to any branch-scoped principal with
    # employees:view, regardless of their own branch assignment.
    resolved_branch_id = branch_id if principal.can_cross_branch("hrms") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        query = query.filter((Employee.branch_id == resolved_branch_id) | (Employee.branch_id.is_(None)))
    employees = scope_employee_query(query, principal).order_by(Employee.employee_no).all()  # department-scoped role: only its departments
    # In-memory only (no db.commit() in this handler) -- a role with
    # employees:view but not employees:view_salary sees every employee-
    # picker dropdown this feeds (Leave assignee, GPS assign, Task
    # assignee) without their salary. None of those dropdowns read
    # basic_salary today, so this is a no-op for existing UI.
    if not principal.has("employees:view_salary"):
        for emp in employees:
            emp.basic_salary = Decimal("0")
    return employees


@router.get("/runs", response_model=list[PayrollRunOut])
def list_runs(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("payroll:view")),
) -> list[PayrollRun] | list[PayrollRunOut]:
    query = (
        db.query(PayrollRun)
        .options(joinedload(PayrollRun.items))
        .filter(PayrollRun.company_id == principal.company_id)
    )
    # Same pattern — previously every branch's payroll runs and net-pay
    # totals were visible to any branch-scoped principal, even though
    # generate_payroll() itself was already branch-scoped at creation time.
    resolved_branch_id = branch_id if principal.can_cross_branch("hrms") else resolve_active_branch(principal, branch_id)
    if resolved_branch_id:
        query = query.filter((PayrollRun.branch_id == resolved_branch_id) | (PayrollRun.branch_id.is_(None)))
    runs = query.order_by(PayrollRun.created_at.desc()).all()
    if not principal.is_dept_scoped:
        return runs
    # Department-scoped role: keep only its own departments' payslip lines and
    # show totals for just those lines. Built as response copies -- the ORM
    # rows are never mutated, so nothing here can be flushed back to the DB.
    in_scope_ids = {
        r[0] for r in scope_employee_query(
            db.query(Employee.id).filter(Employee.company_id == principal.company_id), principal,
        ).all()
    }
    scoped: list[PayrollRunOut] = []
    for run in runs:
        out = PayrollRunOut.model_validate(run)
        out.items = [i for i in out.items if i.employee_id in in_scope_ids]
        out.gross_total = sum((i.basic + i.allowances + i.overtime for i in out.items), Decimal("0.00"))
        out.deductions_total = sum((i.deductions for i in out.items), Decimal("0.00"))
        out.net_total = sum((i.net_pay for i in out.items), Decimal("0.00"))
        scoped.append(out)
    return scoped


@router.post("/generate", response_model=PayrollRunOut, status_code=201)
def generate_payroll(
    payload: PayrollGenerate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PayrollRun:
    # A branch-scoped run and a company-wide run (branch_id=None) for the
    # same period used to be treated as non-conflicting (different
    # branch_id), even though a company-wide run's employee_query below has
    # no branch filter at all and re-includes everyone the branch run
    # already paid — same employee gets two full salaries, and any loan/
    # advance balance the first run touched gets drained a second time.
    # A request conflicts with any existing run for the period that is
    # itself company-wide (covers everyone already), that matches the same
    # branch, or when the new request is itself company-wide (which would
    # re-cover every existing branch-scoped run).
    conflict_filters = [PayrollRun.company_id == current_user.company_id, PayrollRun.period == payload.period]
    if payload.branch_id:
        conflict_filters.append(or_(PayrollRun.branch_id.is_(None), PayrollRun.branch_id == payload.branch_id))
    existing = db.query(PayrollRun).filter(*conflict_filters).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Payroll run for {payload.period} already exists")

    employee_query = db.query(Employee).filter(Employee.company_id == current_user.company_id, Employee.status == "active")
    if payload.branch_id:
        employee_query = employee_query.filter(Employee.branch_id == payload.branch_id)
    employees = employee_query.all()
    if not employees:
        raise HTTPException(status_code=422, detail="No active employees found")

    ot_hours_per_month = _ot_hours_per_month(db, current_user.company_id)
    # Fetched once here rather than inside each per-employee helper call —
    # each collection previously got re-queried fresh from the DB for every
    # employee (3x N round trips for N employees, all returning the exact
    # same rows), see the helpers' own docstrings.
    overtime_rows = _app_records(db, current_user.company_id, "overtimeRequests")
    loan_rows = _app_records(db, current_user.company_id, "employeeLoans")
    advance_rows = _app_records(db, current_user.company_id, "salaryAdvances")
    run = PayrollRun(company_id=current_user.company_id, branch_id=payload.branch_id, period=payload.period, status="draft")
    gross_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    net_total = Decimal("0.00")
    for employee in employees:
        allowances = money(employee.housing_allowance) + money(employee.transport_allowance) + money(employee.other_allowance)
        overtime = _overtime_pay(overtime_rows, employee.employee_no, employee.full_name, payload.period, employee.basic_salary, ot_hours_per_month)
        loan_deduction = _employee_loan_deductions(loan_rows, employee.employee_no, employee.full_name, payload.period)
        advance_deduction = _salary_advance_deductions(advance_rows, employee.employee_no, employee.full_name, payload.period)
        deductions = loan_deduction + advance_deduction
        net = employee.basic_salary + allowances + overtime - deductions
        gross_total += employee.basic_salary + allowances + overtime
        deductions_total += deductions
        net_total += net
        run.items.append(
            PayrollItem(
                employee_id=employee.id,
                basic=employee.basic_salary,
                allowances=allowances,
                overtime=overtime,
                deductions=deductions,
                net_pay=net,
                wps_status="iban_missing" if not employee.iban else "ready",
            )
        )
    run.gross_total = gross_total
    run.deductions_total = deductions_total
    run.net_total = net_total
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/wps-batch", response_model=WpsBatchOut, status_code=201)
def create_wps_batch(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WpsBatch:
    run = (
        db.query(PayrollRun)
        .options(joinedload(PayrollRun.items).joinedload(PayrollItem.employee))
        .filter(PayrollRun.id == run_id, PayrollRun.company_id == current_user.company_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Payroll run not found")
    rows = ["EDR,EmployeeNo,Name,IBAN,NetPay"]
    has_error = False
    for item in run.items:
        employee = item.employee
        if not employee.iban:
            has_error = True
            # A SIF row with no IBAN isn't submittable to any bank — omit it
            # entirely (the batch below is still correctly marked "blocked"
            # overall) rather than embedding the literal string "MISSING" as
            # if it were IBAN data.
            continue
        rows.append(f"EDR,{employee.employee_no},{employee.full_name},{employee.iban},{item.net_pay:.2f}")
    batch = WpsBatch(
        company_id=current_user.company_id,
        payroll_run_id=run.id,
        batch_number=f"WPS-{run.period}-{run.id[:8]}",
        status="blocked" if has_error else "ready",
        sif_content="\n".join(rows),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch
