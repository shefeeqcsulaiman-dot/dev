from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Company, Employee, User
from app.security import hash_password

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


def _require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


class SetExpiryIn(BaseModel):
    expires_at: str | None = None


class ResetPasswordIn(BaseModel):
    user_id: str
    password: str


class CreateCompanyIn(BaseModel):
    name: str
    email: str
    password: str
    full_name: str = ""
    trn: str | None = None
    expires_at: str | None = None


@router.get("/companies")
def list_companies(db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    companies = (
        db.query(Company)
        .filter(Company.trn != "SUPERADMIN-INTERNAL")
        .order_by(Company.created_at.desc())
        .all()
    )
    result = []
    for company in companies:
        users = db.query(User).filter(User.company_id == company.id).all()
        employee_count = (
            db.query(func.count(Employee.id))
            .filter(Employee.company_id == company.id, Employee.status == "active")
            .scalar()
            or 0
        )
        sub_users = [u for u in users if u.role not in ("admin", "superadmin")]
        result.append(
            {
                "id": company.id,
                "name": company.name,
                "trn": company.trn,
                "country": company.country,
                "created_at": company.created_at.isoformat() if company.created_at else None,
                "subscription_expires_at": company.subscription_expires_at,
                "employee_count": employee_count,
                "sub_user_count": len(sub_users),
                "users": [
                    {
                        "id": u.id,
                        "email": u.email,
                        "full_name": u.full_name,
                        "role": u.role,
                        "password_plain": u.password_plain,
                        "created_at": u.created_at.isoformat() if u.created_at else None,
                    }
                    for u in users
                    if u.role != "superadmin"
                ],
            }
        )
    return result


@router.post("/companies/{company_id}/set-expiry")
def set_expiry(
    company_id: str,
    body: SetExpiryIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.subscription_expires_at = body.expires_at
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/reset-password")
def reset_password(
    company_id: str,
    body: ResetPasswordIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == body.user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(body.password)
    user.password_plain = body.password
    db.commit()
    return {"ok": True}


@router.post("/companies", status_code=201)
def create_company(
    body: CreateCompanyIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    company = Company(
        name=body.name.strip(),
        trn=body.trn or None,
        country="United Arab Emirates",
        subscription_expires_at=body.expires_at,
    )
    db.add(company)
    db.flush()
    full_name = body.full_name.strip() or (body.name.strip() + " Admin")
    user = User(
        company_id=company.id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(body.password),
        password_plain=body.password,
        role="admin",
    )
    db.add(user)
    db.commit()
    return {"ok": True, "company_id": company.id}


@router.delete("/companies/{company_id}")
def delete_company(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if db.query(User).filter(User.company_id == company_id, User.role == "superadmin").count() > 0:
        raise HTTPException(status_code=400, detail="Cannot delete superadmin company")
    db.query(User).filter(User.company_id == company_id).delete()
    db.delete(company)
    db.commit()
    return {"ok": True}
