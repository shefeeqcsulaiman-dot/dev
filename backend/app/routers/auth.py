import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.company_defaults import seed_company_defaults
from app.database import get_db
from app.dependencies import Principal, assert_company_active, get_current_principal, get_current_user
from app.limiter import limiter
from app.module_catalog import ALL_MODULES
from pydantic import BaseModel

from app.models import Branch, Company, TrialRequest, User
from app.schemas import LoginRequest, RegisterRequest, Token, UserOut
from app.security import authenticate_user, create_access_token, hash_password, impersonator_id_from_token

# GCC + UK — the markets this deployment serves, same list/defaults as the
# superadmin "New Company" country selector (see CreateCompanyIn in
# superadmin.py) — kept in sync so a self-serve signup and a superadmin-
# created company land on identical currency/VAT defaults for the same
# country. Company.currency/vat_rate stay editable later in Settings either
# way; this only avoids a non-UAE signup silently starting on AED/5% VAT.
_COUNTRY_DEFAULTS = {
    "United Arab Emirates": {"currency": "AED", "vat_rate": "5.00"},
    "Saudi Arabia": {"currency": "SAR", "vat_rate": "15.00"},
    "Bahrain": {"currency": "BHD", "vat_rate": "10.00"},
    "Kuwait": {"currency": "KWD", "vat_rate": "0.00"},
    "Oman": {"currency": "OMR", "vat_rate": "5.00"},
    "Qatar": {"currency": "QAR", "vat_rate": "0.00"},
    "United Kingdom": {"currency": "GBP", "vat_rate": "20.00"},
}


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your email and password and try again")
    # Unlike the vague "incorrect email or password" above (deliberate,
    # avoids leaking account existence), a suspended company gets its own
    # clear message here — this is about company standing, not credentials,
    # and the whole point of Suspend is that affected staff should know why
    # they're blocked rather than assume they mistyped their password.
    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == user.company_id).scalar()
    assert_company_active(expires_at)
    return Token(access_token=create_access_token(user.id))


@router.post("/register", response_model=Token, status_code=201)
@limiter.limit("30/hour")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)) -> Token:
    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(status_code=409, detail="Registration failed")
    trial_expires = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    country = (payload.country or "United Arab Emirates").strip() or "United Arab Emirates"
    country_defaults = _COUNTRY_DEFAULTS.get(country)
    company = Company(
        name=payload.company_name,
        trn=payload.trn or None,
        country=country,
        subscription_expires_at=trial_expires,
        modules_enabled=json.dumps(ALL_MODULES),
    )
    if country_defaults:
        company.currency = country_defaults["currency"]
        company.vat_rate = Decimal(country_defaults["vat_rate"])
    db.add(company)
    db.flush()
    # A company with no chart of accounts can never post a single
    # transaction: post_source_transaction() requires control accounts
    # 1100/2200 to exist, and fails the posting job silently otherwise —
    # the invoice/purchase save still returns success, it just never
    # reaches the ledger, VAT report, or any financial statement. This was
    # previously dead code (seed_accounts had zero callers anywhere), so
    # every company that ever registered started financially non-functional.
    seed_company_defaults(db, company.id)
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
def me(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        impersonator_id = impersonator_id_from_token(auth.removeprefix("Bearer ").strip())
        if impersonator_id:
            impersonator = db.query(User).filter(User.id == impersonator_id).first()
            if impersonator:
                current_user.impersonated_by = {"id": impersonator.id, "email": impersonator.email}
    return current_user


class AccessibleBranchOut(BaseModel):
    id: str
    name: str


class WhoAmIOut(BaseModel):
    kind: str  # "user" | "employee" | "branch"
    id: str
    company_id: str
    display_name: str
    email: str | None = None
    is_admin: bool
    permissions: list[str] = []
    role_name: str | None = None
    branch_id: str | None = None
    # Branch Security Layer Phase 3 — populated only when this identity has
    # MORE than one accessible branch (the common single-branch/no-branch
    # case sends an empty list, so the frontend switcher only ever renders
    # when there's an actual choice to make).
    accessible_branches: list[AccessibleBranchOut] = []
    # Unlike accessible_branches above (deliberately empty in the common
    # single-branch case — nothing to switch between), this is always
    # populated whenever branch_id is set, single or multi. A Branch Login
    # session or a branch-assigned Employee otherwise has no way to tell
    # which branch's data they're looking at anywhere in the UI.
    branch_name: str | None = None
    # Only ever set for a Branch principal impersonation session
    # (superadmin.py's impersonate_company(), branch_id path) — /auth/me's
    # own impersonated_by only covers a User target, so a branch-scoped
    # session had no way to know it was being impersonated at all, and the
    # frontend's impersonation banner (driven by /auth/me) never rendered
    # for one. Employee tokens are never impersonated today, so this stays
    # None for kind="employee".
    impersonated_by: dict | None = None


@router.get("/whoami", response_model=WhoAmIOut)
def whoami(request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> WhoAmIOut:
    """Identity check that works for any of the three login paths (admin
    User, HRMS Employee sub-user, or Branch Login Phase 2's Branch entity) —
    the one call the frontend makes to decide what to show, instead of
    guessing which of /auth/me or /hr/me applies."""
    impersonated_by: dict | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        impersonator_id = impersonator_id_from_token(auth_header.removeprefix("Bearer ").strip())
        if impersonator_id:
            impersonator = db.query(User).filter(User.id == impersonator_id).first()
            if impersonator:
                impersonated_by = {"id": impersonator.id, "email": impersonator.email}
    accessible_branches: list[AccessibleBranchOut] = []
    if len(principal.accessible_branch_ids) > 1:
        rows = db.query(Branch.id, Branch.name).filter(Branch.id.in_(principal.accessible_branch_ids)).all()
        accessible_branches = [AccessibleBranchOut(id=bid, name=name) for bid, name in rows]
    branch_name: str | None = None
    if principal.branch:
        branch_name = principal.branch.name
    elif principal.branch_id:
        branch_name = db.query(Branch.name).filter(Branch.id == principal.branch_id).scalar()
    if principal.user:
        principal_id = principal.user.id
    elif principal.employee:
        principal_id = principal.employee.id
    else:
        principal_id = principal.branch.id
    return WhoAmIOut(
        kind=principal.kind,
        id=principal_id,
        company_id=principal.company_id,
        display_name=principal.display_name,
        email=principal.user.email if principal.user else None,
        is_admin=principal.is_admin,
        permissions=sorted(principal.permissions),
        role_name=principal.role_name,
        branch_id=principal.branch_id,
        accessible_branches=accessible_branches,
        branch_name=branch_name,
        impersonated_by=impersonated_by,
    )


class TrialRequestIn(BaseModel):
    full_name: str
    company_name: str
    email: str
    phone: str | None = None
    employee_count: str | None = None
    interest: str | None = None
    notes: str | None = None


@router.post("/trial-request", status_code=201)
@limiter.limit("30/hour")
def trial_request(request: Request, payload: TrialRequestIn, db: Session = Depends(get_db)) -> dict:
    record = TrialRequest(
        full_name=payload.full_name.strip(),
        company_name=payload.company_name.strip(),
        email=payload.email.strip().lower(),
        phone=payload.phone,
        employee_count=payload.employee_count,
        interest=payload.interest,
        notes=payload.notes,
        status="new",
    )
    db.add(record)
    db.commit()
    return {"ok": True}
