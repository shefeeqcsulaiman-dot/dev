import json
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import Principal, get_current_user, require_module, require_principal_permission
from app.models import AppDataRecord, Employee, PayrollItem, PayrollRun, User, WpsBatch
from app.schemas import EmployeeOut, PayrollGenerate, PayrollRunOut, WpsBatchOut


router = APIRouter(prefix="/payroll", tags=["payroll"], dependencies=[Depends(require_module("hrms"))])

# Standard UAE MOHRE convention for converting a monthly basic salary into an
# hourly rate: 30 calendar days x 8 working hours/day. No other convention
# exists anywhere else in this codebase (the OT Rules settings screen only
# ever writes multiplier config, never a resolved per-employee hourly rate).
_OT_HOURS_PER_MONTH = Decimal("240")


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


def _employee_loan_deductions(db: Session, company_id: str, employee_no: str, employee_name: str, period: str) -> Decimal:
    """Active-loan EMI due for this employee this period. The loan's stored
    balance is decremented and its status flipped to "Closed" once the
    final (true-up) installment is deducted — applied immediately to the
    row (not deferred), so if two employees share the same full name (the
    loan record's only identifying field is free-text employee name — no
    employee_no on it), the second one sees the already-reduced balance
    from the first instead of independently draining the same loan twice.
    Falls back to matching by name only when a row has no employee_id at
    all (records saved before this field existed)."""
    key = _name_key(employee_name)
    total = Decimal("0.00")
    for row in _app_records(db, company_id, "employeeLoans"):
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


def _salary_advance_deductions(db: Session, company_id: str, employee_no: str, employee_name: str, period: str) -> Decimal:
    """A salary advance is repaid in full, in the single payroll period it
    was requested against - not spread out - then marked Repaid so it's
    never deducted again (and, like loans, never double-deducted across two
    same-named employees since the write happens immediately)."""
    key = _name_key(employee_name)
    total = Decimal("0.00")
    for row in _app_records(db, company_id, "salaryAdvances"):
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


def _overtime_pay(db: Session, company_id: str, employee_no: str, employee_name: str, period: str, basic_salary: Decimal) -> Decimal:
    """Sum of approved overtime for this employee in this period, at the
    multiplier the requester's own OT-type selection resolved to when they
    submitted it (see updateOtMultiplier() in app.js) - not recomputed here,
    just applied to an hourly rate derived from basic salary. Same
    employee_id-first matching as the deduction helpers, so two employees
    sharing a full name don't each get credited the other's OT hours."""
    key = _name_key(employee_name)
    hourly_rate = money(basic_salary) / _OT_HOURS_PER_MONTH
    total = Decimal("0.00")
    for row in _app_records(db, company_id, "overtimeRequests"):
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
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("employees:view")),
) -> list[Employee]:
    return db.query(Employee).filter(Employee.company_id == principal.company_id).order_by(Employee.employee_no).all()


@router.get("/runs", response_model=list[PayrollRunOut])
def list_runs(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("payroll:view")),
) -> list[PayrollRun]:
    return (
        db.query(PayrollRun)
        .options(joinedload(PayrollRun.items))
        .filter(PayrollRun.company_id == principal.company_id)
        .order_by(PayrollRun.created_at.desc())
        .all()
    )


@router.post("/generate", response_model=PayrollRunOut, status_code=201)
def generate_payroll(
    payload: PayrollGenerate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PayrollRun:
    existing = db.query(PayrollRun).filter(
        PayrollRun.company_id == current_user.company_id,
        PayrollRun.period == payload.period,
        PayrollRun.branch_id == payload.branch_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Payroll run for {payload.period} already exists")

    employee_query = db.query(Employee).filter(Employee.company_id == current_user.company_id, Employee.status == "active")
    if payload.branch_id:
        employee_query = employee_query.filter(Employee.branch_id == payload.branch_id)
    employees = employee_query.all()
    if not employees:
        raise HTTPException(status_code=422, detail="No active employees found")

    run = PayrollRun(company_id=current_user.company_id, branch_id=payload.branch_id, period=payload.period, status="draft")
    gross_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    net_total = Decimal("0.00")
    for employee in employees:
        allowances = Decimal("0.00")
        overtime = _overtime_pay(db, current_user.company_id, employee.employee_no, employee.full_name, payload.period, employee.basic_salary)
        loan_deduction = _employee_loan_deductions(db, current_user.company_id, employee.employee_no, employee.full_name, payload.period)
        advance_deduction = _salary_advance_deductions(db, current_user.company_id, employee.employee_no, employee.full_name, payload.period)
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
