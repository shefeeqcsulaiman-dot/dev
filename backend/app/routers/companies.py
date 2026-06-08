from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import User
from app.schemas import CompanyOut, CompanyUpdate


router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/current", response_model=CompanyOut)
def current_company(current_user: User = Depends(get_current_user)):
    return current_user.company


@router.put("/current", response_model=CompanyOut)
def update_company(
    payload: CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = current_user.company
    simple_fields = [
        "name", "trade_name", "country", "emirate", "business_type",
        "business_activity", "legal_structure", "trade_license_no",
        "address", "po_box", "phone", "website", "logo",
    ]
    for field in simple_fields:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(company, field, val or None)
    if payload.trn is not None:
        company.trn = payload.trn or None
    db.add(company)
    db.commit()
    db.refresh(company)
    return company
