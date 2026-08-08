import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import Principal, company_allows_module, get_current_principal, get_current_user, get_db
from app.models import Branch, Company, CompanyLocation, Employee, User
from app.module_catalog import BRANCH_ELIGIBLE_MODULES
from app.schemas import BranchCreate, BranchOut, BranchUpdate

router = APIRouter(prefix="/branches", tags=["branches"])


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
    return (
        db.query(Branch)
        .filter(Branch.company_id == company.id)
        .order_by(Branch.name)
        .all()
    )


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
        status=payload.status or "active",
        modules_enabled=modules_enabled,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


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
    for field in ("code", "city", "address"):
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
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


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
