import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from pydantic import BaseModel

from app.models import Company, TrialRequest, User
from app.schemas import LoginRequest, RegisterRequest, Token, UserOut
from app.security import authenticate_user, create_access_token, hash_password

_ALL_MODULES = [
    "sales", "quotations", "pos", "purchase", "inventory", "expense",
    "bank", "accounting", "corporate", "reports", "hrms", "ess",
    "notifications", "expert", "exception", "ai",
]


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    return Token(access_token=create_access_token(user.id))


@router.post("/register", response_model=Token, status_code=201)
@limiter.limit("5/hour")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)) -> Token:
    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(status_code=409, detail="Registration failed")
    trial_expires = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    company = Company(
        name=payload.company_name,
        trn=payload.trn or None,
        country="United Arab Emirates",
        subscription_expires_at=trial_expires,
        modules_enabled=json.dumps(_ALL_MODULES),
    )
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


class TrialRequestIn(BaseModel):
    full_name: str
    company_name: str
    email: str
    phone: str | None = None
    interest: str | None = None
    notes: str | None = None


@router.post("/trial-request", status_code=201)
@limiter.limit("5/hour")
def trial_request(request: Request, payload: TrialRequestIn, db: Session = Depends(get_db)) -> dict:
    record = TrialRequest(
        full_name=payload.full_name.strip(),
        company_name=payload.company_name.strip(),
        email=payload.email.strip().lower(),
        phone=payload.phone,
        interest=payload.interest,
        notes=payload.notes,
        status="new",
    )
    db.add(record)
    db.commit()
    return {"ok": True}
