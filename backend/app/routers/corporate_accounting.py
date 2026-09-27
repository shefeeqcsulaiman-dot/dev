from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_principal import Principal, require_principal_permission
from app.database import get_db
from app.dependencies import require_module
from app.models import (
    AccrualPrepaymentRecord,
    ApprovalMatrixRecord,
    BudgetRecord,
    CashFlowForecastRecord,
    ConsolidationRecord,
    CorporateTaxRecord,
    CostCenterRecord,
    CreditControlRecord,
    FixedAssetRecord,
    MonthEndCloseRecord,
)
from app.schemas import (
    AccrualPrepaymentIn,
    ApprovalMatrixIn,
    BudgetIn,
    CashFlowForecastIn,
    ConsolidationIn,
    CreditControlIn,
    CostCenterIn,
    FixedAssetIn,
)


router = APIRouter(prefix="/corporate-accounting", tags=["corporate accounting"], dependencies=[Depends(require_module("corporate"))])


@router.get("/summary")
def summary(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> dict[str, int]:
    company_id = principal.company_id
    return {
        "corporate_tax": db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == company_id).count(),
        "fixed_assets": db.query(FixedAssetRecord).filter(FixedAssetRecord.company_id == company_id).count(),
        "accruals_prepayments": db.query(AccrualPrepaymentRecord).filter(AccrualPrepaymentRecord.company_id == company_id).count(),
        "cost_centers": db.query(CostCenterRecord).filter(CostCenterRecord.company_id == company_id).count(),
        "budgets": db.query(BudgetRecord).filter(BudgetRecord.company_id == company_id).count(),
        "cash_flow_forecasts": db.query(CashFlowForecastRecord).filter(CashFlowForecastRecord.company_id == company_id).count(),
        "credit_control": db.query(CreditControlRecord).filter(CreditControlRecord.company_id == company_id).count(),
        "month_end_close": db.query(MonthEndCloseRecord).filter(MonthEndCloseRecord.company_id == company_id).count(),
        "consolidation": db.query(ConsolidationRecord).filter(ConsolidationRecord.company_id == company_id).count(),
        "approval_matrix": db.query(ApprovalMatrixRecord).filter(ApprovalMatrixRecord.company_id == company_id).count(),
    }


@router.get("/corporate-tax", response_model=None)
def corporate_tax(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == principal.company_id).order_by(CorporateTaxRecord.created_at.desc()).all()


@router.get("/fixed-assets", response_model=None)
def fixed_assets(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(FixedAssetRecord).filter(FixedAssetRecord.company_id == principal.company_id).order_by(FixedAssetRecord.asset_code).all()


@router.post("/fixed-assets", status_code=201, response_model=None)
def create_fixed_asset(payload: FixedAssetIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> FixedAssetRecord:
    if db.query(FixedAssetRecord.id).filter(FixedAssetRecord.company_id == principal.company_id, FixedAssetRecord.asset_code == payload.asset_code).first():
        raise HTTPException(status_code=409, detail=f"Asset code '{payload.asset_code}' already exists")
    row = FixedAssetRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/accruals-prepayments", response_model=None)
def accruals_prepayments(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(AccrualPrepaymentRecord).filter(AccrualPrepaymentRecord.company_id == principal.company_id).order_by(AccrualPrepaymentRecord.created_at.desc()).all()


@router.post("/accruals-prepayments", status_code=201, response_model=None)
def create_accrual_prepayment(payload: AccrualPrepaymentIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> AccrualPrepaymentRecord:
    row = AccrualPrepaymentRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/cost-centers", response_model=None)
def cost_centers(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(CostCenterRecord).filter(CostCenterRecord.company_id == principal.company_id).order_by(CostCenterRecord.code).all()


@router.post("/cost-centers", status_code=201, response_model=None)
def create_cost_center(payload: CostCenterIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> CostCenterRecord:
    row = CostCenterRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/budgets", response_model=None)
def budgets(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(BudgetRecord).filter(BudgetRecord.company_id == principal.company_id).order_by(BudgetRecord.fiscal_year.desc()).all()


@router.post("/budgets", status_code=201, response_model=None)
def create_budget(payload: BudgetIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> BudgetRecord:
    row = BudgetRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/cash-flow", response_model=None)
def cash_flow(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(CashFlowForecastRecord).filter(CashFlowForecastRecord.company_id == principal.company_id).order_by(CashFlowForecastRecord.forecast_date).all()


@router.post("/cash-flow", status_code=201, response_model=None)
def create_cash_flow_forecast(payload: CashFlowForecastIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> CashFlowForecastRecord:
    row = CashFlowForecastRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/credit-control", response_model=None)
def credit_control(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(CreditControlRecord).filter(CreditControlRecord.company_id == principal.company_id).order_by(CreditControlRecord.customer_name).all()


@router.post("/credit-control", status_code=201, response_model=None)
def create_credit_control(payload: CreditControlIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> CreditControlRecord:
    row = CreditControlRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/month-end", response_model=None)
def month_end(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(MonthEndCloseRecord).filter(MonthEndCloseRecord.company_id == principal.company_id).order_by(MonthEndCloseRecord.period.desc()).all()


@router.get("/consolidation", response_model=None)
def consolidation(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(ConsolidationRecord).filter(ConsolidationRecord.company_id == principal.company_id).order_by(ConsolidationRecord.group_name).all()


@router.post("/consolidation", status_code=201, response_model=None)
def create_consolidation(payload: ConsolidationIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> ConsolidationRecord:
    row = ConsolidationRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/approval-matrix", response_model=None)
def approval_matrix(db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    return db.query(ApprovalMatrixRecord).filter(ApprovalMatrixRecord.company_id == principal.company_id).order_by(ApprovalMatrixRecord.module, ApprovalMatrixRecord.min_amount).all()


@router.post("/approval-matrix", status_code=201, response_model=None)
def create_approval_matrix(payload: ApprovalMatrixIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))) -> ApprovalMatrixRecord:
    row = ApprovalMatrixRecord(company_id=principal.company_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── GL posting for depreciation and accrual/prepayment release ─────────────
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func

from app.accounting_posting import create_gl_entries_from_journal
from app.models import Account, JournalEntry, JournalLine


class PostAmountIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)


def _ledger_account(db: Session, company_id: str, code: str, name: str, account_type: str) -> Account:
    account = db.query(Account).filter(Account.company_id == company_id, Account.code == code).first()
    if not account:
        account = Account(company_id=company_id, code=code, name=name, type=account_type, level=5, node_type="POSTING_LEDGER")
        db.add(account)
        db.flush()
    return account


def _post_two_line_journal(db: Session, company_id: str, source_module: str, source_id: str, description: str,
                           debit_account: Account, credit_account: Account, amount: Decimal) -> JournalEntry:
    journal = JournalEntry(
        company_id=company_id, entry_number=f"{source_module[:4].upper()}-{source_id[:8]}-{int(datetime.now(timezone.utc).timestamp())}",
        source_module=source_module, source_id=source_id, entry_date=datetime.now(timezone.utc),
        description=description, status="posted",
    )
    journal.lines = [
        JournalLine(account_id=debit_account.id, description=description, debit=amount, credit=Decimal("0.00")),
        JournalLine(account_id=credit_account.id, description=description, debit=Decimal("0.00"), credit=amount),
    ]
    db.add(journal)
    db.flush()
    create_gl_entries_from_journal(db, journal, journal.entry_number, source_module)
    return journal


@router.post("/fixed-assets/{asset_id}/depreciate", response_model=None)
def depreciate_fixed_asset(asset_id: str, payload: PostAmountIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    """Books Dr Depreciation Expense / Cr Accumulated Depreciation and rolls
    accumulated_depreciation forward (never past the asset's cost)."""
    asset = db.query(FixedAssetRecord).filter(FixedAssetRecord.id == asset_id, FixedAssetRecord.company_id == principal.company_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    amount = payload.amount
    if amount is None:
        raise HTTPException(status_code=422, detail="Enter the depreciation amount")
    amount = amount.quantize(Decimal("0.01"))
    if asset.accumulated_depreciation + amount > asset.purchase_cost:
        raise HTTPException(status_code=422, detail="Depreciation would exceed the asset's cost")
    expense = _ledger_account(db, principal.company_id, "6100", "Depreciation Expense", "indirect expense")
    accumulated = _ledger_account(db, principal.company_id, "1510", "Accumulated Depreciation", "asset")
    _post_two_line_journal(db, principal.company_id, "depreciation", asset.id, f"Depreciation - {asset.asset_name}", expense, accumulated, amount)
    asset.accumulated_depreciation = asset.accumulated_depreciation + amount
    db.commit()
    db.refresh(asset)
    return asset


def _released_amount(db: Session, company_id: str, record_id: str) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .join(Account, Account.id == JournalLine.account_id)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "accrual_release",
                JournalEntry.source_id == record_id, Account.type == "indirect expense")
        .scalar()
    )
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


@router.post("/accruals-prepayments/{record_id}/release", response_model=None)
def release_accrual_prepayment(record_id: str, payload: PostAmountIn, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("corporate:view"))):
    """Books one period's release: a prepayment moves Prepaid -> Expense, an
    accrual books Expense -> Accrued liability. Capped at the record's total."""
    rec = db.query(AccrualPrepaymentRecord).filter(AccrualPrepaymentRecord.id == record_id, AccrualPrepaymentRecord.company_id == principal.company_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    amount = (payload.amount or rec.monthly_amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Enter an amount to release")
    if _released_amount(db, principal.company_id, rec.id) + amount > rec.total_amount:
        raise HTTPException(status_code=422, detail="Release would exceed the total amount")
    expense = _ledger_account(db, principal.company_id, "6200", "Prepayment & Accrual Expense", "indirect expense")
    if "prepay" in rec.record_type.lower():
        counter = _ledger_account(db, principal.company_id, "1300", "Prepaid Expenses", "asset")
        _post_two_line_journal(db, principal.company_id, "accrual_release", rec.id, f"Prepayment release - {rec.reference}", expense, counter, amount)
    else:
        counter = _ledger_account(db, principal.company_id, "2400", "Accrued Expenses", "liability")
        _post_two_line_journal(db, principal.company_id, "accrual_release", rec.id, f"Accrual - {rec.reference}", expense, counter, amount)
    db.commit()
    return {"id": rec.id, "released": str(_released_amount(db, principal.company_id, rec.id)), "total": str(rec.total_amount)}
