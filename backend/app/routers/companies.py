from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import Company, User, uuid as _new_uuid
from app.schemas import CompanyOut, CompanyUpdate


router = APIRouter(prefix="/companies", tags=["companies"])

# Fields that must never be set to NULL (DB NOT NULL constraint)
_REQUIRED_FIELDS = {"name", "country"}


@router.get("/current", response_model=CompanyOut)
def current_company(current_user: User = Depends(get_current_user)):
    if not current_user.company:
        raise HTTPException(status_code=404, detail="Company not found for this user")
    return current_user.company


@router.put("/current", response_model=CompanyOut)
def update_company(
    payload: CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = current_user.company
    if not company:
        # Auto-create a company record and link it to this user
        company = Company(id=_new_uuid(), name=payload.name or "My Company")
        db.add(company)
        db.flush()
        current_user.company_id = company.id
        db.add(current_user)

    nullable_fields = [
        "trade_name", "emirate", "business_type", "business_activity",
        "legal_structure", "trade_license_no", "trade_license_issue_date",
        "trade_license_expiry", "free_zone", "address", "po_box",
        "phone", "website", "logo",
    ]
    for field in nullable_fields:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(company, field, val or None)

    # Required fields: only update when a non-empty value is provided
    if payload.name:
        company.name = payload.name
    if payload.country:
        company.country = payload.country

    # TRN: unique nullable — only update when provided and non-empty
    if payload.trn:
        company.trn = payload.trn

    db.add(company)
    db.commit()
    db.refresh(company)
    return company
