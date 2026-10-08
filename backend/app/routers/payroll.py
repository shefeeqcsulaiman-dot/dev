import calendar
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth_principal import resolve_active_branch
from app.database import get_db
from app.department_scope import scope_employee_query
from app.dependencies import Principal, require_module, require_principal_permission
from app.models import AppDataRecord, AuditLog, Company, Employee, PayrollItem, PayrollRun, WpsBatch
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
    """A collection's rows, loaded once per request (db session): payroll generate and
    approve ask for the same overtime/loan/advance/adjustment lists once per employee,
    which used to re-query and re-parse the whole list every time."""
    cache = db.info.setdefault("_payroll_app_records", {})
    key = (company_id, collection)
    if key not in cache:
        cache[key] = (
            db.query(AppDataRecord)
            .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
            .all()
        )
    return cache[key]


def _payload(row: AppDataRecord) -> dict:
    """Parsed row.payload, re-parsed only when the saved text changes (deductions
    edit the dict and save it straight back with _save_payload())."""
    cached = getattr(row, "_tf_parsed", None)
    if cached is not None and cached[0] is row.payload:
        return cached[1]
    try:
        data = json.loads(row.payload or "{}")
        data = data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        data = {}
    row._tf_parsed = (row.payload, data)
    return data


def _save_payload(row: AppDataRecord, data: dict) -> None:
    row.payload = json.dumps(data, ensure_ascii=False, default=str)


def _name_key(value: object) -> str:
    return str(value or "").strip().lower()


def _employee_loan_deductions(db: Session, company_id: str, employee_no: str, employee_name: str, period: str, apply: bool = True) -> Decimal:
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
        if apply:
            data["balance"] = f"{new_balance:.2f}"
            if new_balance <= 0:
                data["status"] = "Closed"
            _save_payload(row, data)
    return total


def _salary_advance_deductions(db: Session, company_id: str, employee_no: str, employee_name: str, period: str, apply: bool = True) -> Decimal:
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
        if apply:
            data["status"] = "Repaid"
            _save_payload(row, data)
    return total


def _overtime_pay(db: Session, company_id: str, employee_no: str, employee_name: str, period: str, basic_salary: Decimal, ot_hours_per_month: Decimal) -> Decimal:
    """Sum of approved overtime for this employee in this period, at the
    multiplier the requester's own OT-type selection resolved to when they
    submitted it (see updateOtMultiplier() in app.js) - not recomputed here,
    just applied to an hourly rate derived from basic salary. Same
    employee_id-first matching as the deduction helpers, so two employees
    sharing a full name don't each get credited the other's OT hours."""
    key = _name_key(employee_name)
    hourly_rate = money(basic_salary) / ot_hours_per_month
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


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]


def _adjustment_period(data: dict) -> str:
    explicit = str(data.get("period_ym") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", explicit):
        return explicit
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", str(data.get("period") or "").strip())
    if match and match.group(1).lower() in _MONTHS:
        return f"{match.group(2)}-{_MONTHS.index(match.group(1).lower()) + 1:02d}"
    return ""


def _payroll_adjustments(db: Session, company_id: str, employee_no: str, employee_name: str, period: str) -> tuple[Decimal, Decimal]:
    """(additions, deductions) from the payroll screen's Quick Adjustments:
    Overtime/Bonus add to pay, Deduction/Advance reduce it."""
    key = _name_key(employee_name)
    additions = Decimal("0.00")
    deductions = Decimal("0.00")
    for row in _app_records(db, company_id, "payrollAdjustments"):
        data = _payload(row)
        if _adjustment_period(data) != period:
            continue
        row_employee_id = str(data.get("employee_id") or "").strip()
        if row_employee_id:
            if row_employee_id != employee_no:
                continue
        elif _name_key(data.get("employee")) != key:
            continue
        value = money(data.get("amount"))
        if value <= 0:
            continue
        if _name_key(data.get("type")) in ("deduction", "advance"):
            deductions += value
        else:
            additions += value
    return additions, deductions


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


def _payroll_operator(principal: Principal = Depends(require_principal_permission("payroll:run_payroll", "payroll:edit"))) -> Principal:
    """Admin or a role allowed to run payroll. Runs pay the whole company/branch, so department-scoped roles can't."""
    if principal.is_dept_scoped:
        raise HTTPException(status_code=403, detail="Payroll runs cover every department, so a department-limited role can't run them")
    return principal


def _actor(principal: Principal) -> dict:
    return {"user_id": principal.user.id} if principal.user else {"employee_id": principal.employee.id if principal.employee else None}


@router.post("/generate", response_model=PayrollRunOut, status_code=201)
def generate_payroll(
    payload: PayrollGenerate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_payroll_operator),
) -> PayrollRun:
    if not principal.can_cross_branch("hrms"):
        own = resolve_active_branch(principal, payload.branch_id)
        if own:
            payload.branch_id = own
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
    if payload.period > datetime.now(timezone.utc).strftime("%Y-%m"):
        raise HTTPException(status_code=422, detail="Cannot generate payroll for a future period")
    conflict_filters = [PayrollRun.company_id == principal.company_id, PayrollRun.period == payload.period]
    if payload.branch_id:
        conflict_filters.append(or_(PayrollRun.branch_id.is_(None), PayrollRun.branch_id == payload.branch_id))
    existing = db.query(PayrollRun).filter(*conflict_filters).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Payroll run for {payload.period} already exists")

    employee_query = db.query(Employee).filter(Employee.company_id == principal.company_id, Employee.status == "active")
    if payload.branch_id:
        employee_query = employee_query.filter(Employee.branch_id == payload.branch_id)
    employees = employee_query.all()
    if not employees:
        raise HTTPException(status_code=422, detail="No active employees found")

    ot_hours_per_month = _ot_hours_per_month(db, principal.company_id)
    run = PayrollRun(company_id=principal.company_id, branch_id=payload.branch_id, period=payload.period, status="draft")
    gross_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    net_total = Decimal("0.00")
    for employee in employees:
        allowances = money(employee.housing_allowance) + money(employee.transport_allowance) + money(employee.other_allowance)
        overtime = _overtime_pay(db, principal.company_id, employee.employee_no, employee.full_name, payload.period, employee.basic_salary, ot_hours_per_month)
        # Loan/advance balances are only drained when the run is approved.
        loan_deduction = _employee_loan_deductions(db, principal.company_id, employee.employee_no, employee.full_name, payload.period, apply=False)
        advance_deduction = _salary_advance_deductions(db, principal.company_id, employee.employee_no, employee.full_name, payload.period, apply=False)
        adj_add, adj_ded = _payroll_adjustments(db, principal.company_id, employee.employee_no, employee.full_name, payload.period)
        overtime += adj_add
        deductions = loan_deduction + advance_deduction + adj_ded
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
    try:
        db.commit()
    except IntegrityError:
        # The SELECT-based conflict check above is racy — two near-
        # simultaneous requests can both pass it before either commits. The
        # unique indexes added in main.py's startup migration (see
        # uq_payroll_runs_company_period_branch/_companywide) catch what the
        # check missed; translate that into the same clean 409 rather than
        # letting it surface as an unhandled 500, and roll back so this
        # request's loan/advance deductions (already applied in-memory to
        # the AppDataRecord rows above) don't get committed as a second,
        # duplicate drain of the same balances.
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Payroll run for {payload.period} already exists")
    db.refresh(run)
    return run


def _load_run(db: Session, run_id: str, company_id: str, principal: Principal | None = None) -> PayrollRun:
    run = (
        db.query(PayrollRun)
        .options(joinedload(PayrollRun.items).joinedload(PayrollItem.employee))
        .filter(PayrollRun.id == run_id, PayrollRun.company_id == company_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Payroll run not found")
    # A branch-locked login only handles its own branch's runs.
    if principal is not None and not principal.can_cross_branch("hrms"):
        own = resolve_active_branch(principal, None)
        if own and run.branch_id != own:
            raise HTTPException(status_code=404, detail="Payroll run not found")
    return run


@router.post("/runs/{run_id}/approve", response_model=PayrollRunOut)
def approve_payroll_run(
    run_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_payroll_operator),
) -> PayrollRun:
    """Draft -> approved. Only now are loan/advance balances drained; the
    run's stored deductions were computed read-only at generation."""
    run = _load_run(db, run_id, principal.company_id, principal)
    if run.status != "draft":
        raise HTTPException(status_code=409, detail=f"Payroll run is already {run.status}")
    for item in run.items:
        emp = item.employee
        _employee_loan_deductions(db, principal.company_id, emp.employee_no, emp.full_name, run.period)
        _salary_advance_deductions(db, principal.company_id, emp.employee_no, emp.full_name, run.period)
    run.status = "approved"
    db.add(AuditLog(company_id=principal.company_id, **_actor(principal), module="payroll",
                    action="payroll_approved", record_id=run.id, detail=f"Payroll {run.period} approved"))
    db.commit()
    db.refresh(run)
    return run


@router.delete("/runs/{run_id}", status_code=204)
def delete_draft_payroll_run(
    run_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_payroll_operator),
) -> None:
    run = _load_run(db, run_id, principal.company_id, principal)
    if run.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft payroll run can be deleted")
    db.query(WpsBatch).filter(WpsBatch.payroll_run_id == run.id).delete(synchronize_session=False)
    db.delete(run)
    db.commit()


@router.get("/runs/{run_id}/sif")
def payroll_run_sif(
    run_id: str,
    mol_id: str = Query(default="MOL-0000000"),
    file_seq: str = Query(default="SIF-001"),
    pay_date: str = Query(default=""),
    db: Session = Depends(get_db),
    principal: Principal = Depends(_payroll_operator),
) -> Response:
    """CBUAE SIF built from the stored PayrollItems, never from client input."""
    run = _load_run(db, run_id, principal.company_id, principal)
    period_digits = run.period.replace("-", "")
    transfer = re.sub(r"\D", "", pay_date) or datetime.now(timezone.utc).strftime("%Y%m%d")
    days = calendar.monthrange(int(run.period[:4]), int(run.period[5:7]))[1]
    lines: list[str] = []
    total_net = Decimal("0.00")
    total_basic = Decimal("0.00")
    count = 0
    for item in run.items:
        emp = item.employee
        if not emp.iban:
            continue
        net = money(item.net_pay)
        total_net += net
        total_basic += money(item.basic)
        count += 1
        emp_id = emp.wps_id or emp.employee_no
        lines.append(
            f"SCR|{emp_id}|{emp.iban[4:7]}|{transfer}|{emp_id}|{emp.full_name}|{days}|{money(item.basic):.2f}|"
            f"{(money(item.allowances) + money(item.overtime)):.2f}|{money(item.deductions):.2f}|{net:.2f}|IBAN|{emp.iban}"
        )
    header = f"EHR|{mol_id}|{datetime.now(timezone.utc).strftime('%Y%m%d')}|{period_digits}|{file_seq}|{count}|{total_net:.2f}"
    trailer = f"ETR|{count}|{total_basic:.2f}|0.00|0.00|{total_net:.2f}"
    return Response(content="\n".join([header, *lines, trailer]), media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{file_seq}.sif"'})


@router.post("/runs/{run_id}/wps-batch", response_model=WpsBatchOut, status_code=201)
def create_wps_batch(
    run_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_payroll_operator),
) -> WpsBatch:
    run = _load_run(db, run_id, principal.company_id, principal)
    currency = db.query(Company.currency).filter(Company.id == principal.company_id).scalar()
    rows = ["EDR,EmployeeNo,Name,IBAN,NetPay"]
    has_error = False
    excluded_total = Decimal("0.00")
    excluded_names: list[str] = []
    for item in run.items:
        employee = item.employee
        if not employee.iban:
            has_error = True
            # A SIF row with no IBAN isn't submittable to any bank — omit it
            # entirely (the batch below is still correctly marked "blocked"
            # overall) rather than embedding the literal string "MISSING" as
            # if it were IBAN data. That employee's net_pay stays counted in
            # PayrollRun.net_total (the liability is still real — they still
            # need to be paid some other way), but nothing else previously
            # recorded that this batch's bank file doesn't actually cover
            # them; logged below so it's at least traceable in the audit
            # trail once the missing IBAN is later fixed.
            excluded_total += money(item.net_pay)
            excluded_names.append(f"{employee.full_name} ({employee.employee_no})")
            continue
        rows.append(f"EDR,{employee.employee_no},{employee.full_name},{employee.iban},{item.net_pay:.2f}")
    batch = WpsBatch(
        company_id=principal.company_id,
        payroll_run_id=run.id,
        batch_number=f"WPS-{run.period}-{run.id[:8]}",
        status="blocked" if has_error else "ready",
        sif_content="\n".join(rows),
    )
    db.add(batch)
    if has_error:
        db.add(AuditLog(
            company_id=principal.company_id,
            **_actor(principal),
            module="payroll",
            action="wps_batch_excluded_employees",
            record_id=run.id,
            detail=f"{currency or 'AED'} {excluded_total:.2f} for {len(excluded_names)} employee(s) missing IBAN, not in this WPS file: {'; '.join(excluded_names)}",
        ))
    db.commit()
    db.refresh(batch)
    return batch
