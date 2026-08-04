import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models import Branch, CompanyLocation, Employee, User
from app.schemas import BranchCreate, BranchOut, BranchUpdate

router = APIRouter(prefix="/branches", tags=["branches"])


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
    current_user: User = Depends(get_current_user),
):
    company = current_user.company
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
    branch = Branch(
        company_id=current_user.company_id,
        name=payload.name.strip(),
        code=(payload.code or "").strip() or None,
        city=(payload.city or "").strip() or None,
        address=(payload.address or "").strip() or None,
        status=payload.status or "active",
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
