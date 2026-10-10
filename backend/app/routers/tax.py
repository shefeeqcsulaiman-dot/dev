from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.cache as cache
from app.auth_principal import Principal, require_principal_permission
from app.database import get_db
from app.dependencies import get_current_user, require_module
from app.models import CorporateTaxReturn, TaxCode, TaxLine, User, VatReturn
from app.schemas import CorporateTaxReturnCreate, CorporateTaxReturnOut, TaxCodeIn, TaxCodeOut, TaxLineOut, VatReturnCreate, VatReturnOut


router = APIRouter(prefix="/tax", tags=["tax"])


@router.get("/codes", response_model=list[TaxCodeOut], dependencies=[Depends(require_module("accounting"))])
def list_tax_codes(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[TaxCode]:
    return db.query(TaxCode).filter(TaxCode.company_id == principal.company_id).order_by(TaxCode.code).all()


@router.post("/codes", response_model=TaxCodeOut, status_code=201, dependencies=[Depends(require_module("accounting"))])
def create_tax_code(
    payload: TaxCodeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaxCode:
    existing = db.query(TaxCode).filter(
        TaxCode.company_id == current_user.company_id,
        TaxCode.code == payload.code,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Tax code '{payload.code}' already exists")

    code = TaxCode(company_id=current_user.company_id, **payload.model_dump())
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


@router.get("/lines", response_model=list[TaxLineOut], dependencies=[Depends(require_module("accounting"))])
def list_tax_lines(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[TaxLine]:
    return db.query(TaxLine).filter(TaxLine.company_id == principal.company_id).order_by(TaxLine.created_at.desc()).all()


@router.get("/vat-return", dependencies=[Depends(require_module("accounting"))])
def vat_return(
    period: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("accounting:view")),
) -> dict[str, str]:
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    cache_key = f"vat_return:{principal.company_id}:{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    output_vat = (
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == principal.company_id, TaxLine.period == period, TaxLine.direction == "output")
        .scalar()
    )
    input_vat = (
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == principal.company_id, TaxLine.period == period, TaxLine.direction == "input")
        .scalar()
    )
    net = Decimal(str(output_vat)) - Decimal(str(input_vat))
    result = {
        "period": period,
        "output_vat": f"{Decimal(str(output_vat)):.2f}",
        "input_vat": f"{Decimal(str(input_vat)):.2f}",
        "net_vat_payable": f"{net:.2f}",
    }
    cache.set_in_group(cache_key, result, 300, cache.report_group(principal.company_id))
    return result


@router.get("/vat-returns", response_model=list[VatReturnOut], dependencies=[Depends(require_module("accounting"))])
def list_vat_returns(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("accounting:view"))) -> list[VatReturn]:
    return db.query(VatReturn).filter(VatReturn.company_id == principal.company_id).order_by(VatReturn.period.desc()).all()


@router.post("/vat-returns", response_model=VatReturnOut, status_code=201, dependencies=[Depends(require_module("accounting"))])
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
    if row and row.filing_status == "filed":
        raise HTTPException(
            status_code=409,
            detail=f"VAT return for period '{payload.period}' is already filed (FTA ref {row.fta_reference_no or 'n/a'}) and cannot be re-submitted. File an amendment through the FTA portal instead.",
        )
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
    if payload.filing_status == "filed":
        row.filed_date = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


@router.get("/corporate-tax-returns", response_model=list[CorporateTaxReturnOut], dependencies=[Depends(require_module("corporate"))])
def list_corporate_tax_returns(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> list[CorporateTaxReturn]:
    return db.query(CorporateTaxReturn).filter(CorporateTaxReturn.company_id == principal.company_id).order_by(CorporateTaxReturn.tax_period.desc()).all()


@router.post("/corporate-tax-returns", response_model=CorporateTaxReturnOut, status_code=201, dependencies=[Depends(require_module("corporate"))])
def create_corporate_tax_return(
    payload: CorporateTaxReturnCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CorporateTaxReturn:
    taxable_income = payload.accounting_profit + payload.non_deductible_expenses - payload.exempt_income - payload.tax_loss_adjustment
    taxable_income = max(Decimal("0.00"), taxable_income)
    # UAE Small Business Relief: the first AED 375,000 of taxable income is
    # taxed at 0%, only the excess is taxed at the standard rate. Matches the
    # frontend's own calcCorporateTax() worksheet.
    SMALL_BUSINESS_RELIEF_THRESHOLD = Decimal("375000.00")
    taxable_above_threshold = max(Decimal("0.00"), taxable_income - SMALL_BUSINESS_RELIEF_THRESHOLD)
    tax_payable = (taxable_above_threshold * (payload.tax_rate / Decimal("100"))).quantize(Decimal("0.01"))
    row = (
        db.query(CorporateTaxReturn)
        .filter(CorporateTaxReturn.company_id == current_user.company_id, CorporateTaxReturn.tax_period == payload.tax_period)
        .first()
    )
    if row and row.filing_status == "filed":
        raise HTTPException(
            status_code=409,
            detail=f"Corporate tax return for period '{payload.tax_period}' is already filed (ref {row.reference_no or 'n/a'}) and cannot be re-submitted. File an amendment through the FTA portal instead.",
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
    if payload.filing_status == "filed":
        row.filed_date = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
