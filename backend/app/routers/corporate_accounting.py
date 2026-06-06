from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
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
    User,
)


router = APIRouter(prefix="/corporate-accounting", tags=["corporate accounting"])


@router.get("/summary")
def summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, int]:
    company_id = current_user.company_id
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
def corporate_tax(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == current_user.company_id).order_by(CorporateTaxRecord.created_at.desc()).all()


@router.get("/fixed-assets", response_model=None)
def fixed_assets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(FixedAssetRecord).filter(FixedAssetRecord.company_id == current_user.company_id).order_by(FixedAssetRecord.asset_code).all()


@router.get("/accruals-prepayments", response_model=None)
def accruals_prepayments(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(AccrualPrepaymentRecord).filter(AccrualPrepaymentRecord.company_id == current_user.company_id).order_by(AccrualPrepaymentRecord.created_at.desc()).all()


@router.get("/cost-centers", response_model=None)
def cost_centers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(CostCenterRecord).filter(CostCenterRecord.company_id == current_user.company_id).order_by(CostCenterRecord.code).all()


@router.get("/budgets", response_model=None)
def budgets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(BudgetRecord).filter(BudgetRecord.company_id == current_user.company_id).order_by(BudgetRecord.fiscal_year.desc()).all()


@router.get("/cash-flow", response_model=None)
def cash_flow(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(CashFlowForecastRecord).filter(CashFlowForecastRecord.company_id == current_user.company_id).order_by(CashFlowForecastRecord.forecast_date).all()


@router.get("/credit-control", response_model=None)
def credit_control(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(CreditControlRecord).filter(CreditControlRecord.company_id == current_user.company_id).order_by(CreditControlRecord.customer_name).all()


@router.get("/month-end", response_model=None)
def month_end(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(MonthEndCloseRecord).filter(MonthEndCloseRecord.company_id == current_user.company_id).order_by(MonthEndCloseRecord.period.desc()).all()


@router.get("/consolidation", response_model=None)
def consolidation(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ConsolidationRecord).filter(ConsolidationRecord.company_id == current_user.company_id).order_by(ConsolidationRecord.group_name).all()


@router.get("/approval-matrix", response_model=None)
def approval_matrix(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ApprovalMatrixRecord).filter(ApprovalMatrixRecord.company_id == current_user.company_id).order_by(ApprovalMatrixRecord.module, ApprovalMatrixRecord.min_amount).all()
