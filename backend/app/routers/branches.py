import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_principal import assert_company_active
from app.config import get_settings
from app.dependencies import Principal, company_allows_module, get_current_principal, get_current_user, get_db
from app.limiter import limiter
from app.models import AppDataRecord, Branch, Company, CompanyLocation, Employee, ImpersonationSession, User
from app.models import uuid as new_uuid
from app.module_catalog import BRANCH_ELIGIBLE_MODULES
from app.schemas import BranchCreate, BranchOut, BranchUpdate
from app.security import create_access_token, impersonation_revocation_info, pwd_context

router = APIRouter(prefix="/branches", tags=["branches"])
settings = get_settings()
_BRANCH_PREFIX = "branch:"
# Same constant-time-verify-on-not-found pattern as authenticate_user()
# (security.py) and hr_login() (hr_access.py) — always runs a bcrypt
# verify even when the username doesn't match, so response timing can't
# be used to enumerate valid usernames.
_DUMMY_HASH = "$2b$12$Z2HUw9SswHis7rcngsd7iOdXn/b9HafcmcwJx9D39ozeKwrSy22r."


def _branch_out(branch: Branch) -> BranchOut:
    out = BranchOut.model_validate(branch)
    out.has_password = bool(branch.password_hash)
    return out


def _apply_branch_credentials(db: Session, branch: Branch, username: str | None, password: str | None) -> None:
    """Branch Login Phase 2. Mirrors set_employee_portal_access()'s exact
    "None = don't touch, empty string = clear" convention (hr_access.py) —
    the frontend's saveBranchModal() omits these keys entirely when left
    blank on an edit, which Pydantic defaults to None here, so an edit save
    never accidentally nulls out an existing credential."""
    if username is not None:
        username = username.strip()
        if username:
            # Global check, not scoped to this company — uq_branches_username
            # enforces uniqueness platform-wide so the shared /login page can
            # resolve a branch by username alone, with no company link.
            dup = db.query(Branch).filter(Branch.username == username, Branch.id != branch.id).first()
            if dup:
                raise HTTPException(status_code=409, detail="That username is already taken by another branch on this platform — choose a different one")
        branch.username = username or None
    if password:
        if len(password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        branch.password_hash = pwd_context.hash(password)
        branch.password_changed_at = datetime.now(UTC)


def _validate_branch_modules(modules: list[str] | None, company_modules_json: str | None) -> str | None:
    """Branch Login Phase 1: a company can only enable, for a branch, a
    module that's also enabled for the company itself (and only a module
    that's actually branch-eligible at all — see BRANCH_ELIGIBLE_MODULES).
    Returns the value to persist (json.dumps'd, or None for unrestricted)."""
    if modules is None:
        return None
    for module in modules:
        if module not in BRANCH_ELIGIBLE_MODULES:
            raise HTTPException(status_code=400, detail=f"'{module}' cannot be assigned to a branch")
        if not company_allows_module(company_modules_json, module):
            raise HTTPException(status_code=400, detail=f"'{module}' is not enabled for your company — cannot enable it for a branch")
    return json.dumps(modules)


def _migrate_legacy_branches_json(db: Session, company_id: str, raw: str | None) -> None:
    """One-time, idempotent migration of the old cosmetic Company.branches
    JSON blob (Settings > Departments & Branches, no login/identity) into
    real Branch rows. Runs lazily on first GET — if this company already has
    Branch rows, it's a no-op, so this is safe to call on every list request."""
    if db.query(Branch.id).filter(Branch.company_id == company_id).first():
        return
    if not raw:
        return
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(items, list) or not items:
        return
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                continue
            db.add(Branch(company_id=company_id, name=name))
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            db.add(Branch(
                company_id=company_id,
                name=name,
                code=str(item.get("code") or "").strip() or None,
                city=str(item.get("city") or "").strip() or None,
                status=str(item.get("status") or "Active").strip() or "Active",
            ))
    db.commit()


@router.get("", response_model=list[BranchOut])
def list_branches(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # Widened from admin-only in Branch Management Phase 7 — the shared
    # bootstrap/company-info hydration path (applyCompanyToUi() -> ...
    # -> loadBranchesFromDb() in app.js) calls this unconditionally for
    # every session, including an Employee/branch login landing on
    # hrms.html. Left admin-only, this 401s for an Employee token, and the
    # app's own global 401 handler (correctly, by design — see
    # authenticatedFetch()'s comment) refuses to auto-relogin an Employee
    # session, bouncing them straight back to the login page moments after
    # a successful login. Read-only branch names aren't sensitive, so
    # widening the endpoint (not gating the frontend call) matches the fix
    # pattern used throughout this feature. Branch CRUD (create/update/
    # delete below) stays admin-only.
    company = (
        principal.user.company if principal.is_admin and principal.user
        else db.query(Company).filter(Company.id == principal.company_id).first()
    )
    if not company:
        return []
    _migrate_legacy_branches_json(db, company.id, company.branches)
    branches = (
        db.query(Branch)
        .filter(Branch.company_id == company.id)
        .order_by(Branch.name)
        .all()
    )
    return [_branch_out(b) for b in branches]


@router.post("", response_model=BranchOut, status_code=201)
def create_branch(
    payload: BranchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.company_id:
        raise HTTPException(status_code=404, detail="No company found")
    company_modules = current_user.company.modules_enabled if current_user.company else None
    modules_enabled = _validate_branch_modules(payload.modules_enabled, company_modules)
    branch = Branch(
        company_id=current_user.company_id,
        name=payload.name.strip(),
        code=(payload.code or "").strip() or None,
        city=(payload.city or "").strip() or None,
        address=(payload.address or "").strip() or None,
        country=(payload.country or "").strip() or None,
        currency=(payload.currency or "").strip() or None,
        status=payload.status or "active",
        modules_enabled=modules_enabled,
    )
    db.add(branch)
    db.flush()  # assigns branch.id, needed for the duplicate-username self-exclusion check below
    _apply_branch_credentials(db, branch, payload.username, payload.password)
    db.commit()
    db.refresh(branch)
    return _branch_out(branch)


@router.put("/{branch_id}", response_model=BranchOut)
def update_branch(
    branch_id: str,
    payload: BranchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    branch = (
        db.query(Branch)
        .filter(Branch.id == branch_id, Branch.company_id == current_user.company_id)
        .first()
    )
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if payload.name:
        branch.name = payload.name.strip()
    for field in ("code", "city", "address", "country", "currency"):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(branch, field, val.strip() or None)
    if payload.status:
        branch.status = payload.status
    # modules_enabled needs its own explicit-null-vs-omitted check ("is not
    # None" can't tell them apart, since sending null is itself the
    # meaningful "make this branch unrestricted again" value) —
    # model_fields_set tells us whether the client sent the key at all.
    if "modules_enabled" in payload.model_fields_set:
        company_modules = db.query(Company.modules_enabled).filter(Company.id == current_user.company_id).scalar()
        branch.modules_enabled = _validate_branch_modules(payload.modules_enabled, company_modules)
    _apply_branch_credentials(db, branch, payload.username, payload.password)
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return _branch_out(branch)


@router.delete("/{branch_id}")
def delete_branch(
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    branch = (
        db.query(Branch)
        .filter(Branch.id == branch_id, Branch.company_id == current_user.company_id)
        .first()
    )
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    # Unassign, don't cascade-delete, anyone/anything currently pointing at
    # this branch — losing a branch shouldn't take employees or geofence
    # points down with it.
    db.query(Employee).filter(Employee.branch_id == branch_id).update({"branch_id": None})
    db.query(CompanyLocation).filter(CompanyLocation.branch_id == branch_id).update({"branch_id": None})
    db.delete(branch)
    db.commit()
    return {"ok": True}


class BranchLoginRequest(BaseModel):
    username: str
    password: str


class BranchToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _create_branch_token(branch_id: str) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": _BRANCH_PREFIX + branch_id, "exp": exp}, settings.secret_key, algorithm="HS256")


@router.post("/login", response_model=BranchToken)
@limiter.limit("10/minute")
def branch_login(request: Request, payload: BranchLoginRequest, db: Session = Depends(get_db)) -> BranchToken:
    """Branch Login Phase 2 — the Branch entity's own shared login, a third
    identity alongside /auth/login (User admin) and /hr/login (Employee
    sub-user). Structurally mirrors hr_login() (hr_access.py) closely:
    case-insensitive global username lookup (no company selector needed,
    same as Employee.username's platform-wide-unique convention), constant-
    time dummy-hash verify on not-found, disabled-branch and suspended-
    company checks."""
    username = payload.username.strip()
    branch = db.query(Branch).filter(Branch.username.ilike(username)).first()
    if not branch:
        pwd_context.verify(payload.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail="We couldn't sign you in — check your username and password and try again")

    if not branch.password_hash or not pwd_context.verify(payload.password, branch.password_hash):
        raise HTTPException(status_code=401, detail="We couldn't sign you in — check your username and password and try again")
    if branch.status != "Active":
        raise HTTPException(status_code=403, detail="This branch is disabled")

    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == branch.company_id).scalar()
    assert_company_active(expires_at)

    now = datetime.now(UTC)
    branch.last_login = now
    branch.last_activity = now
    db.add(branch)
    db.commit()

    return BranchToken(access_token=_create_branch_token(branch.id))


@router.post("/{branch_id}/impersonate", response_model=BranchToken)
@limiter.limit("20/minute")
def impersonate_branch(
    request: Request,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BranchToken:
    """Lets a company's own User (Main Dashboard) drop straight into a
    branch's own dashboard view -- the same branch-scoped token
    branch_login() issues -- without needing that branch's separate
    username/password. Mirrors superadmin.py's impersonate_company(body.
    branch_id) mechanism (create_access_token(..., impersonated_by=...),
    revocable through the same /superadmin/end-impersonation flow, which
    only checks for an "imp" claim and isn't superadmin-gated itself) rather
    than duplicating a parallel token/session scheme. Reuses
    ImpersonationSession for tracking despite its "superadmin_id" column
    name -- structurally it's just "impersonator user id", and reusing it
    means a real superadmin can still see and force-end this session from
    the same /superadmin/impersonation-sessions tooling."""
    branch = (
        db.query(Branch)
        .filter(Branch.id == branch_id, Branch.company_id == current_user.company_id)
        .first()
    )
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if branch.status != "Active":
        raise HTTPException(status_code=403, detail="This branch is disabled")

    token = create_access_token(_BRANCH_PREFIX + branch.id, impersonated_by=current_user.id)
    revocation = impersonation_revocation_info(token)
    if revocation:
        jti, _ttl = revocation
        db.add(ImpersonationSession(
            superadmin_id=current_user.id,
            target_branch_id=branch.id,
            company_id=current_user.company_id,
            token_jti=jti,
        ))
    db.add(AppDataRecord(
        id=new_uuid(),
        company_id=current_user.company_id,
        collection="audit",
        record_key=None,
        payload=json.dumps({
            "time": datetime.now(UTC).strftime("%d/%m/%Y, %H:%M"),
            "user": f"{current_user.full_name} ({current_user.email})",
            "action": "Impersonation started",
            "record": f"as branch {branch.name}",
            "result": "Started",
        }),
    ))
    db.commit()
    return BranchToken(access_token=token)
