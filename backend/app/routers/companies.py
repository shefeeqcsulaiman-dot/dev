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
    if payload.name is not None:
        company.name = payload.name
    if payload.trn is not None:
        company.trn = payload.trn or None
    if payload.country is not None:
        company.country = payload.country
    if payload.logo is not None:
        company.logo = payload.logo or None
    db.add(company)
    db.commit()
    db.refresh(company)
    return company
