from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import CorporateTaxReturn, TaxCode, TaxLine, User, VatReturn
from app.schemas import CorporateTaxReturnCreate, CorporateTaxReturnOut, TaxCodeIn, TaxCodeOut, TaxLineOut, VatReturnCreate, VatReturnOut


router = APIRouter(prefix="/tax", tags=["tax"])


@router.get("/codes", response_model=list[TaxCodeOut])
def list_tax_codes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[TaxCode]:
    return db.query(TaxCode).filter(TaxCode.company_id == current_user.company_id).order_by(TaxCode.code).all()


@router.post("/codes", response_model=TaxCodeOut, status_code=201)
def create_tax_code(
    payload: TaxCodeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaxCode:
    code = TaxCode(company_id=current_user.company_id, **payload.model_dump())
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


@router.get("/lines", response_model=list[TaxLineOut])
def list_tax_lines(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[TaxLine]:
    return db.query(TaxLine).filter(TaxLine.company_id == current_user.company_id).order_by(TaxLine.created_at.desc()).all()


@router.get("/vat-return")
def vat_return(
    period: str = "2024-06",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    output_vat = (
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == period, TaxLine.direction == "output")
        .scalar()
    )
    input_vat = (
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == period, TaxLine.direction == "input")
        .scalar()
    )
    net = Decimal(str(output_vat)) - Decimal(str(input_vat))
    return {
        "period": period,
        "output_vat": f"{Decimal(str(output_vat)):.2f}",
        "input_vat": f"{Decimal(str(input_vat)):.2f}",
        "net_vat_payable": f"{net:.2f}",
    }


@router.get("/vat-returns", response_model=list[VatReturnOut])
def list_vat_returns(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[VatReturn]:
    return db.query(VatReturn).filter(VatReturn.company_id == current_user.company_id).order_by(VatReturn.period.desc()).all()


@router.post("/vat-returns", response_model=VatReturnOut, status_code=201)
def create_vat_return(
    payload: VatReturnCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VatReturn:
    output_taxable = Decimal(
        str(
            db.query(func.coalesce(func.sum(TaxLine.taxable_amount), 0))
            .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == payload.period, TaxLine.direction == "output")
            .scalar()
            or 0
        )
    )
    output_vat = Decimal(
        str(
            db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
            .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == payload.period, TaxLine.direction == "output")
            .scalar()
            or 0
        )
    )
    input_taxable = Decimal(
        str(
            db.query(func.coalesce(func.sum(TaxLine.taxable_amount), 0))
            .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == payload.period, TaxLine.direction == "input")
            .scalar()
            or 0
        )
    )
    input_vat = Decimal(
        str(
            db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
            .filter(TaxLine.company_id == current_user.company_id, TaxLine.period == payload.period, TaxLine.direction == "input")
            .scalar()
            or 0
        )
    )
    row = db.query(VatReturn).filter(VatReturn.company_id == current_user.company_id, VatReturn.period == payload.period).first()
    if not row:
        row = VatReturn(company_id=current_user.company_id, period=payload.period)
        db.add(row)
    row.sales_taxable_amount = output_taxable
    row.output_vat = output_vat
    row.purchase_taxable_amount = input_taxable
    row.input_vat = input_vat
    row.adjustments = payload.adjustments
    row.net_vat = output_vat - input_vat + payload.adjustments
    row.filing_status = payload.filing_status
    row.fta_reference_no = payload.fta_reference_no
    row.attachment = payload.attachment
    db.commit()
    db.refresh(row)
    return row


@router.get("/corporate-tax-returns", response_model=list[CorporateTaxReturnOut])
def list_corporate_tax_returns(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[CorporateTaxReturn]:
    return db.query(CorporateTaxReturn).filter(CorporateTaxReturn.company_id == current_user.company_id).order_by(CorporateTaxReturn.tax_period.desc()).all()


@router.post("/corporate-tax-returns", response_model=CorporateTaxReturnOut, status_code=201)
def create_corporate_tax_return(
    payload: CorporateTaxReturnCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CorporateTaxReturn:
    taxable_income = payload.accounting_profit + payload.non_deductible_expenses - payload.exempt_income - payload.tax_loss_adjustment
    taxable_income = max(Decimal("0.00"), taxable_income)
    tax_payable = (taxable_income * (payload.tax_rate / Decimal("100"))).quantize(Decimal("0.01"))
    row = (
        db.query(CorporateTaxReturn)
        .filter(CorporateTaxReturn.company_id == current_user.company_id, CorporateTaxReturn.tax_period == payload.tax_period)
        .first()
    )
    if not row:
        row = CorporateTaxReturn(company_id=current_user.company_id, tax_period=payload.tax_period)
        db.add(row)
    row.accounting_profit = payload.accounting_profit
    row.non_deductible_expenses = payload.non_deductible_expenses
    row.exempt_income = payload.exempt_income
    row.tax_loss_adjustment = payload.tax_loss_adjustment
    row.taxable_income = taxable_income
    row.tax_rate = payload.tax_rate
    row.corporate_tax_payable = tax_payable
    row.filing_status = payload.filing_status
    row.reference_no = payload.reference_no
    row.attachment = payload.attachment
    db.commit()
    db.refresh(row)
    return row
