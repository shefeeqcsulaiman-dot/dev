import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import AppDataRecord, ClientError, Company, Employee, User
from app.security import hash_password, user_id_from_token

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


ALL_MODULES = [
    "sales", "quotations", "pos", "purchase", "inventory", "expense",
    "bank", "accounting", "corporate", "reports", "hrms", "ess",
    "notifications", "expert", "exception", "ai",
]


class ModulesIn(BaseModel):
    modules: list[str]


class CreateCompanyIn(BaseModel):
    name: str
    email: str
    password: str
    full_name: str = ""
    trn: str | None = None
    expires_at: str | None = None
    modules: list[str] | None = None


class UpdateCompanyIn(BaseModel):
    name: str | None = None
    trn: str | None = None
    country: str | None = None


class AddUserIn(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "user"


class UpdateUserIn(BaseModel):
    email: str | None = None
    full_name: str | None = None
    role: str | None = None


@router.get("/companies")
def list_companies(db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    companies = (
        db.query(Company)
        .filter(or_(Company.trn.is_(None), Company.trn != "SUPERADMIN-INTERNAL"))
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
        try:
            mods = json.loads(company.modules_enabled) if company.modules_enabled else ALL_MODULES
        except Exception:
            mods = ALL_MODULES
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
                "modules_enabled": mods,
                "users": [
                    {
                        "id": u.id,
                        "email": u.email,
                        "full_name": u.full_name,
                        "role": u.role,
                        "is_active": getattr(u, "is_active", True),
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
    mods = body.modules if body.modules is not None else ALL_MODULES
    company = Company(
        name=body.name.strip(),
        trn=body.trn or None,
        country="United Arab Emirates",
        subscription_expires_at=body.expires_at,
        modules_enabled=json.dumps(mods),
    )
    db.add(company)
    db.flush()
    full_name = body.full_name.strip() or (body.name.strip() + " Admin")
    user = User(
        company_id=company.id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(body.password),
        role="admin",
    )
    db.add(user)
    db.commit()
    return {"ok": True, "company_id": company.id}


@router.patch("/companies/{company_id}")
def update_company(
    company_id: str,
    body: UpdateCompanyIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if body.name is not None:
        company.name = body.name.strip()
    if body.trn is not None:
        company.trn = body.trn.strip() or None
    if body.country is not None:
        company.country = body.country.strip()
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/users", status_code=201)
def add_user(
    company_id: str,
    body: AddUserIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    role = body.role if body.role in ("admin", "user", "accountant", "viewer") else "user"
    user = User(
        company_id=company_id,
        email=email,
        full_name=body.full_name.strip() or email,
        password_hash=hash_password(body.password),
        role=role,
    )
    db.add(user)
    db.commit()
    return {"ok": True, "user_id": user.id}


@router.patch("/companies/{company_id}/users/{user_id}")
def update_user(
    company_id: str,
    user_id: str,
    body: UpdateUserIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.email is not None:
        new_email = body.email.strip().lower()
        existing = db.query(User).filter(User.email == new_email, User.id != user_id).first()
        if existing:
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = new_email
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.role is not None and body.role in ("admin", "user", "accountant", "viewer"):
        user.role = body.role
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/users/{user_id}/toggle-status")
def toggle_user_status(
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot disable superadmin")
    user.is_active = not getattr(user, "is_active", True)
    db.commit()
    return {"ok": True, "is_active": user.is_active}


@router.delete("/companies/{company_id}/users/{user_id}")
def delete_user(
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot delete superadmin user")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/companies/{company_id}/modules")
def get_company_modules(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        mods = json.loads(company.modules_enabled) if company.modules_enabled else ALL_MODULES
    except Exception:
        mods = ALL_MODULES
    return {"modules": mods, "all_modules": ALL_MODULES}


@router.put("/companies/{company_id}/modules")
def set_company_modules(
    company_id: str,
    body: ModulesIn,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    valid = [m for m in body.modules if m in ALL_MODULES]
    company.modules_enabled = json.dumps(valid)
    db.commit()
    return {"ok": True, "modules": valid}


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


# ── Client error reporting ────────────────────────────────────────────────────

class ClientErrorIn(BaseModel):
    message: str
    stack: Optional[str] = None
    url: Optional[str] = None
    context: Optional[str] = None
    user_agent: Optional[str] = None


@router.post("/client-errors", status_code=201)
@limiter.limit("30/minute")
def report_client_error(
    request: Request,
    payload: ClientErrorIn,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Accepts client-side JS errors. Auth is optional — errors before login are still captured."""
    from app.models import uuid as _uuid
    company_id: Optional[str] = None
    user_id: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        uid = user_id_from_token(token)
        if uid:
            user = db.query(User).filter(User.id == uid).first()
            if user:
                user_id = user.id
                company_id = user.company_id
    err = ClientError(
        id=_uuid(),
        company_id=company_id,
        user_id=user_id,
        message=payload.message[:2000],
        stack=(payload.stack or "")[:4000] or None,
        url=(payload.url or "")[:500] or None,
        context=(payload.context or "")[:120] or None,
        user_agent=(payload.user_agent or "")[:500] or None,
    )
    db.add(err)
    db.commit()
    return {"ok": True}


@router.get("/client-errors")
def list_client_errors(
    days: int = 7,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(ClientError)
        .filter(ClientError.occurred_at >= since)
        .order_by(ClientError.occurred_at.desc())
        .limit(limit)
        .all()
    )
    total = db.query(func.count(ClientError.id)).filter(ClientError.occurred_at >= since).scalar()
    return {
        "total": total,
        "errors": [
            {
                "id": r.id,
                "company_id": r.company_id,
                "user_id": r.user_id,
                "message": r.message,
                "stack": r.stack,
                "url": r.url,
                "context": r.context,
                "user_agent": r.user_agent,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ],
    }


@router.delete("/client-errors")
def clear_client_errors(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    deleted = db.query(ClientError).delete()
    db.commit()
    return {"ok": True, "deleted": deleted}


# ── Audit log aggregation ─────────────────────────────────────────────────────

@router.get("/audit-logs")
def list_audit_logs(
    days: int = 7,
    limit: int = 300,
    company_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    """Return recent audit log entries from all companies (or one if company_id given)."""
    import json as _json
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.collection == "audit",
            AppDataRecord.created_at >= since,
        )
        .order_by(AppDataRecord.created_at.desc())
    )
    if company_id:
        q = q.filter(AppDataRecord.company_id == company_id)
    rows = q.limit(limit).all()

    # Load company names for display
    company_ids = {r.company_id for r in rows}
    companies = {c.id: c.name for c in db.query(Company).filter(Company.id.in_(company_ids)).all()} if company_ids else {}

    entries = []
    for row in rows:
        try:
            payload = _json.loads(row.data) if isinstance(row.data, str) else (row.data or {})
        except Exception:
            payload = {}
        entries.append({
            "id": row.id,
            "company_id": row.company_id,
            "company_name": companies.get(row.company_id, row.company_id),
            "action": payload.get("action") or payload.get("event") or "—",
            "detail": payload.get("detail") or payload.get("description") or "",
            "status": payload.get("status") or "",
            "user": payload.get("user") or payload.get("email") or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return {"total": len(entries), "entries": entries}
