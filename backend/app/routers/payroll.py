from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Employee, PayrollItem, PayrollRun, User, WpsBatch
from app.schemas import EmployeeOut, PayrollGenerate, PayrollRunOut, WpsBatchOut


router = APIRouter(prefix="/payroll", tags=["payroll"])


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[Employee]:
    return db.query(Employee).filter(Employee.company_id == current_user.company_id).order_by(Employee.employee_no).all()


@router.get("/runs", response_model=list[PayrollRunOut])
def list_runs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[PayrollRun]:
    return (
        db.query(PayrollRun)
        .options(joinedload(PayrollRun.items))
        .filter(PayrollRun.company_id == current_user.company_id)
        .order_by(PayrollRun.created_at.desc())
        .all()
    )


@router.post("/generate", response_model=PayrollRunOut, status_code=201)
def generate_payroll(
    payload: PayrollGenerate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PayrollRun:
    employees = db.query(Employee).filter(Employee.company_id == current_user.company_id, Employee.status == "active").all()
    if not employees:
        raise HTTPException(status_code=422, detail="No active employees found")

    run = PayrollRun(company_id=current_user.company_id, period=payload.period, status="draft")
    gross_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    net_total = Decimal("0.00")
    for employee in employees:
        allowances = employee.basic_salary * Decimal("0.15")
        overtime = Decimal("350.00") if employee.department in {"Sales", "Operations"} else Decimal("0.00")
        deductions = Decimal("300.00") if not employee.iban else Decimal("0.00")
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
        rows.append(f"EDR,{employee.employee_no},{employee.full_name},{employee.iban or 'MISSING'},{item.net_pay:.2f}")
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
