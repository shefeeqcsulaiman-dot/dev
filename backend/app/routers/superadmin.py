import datetime as _dt
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.company_defaults import seed_company_defaults
from app.config import get_settings
from app.database import get_db
from app.dependencies import Principal, get_current_principal, get_current_user
from app.limiter import limiter
from app.routers.app_data import build_all_companies_backup_zip, build_company_sql_dump
# Re-exported here under the same name for every pre-existing call site in
# this file — moved to app/module_catalog.py (Branch Login Phase 1) so core
# auth code (auth_principal.py, dependencies.py) and branches.py can import
# it without a reverse dependency on this router module.
from app.module_catalog import ALL_MODULES
from app.models import (
    AccrualPrepaymentRecord, Account, AppDataRecord, ApprovalMatrixRecord,
    AttendanceDetail, AttendanceSession, AuditLog, AuditLogDetail, BankAccount,
    BankReconciliationMatch, BankStatementLine, BiometricDevice, BudgetRecord,
    Branch, CashFlowForecastRecord, ClientError, Company, CompanyLocation,
    ConsolidationRecord, CorporateTaxRecord, CorporateTaxReturn, CostCenterRecord,
    CreditControlRecord, CustomerAgingSnapshot, DailyGlBalance, Document,
    DomainEvent, Employee, EmployeeBranchAccess, EmployeeLocation, EmployeeLocationLog, EventOutbox,
    EventProcessingLog, ExceptionEvent, FixedAssetRecord, GeneralLedgerEntry,
    ImpersonationSession, InventoryBalanceSnapshot, InventoryValuationLayer, Invoice, InvoiceLine,
    ItemUnit, ItemUnitConversion, Job, JobStatus, JournalEntry, JournalLine, LeaveRequest,
    MonthEndCloseRecord, Payment, PayrollItem, PayrollRun, PeriodLock, PostingJob,
    Receipt, Role, RolePermission, SourceTransaction, SourceTransactionLine,
    StockAdjustmentApproval, StockMovement, StockProductMapping, TaxCode, TaxLine,
    TaxPeriod, TrialRequest, User, VatReturn, VatReturnSnapshot, Voucher,
    VoucherLine, VoucherType, Warehouse, WpsBatch,
)
from app.schemas import CompanyUpdate
from app.security import (
    create_access_token, hash_password, impersonation_revocation_info,
    impersonator_id_from_token, revoke_impersonation_jti, user_id_from_token,
    verify_password,
)

router = APIRouter(prefix="/superadmin", tags=["superadmin"])
settings = get_settings()


def _require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


class SetExpiryIn(BaseModel):
    expires_at: str | None = None


class ExtendRenewalsIn(BaseModel):
    company_ids: list[str] = Field(min_length=1)
    days: int = Field(ge=1, le=3650)


class ResetPasswordIn(BaseModel):
    user_id: str
    password: str = Field(min_length=6)


class DeleteCompanyIn(BaseModel):
    # The acting superadmin's OWN account password — verified server-side
    # against their real password_hash below. Previously this destructive
    # endpoint had no confirmation of any kind server-side; the two
    # "authorization passwords" the frontend modal checked were hardcoded
    # literal strings baked into the shipped JS (view-source readable,
    # identical forever), never sent to or verified by the backend at all.
    password: str


class ModulesIn(BaseModel):
    modules: list[str]


class BulkModuleIn(BaseModel):
    """Turn ONE module on/off for many companies at once (Module Permissions >
    "All companies"). company_ids omitted/null = every company."""
    module: str
    enabled: bool
    company_ids: list[str] | None = None


class CreateCompanyIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    full_name: str = ""
    trn: str | None = None
    expires_at: str | None = None
    modules: list[str] | None = None
    country: str | None = None
    currency: str | None = None
    vat_rate: Decimal | None = None




class AddUserIn(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "user"


class UpdateUserIn(BaseModel):
    email: str | None = None
    full_name: str | None = None
    role: str | None = None


def _company_last_login(users: list[User], now_utc: datetime) -> tuple[datetime | None, int | None]:
    """Most recent recorded login among a company's own users (super admin excluded)
    and how many whole days ago that was. (None, None) = no login on record yet."""
    stamps = [u.last_login for u in users if u.role != "superadmin" and getattr(u, "last_login", None)]
    if not stamps:
        return None, None
    latest = max(s if s.tzinfo else s.replace(tzinfo=timezone.utc) for s in stamps)
    return latest, max(0, (now_utc - latest).days)


def _days_left(expires_at: str | None, today: _dt.date) -> int | None:
    if not expires_at:
        return None
    try:
        return (_dt.date.fromisoformat(expires_at[:10]) - today).days
    except ValueError:
        return None


@router.get("/renewals")
def list_renewals(
    days: int = 30,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    """Companies whose subscription is already expired or expires within `days`,
    soonest first, plus headline counts for the Renewals page / Overview card."""
    days = max(1, min(days, 365))
    today = datetime.now(timezone.utc).date()
    companies = (
        db.query(Company)
        .filter(or_(Company.trn.is_(None), Company.trn != "SUPERADMIN-INTERNAL"))
        .all()
    )
    ids = [c.id for c in companies]
    users_by_company: dict[str, list[User]] = {}
    if ids:
        for u in db.query(User).filter(User.company_id.in_(ids)).all():
            users_by_company.setdefault(u.company_id, []).append(u)
    rows = []
    no_expiry = 0
    for c in companies:
        left = _days_left(c.subscription_expires_at, today)
        if left is None:
            no_expiry += 1
            continue
        if left > days:
            continue
        users = users_by_company.get(c.id, [])
        admin = next((u for u in users if u.role == "admin"), None)
        last_login_at, inactive_days = _company_last_login(users, datetime.now(timezone.utc))
        rows.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "admin_email": admin.email if admin else None,
            "subscription_expires_at": c.subscription_expires_at,
            "days_left": left,
            "status": "expired" if left < 0 else "expiring",
            "last_login_at": last_login_at.isoformat() if last_login_at else None,
            "inactive_days": inactive_days,
            "user_count": len([u for u in users if u.role != "superadmin"]),
        })
    rows.sort(key=lambda r: r["days_left"])
    return {
        "days": days,
        "counts": {
            "expired": sum(1 for r in rows if r["status"] == "expired"),
            "within_7": sum(1 for r in rows if 0 <= r["days_left"] <= 7),
            "within_window": len(rows),
            "no_expiry": no_expiry,
        },
        "companies": rows,
    }


@router.post("/renewals/extend")
def extend_renewals(
    body: ExtendRenewalsIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    """Add `days` to each company's subscription: from its current expiry when that is
    still in the future, otherwise from today (same rule the per-row "+7 Days / +1 Year"
    menu uses). Audited per company."""
    today = datetime.now(timezone.utc).date()
    results = []
    for company in db.query(Company).filter(
        Company.id.in_(body.company_ids),
        or_(Company.trn.is_(None), Company.trn != "SUPERADMIN-INTERNAL"),
    ).all():
        left = _days_left(company.subscription_expires_at, today)
        base = today + timedelta(days=left) if left is not None and left > 0 else today
        new_expiry = (base + timedelta(days=body.days)).isoformat()
        company.subscription_expires_at = new_expiry
        _write_audit_blob(db, company.id, superadmin.email, "extend_expiry",
                          f"Expiry extended by {body.days} days to {new_expiry} (renewals)", "Done")
        results.append({"id": company.id, "name": company.name, "subscription_expires_at": new_expiry})
    if not results:
        raise HTTPException(status_code=404, detail="No matching companies")
    db.commit()
    return {"ok": True, "extended": len(results), "results": results}


@router.get("/companies")
def list_companies(db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    companies = (
        db.query(Company)
        .filter(or_(Company.trn.is_(None), Company.trn != "SUPERADMIN-INTERNAL"))
        .order_by(Company.created_at.desc())
        .all()
    )
    company_ids = [c.id for c in companies]

    # Previously 4 queries PER company (users, employee_count, branches,
    # employees) — O(n) round trips that scaled with tenant count, same
    # shape as the /invoices and dashboard() chattiness fixed earlier this
    # pass (see docs/architecture.md §29). Batched into 4 queries total,
    # grouped into per-company buckets in Python.
    users_by_company: dict[str, list[User]] = {}
    if company_ids:
        for u in db.query(User).filter(User.company_id.in_(company_ids)).all():
            users_by_company.setdefault(u.company_id, []).append(u)

    active_employee_counts: dict[str, int] = {}
    if company_ids:
        for cid, cnt in (
            db.query(Employee.company_id, func.count(Employee.id))
            .filter(Employee.company_id.in_(company_ids), Employee.status == "active")
            .group_by(Employee.company_id)
            .all()
        ):
            active_employee_counts[cid] = cnt

    branches_by_company: dict[str, list[Branch]] = {}
    if company_ids:
        for b in db.query(Branch).filter(Branch.company_id.in_(company_ids)).order_by(Branch.name).all():
            branches_by_company.setdefault(b.company_id, []).append(b)

    # Still capped to 200 per company (a roster can run into the
    # hundreds/thousands; active_employee_counts above already conveys the
    # true total past this cap) — capped in Python after one fetch rather
    # than with a per-company LIMIT, since that would need a window
    # function to stay correct across companies in a single query.
    employees_by_company: dict[str, list[Employee]] = {}
    if company_ids:
        for e in db.query(Employee).filter(Employee.company_id.in_(company_ids)).order_by(Employee.employee_no).all():
            bucket = employees_by_company.setdefault(e.company_id, [])
            if len(bucket) < 200:
                bucket.append(e)

    result = []
    now_utc = datetime.now(timezone.utc)
    for company in companies:
        users = users_by_company.get(company.id, [])
        last_login_at, inactive_days = _company_last_login(users, now_utc)
        employee_count = active_employee_counts.get(company.id, 0)
        branches = branches_by_company.get(company.id, [])
        branch_names = {b.id: b.name for b in branches}
        employees = employees_by_company.get(company.id, [])
        sub_users = [u for u in users if u.role not in ("admin", "superadmin")]
        try:
            mods = json.loads(company.modules_enabled) if company.modules_enabled else ALL_MODULES
        except Exception:
            mods = ALL_MODULES
        result.append(
            {
                "id": company.id,
                "name": company.name,
                "trade_name": company.trade_name,
                "trn": company.trn,
                "country": company.country,
                "currency": company.currency,
                "vat_rate": str(company.vat_rate),
                "emirate": company.emirate,
                "business_type": company.business_type,
                "business_activity": company.business_activity,
                "legal_structure": company.legal_structure,
                "trade_license_no": company.trade_license_no,
                "trade_license_issue_date": company.trade_license_issue_date,
                "trade_license_expiry": company.trade_license_expiry,
                "free_zone": company.free_zone,
                "address": company.address,
                "po_box": company.po_box,
                "phone": company.phone,
                "website": company.website,
                "fta_username": company.fta_username,
                "created_at": company.created_at.isoformat() if company.created_at else None,
                "subscription_expires_at": company.subscription_expires_at,
                "last_login_at": last_login_at.isoformat() if last_login_at else None,
                "inactive_days": inactive_days,
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
                "branches": [
                    {
                        "id": b.id,
                        "name": b.name,
                        "code": b.code,
                        "city": b.city,
                        "status": b.status,
                    }
                    for b in branches
                ],
                "employees": [
                    {
                        "id": e.id,
                        "employee_no": e.employee_no,
                        "full_name": e.full_name,
                        "department": e.department,
                        "designation": e.designation,
                        "status": e.status,
                        "branch_name": branch_names.get(e.branch_id),
                    }
                    for e in employees
                ],
            }
        )
    return result


@router.get("/companies/{company_id}/db-dump")
@limiter.limit("10/minute")
def superadmin_company_db_dump(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_superadmin),
) -> Response:
    """Same restorable SQL backup a company owner can download for
    themselves (GET /app-data/db-dump), but reachable by Superadmin for
    ANY company without needing to log in as / impersonate them."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(404, "Company not found")
    sql_text, fname = build_company_sql_dump(db, company_id, f"Superadmin ({current_user.full_name})")
    return Response(
        content=sql_text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/companies/backup-all")
@limiter.limit("3/minute")
def superadmin_backup_all_companies(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_superadmin),
) -> Response:
    """One .zip containing every company's own restorable SQL backup file
    (same format/content as the per-company endpoint above), so Superadmin
    can pull a full-platform backup in one action. Zipped per-company
    rather than concatenated into one script -- keeps each tenant's data
    separable for a real single-company restore, and a bad/huge company
    dump can't corrupt the file boundaries of the others."""
    zip_bytes = build_all_companies_backup_zip(db, f"Superadmin ({current_user.full_name})")
    date_str = _dt.datetime.utcnow().strftime("%Y%m%d")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="taxflow-all-companies-backup-{date_str}.zip"'},
    )


@router.post("/companies/{company_id}/set-expiry")
def set_expiry(
    company_id: str,
    body: SetExpiryIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.subscription_expires_at = body.expires_at
    # This doubles as Suspend Now (frontend passes today's date) — a
    # login-blocking action for every user in the company, previously the
    # only mutating endpoint in this file with no audit trail at all.
    today = datetime.now(timezone.utc).date().isoformat()
    if not body.expires_at:
        action, detail = "clear_expiry", "Subscription expiry removed (no limit)"
    elif body.expires_at <= today:
        action, detail = "suspend_company", f"Suspended (expiry set to {body.expires_at})"
    else:
        action, detail = "set_expiry", f"Expiry set to {body.expires_at}"
    _write_audit_blob(db, company_id, superadmin.email, action, detail, "Done")
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/reset-password")
@limiter.limit("20/minute")
def reset_password(
    request: Request,
    company_id: str,
    body: ResetPasswordIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == body.user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot reset another superadmin's password")
    user.password_hash = hash_password(body.password)
    _write_audit_blob(db, company_id, superadmin.email, "reset_password", user.email, "Done")
    db.commit()
    return {"ok": True}


@router.post("/companies", status_code=201)
def create_company(
    body: CreateCompanyIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    mods = body.modules if body.modules is not None else ALL_MODULES
    company = Company(
        name=body.name.strip(),
        trn=body.trn or None,
        country=(body.country or "United Arab Emirates").strip() or "United Arab Emirates",
        subscription_expires_at=body.expires_at,
        modules_enabled=json.dumps(mods),
    )
    # currency/vat_rate otherwise fall back to the Company model's own
    # defaults (AED/5.00) — only set explicitly when the frontend sends a
    # non-UAE country's values, same optional-override pattern
    # update_company() already uses.
    if body.currency:
        company.currency = body.currency.strip()
    if body.vat_rate is not None:
        company.vat_rate = body.vat_rate
    db.add(company)
    db.flush()
    # Same reason /auth/register (self-serve signup) calls this: a company
    # with no chart of accounts can never post a single transaction —
    # post_source_transaction() requires control accounts 1100/2200 to
    # exist and fails the posting job silently otherwise, so every invoice/
    # purchase saved fine in the UI but never reached the ledger, VAT
    # report, or any financial statement. This endpoint never called it, so
    # every tenant created via the Super Admin panel started financially
    # non-functional the same way self-serve signups once did.
    seed_company_defaults(db, company.id)
    full_name = body.full_name.strip() or (body.name.strip() + " Admin")
    user = User(
        company_id=company.id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(body.password),
        role="admin",
    )
    db.add(user)
    _write_audit_blob(db, company.id, superadmin.email, "create_company", company.name, "Done")
    db.commit()
    return {"ok": True, "company_id": company.id}


@router.patch("/companies/{company_id}")
def update_company(
    company_id: str,
    body: CompanyUpdate,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    # SUPERADMIN-INTERNAL is a sentinel TRN list_companies() uses to hide the
    # internal superadmin company from the tenant list — don't let a normal
    # PATCH spoof a tenant into (or out of) that hidden state.
    if body.trn is not None and body.trn.strip() == "SUPERADMIN-INTERNAL" and company.trn != "SUPERADMIN-INTERNAL":
        raise HTTPException(status_code=400, detail="This TRN value is reserved")

    # Same field set as PUT /companies/current (companies.py) — superadmin
    # edits the full tenant profile, not just name/trn/country.
    nullable_fields = [
        "trade_name", "emirate", "business_type", "business_activity",
        "legal_structure", "trade_license_no", "trade_license_issue_date",
        "trade_license_expiry", "free_zone", "address", "po_box",
        "phone", "website", "fta_username",
    ]
    for field in nullable_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(company, field, val.strip() or None)

    if body.name:
        company.name = body.name.strip()
    if body.country:
        company.country = body.country.strip()
    if body.currency:
        company.currency = body.currency.strip()
    if body.vat_rate is not None:
        company.vat_rate = body.vat_rate
    if body.trn:
        company.trn = body.trn.strip()

    _write_audit_blob(db, company_id, superadmin.email, "update_company", company.name, "Done")
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/users", status_code=201)
def add_user(
    company_id: str,
    body: AddUserIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
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
    # A superadmin can create an admin-role user in any tenant — previously
    # the only user-creation path in this file with no audit trail.
    _write_audit_blob(db, company_id, superadmin.email, "add_user", f"{email} ({role})", "Done")
    db.commit()
    return {"ok": True, "user_id": user.id}


@router.patch("/companies/{company_id}/users/{user_id}")
def update_user(
    company_id: str,
    user_id: str,
    body: UpdateUserIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin" and body.role is not None and body.role != "superadmin":
        raise HTTPException(status_code=400, detail="Cannot change another superadmin's role")
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
    _write_audit_blob(db, company_id, superadmin.email, "update_user", user.email, "Done")
    db.commit()
    return {"ok": True}


@router.post("/companies/{company_id}/users/{user_id}/toggle-status")
def toggle_user_status(
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot disable superadmin")
    user.is_active = not getattr(user, "is_active", True)
    _write_audit_blob(db, company_id, superadmin.email, "toggle_user_status", user.email, "Active" if user.is_active else "Disabled")
    db.commit()
    return {"ok": True, "is_active": user.is_active}


@router.delete("/companies/{company_id}/users/{user_id}")
def delete_user(
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    user = db.query(User).filter(User.id == user_id, User.company_id == company_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot delete superadmin user")
    _write_audit_blob(db, company_id, superadmin.email, "delete_user", user.email, "Deleted")
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
    superadmin: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    valid = [m for m in body.modules if m in ALL_MODULES]
    company.modules_enabled = json.dumps(valid)
    _write_audit_blob(db, company_id, superadmin.email, "set_company_modules", ", ".join(valid), "Done")
    db.commit()
    return {"ok": True, "modules": valid}


@router.put("/modules/bulk")
def set_module_for_companies(
    body: BulkModuleIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    """Enable/disable a single module across all companies (or a chosen subset),
    leaving every other module of each company exactly as it was. A company with
    no explicit list (NULL/empty = unrestricted) is treated as having every
    module on, so switching one module off gives it an explicit list of the rest."""
    if body.module not in ALL_MODULES:
        raise HTTPException(status_code=400, detail=f"Unknown module '{body.module}'")
    query = db.query(Company)
    if body.company_ids is not None:
        query = query.filter(Company.id.in_(body.company_ids))
    changed = unchanged = 0
    for company in query.all():
        try:
            current = json.loads(company.modules_enabled) if company.modules_enabled else list(ALL_MODULES)
        except Exception:
            current = list(ALL_MODULES)
        if not isinstance(current, list):
            current = list(ALL_MODULES)
        has = body.module in current
        if body.enabled == has:
            unchanged += 1
            continue
        updated = current + [body.module] if body.enabled else [m for m in current if m != body.module]
        # keep the catalog order so the stored list stays tidy
        company.modules_enabled = json.dumps([m for m in ALL_MODULES if m in updated])
        _write_audit_blob(
            db, company.id, superadmin.email, "set_company_module",
            f"{body.module}={'on' if body.enabled else 'off'} (bulk)", "Done",
        )
        changed += 1
    db.commit()
    return {"ok": True, "module": body.module, "enabled": body.enabled, "changed": changed, "unchanged": unchanged}


@router.delete("/companies/{company_id}")
@limiter.limit("20/minute")
def delete_company(
    request: Request,
    company_id: str,
    payload: DeleteCompanyIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    if not superadmin.password_hash or not verify_password(payload.password, superadmin.password_hash):
        raise HTTPException(status_code=403, detail="Incorrect password")
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if db.query(User).filter(User.company_id == company_id, User.role == "superadmin").count() > 0:
        raise HTTPException(status_code=400, detail="Cannot delete superadmin company")

    cid = company_id
    s = dict(synchronize_session=False)
    # Logged against the superadmin's OWN company, not the one being deleted —
    # this record would otherwise be wiped out along with the target company's
    # AppDataRecord rows a few lines down, leaving no trace it ever happened.
    _write_audit_blob(db, superadmin.company_id, superadmin.email, "delete_company", f"{company.name} ({cid})", "Deleted")

    # Tier 1 — leaf rows that FK into data tables (no direct company_id)
    inv_ids = db.query(Invoice.id).filter(Invoice.company_id == cid).subquery()
    db.query(InvoiceLine).filter(InvoiceLine.invoice_id.in_(inv_ids)).delete(**s)

    src_ids = db.query(SourceTransaction.id).filter(SourceTransaction.company_id == cid).subquery()
    db.query(SourceTransactionLine).filter(SourceTransactionLine.source_id.in_(src_ids)).delete(**s)

    run_ids = db.query(PayrollRun.id).filter(PayrollRun.company_id == cid).subquery()
    db.query(PayrollItem).filter(PayrollItem.run_id.in_(run_ids)).delete(**s)

    # GeneralLedgerEntry.journal_line_id FKs into journal_lines, so it must
    # be cleared before JournalLine below — this was reversed (GL entries
    # were deleted in "Tier 2", journal_lines here in "Tier 1", i.e. after),
    # which raised a ForeignKeyViolation for any company whose postings had
    # actually produced GL entries (e.g. a company with real, posted
    # transactions — reproduced live on a real customer's data, not caught
    # by the earlier GPS/RBAC/Leave fix since this is a separate ordering
    # bug in a section that predates all of that).
    db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.company_id == cid).delete(**s)

    je_ids = db.query(JournalEntry.id).filter(JournalEntry.company_id == cid).subquery()
    db.query(JournalLine).filter(JournalLine.journal_id.in_(je_ids)).delete(**s)

    v_ids = db.query(Voucher.id).filter(Voucher.company_id == cid).subquery()
    db.query(VoucherLine).filter(VoucherLine.voucher_id.in_(v_ids)).delete(**s)

    # GPS/RBAC/Leave — leaf rows that FK into employees/attendance_sessions,
    # must clear before Employee is deleted below (Tier 4). Previously
    # missing entirely (added after this cascade was written), which raised
    # a foreign-key IntegrityError on any company that had used HRMS GPS
    # attendance, roles, or leave — surfaced as a generic "An internal
    # error occurred" with no useful detail.
    db.query(EmployeeLocationLog).filter(EmployeeLocationLog.company_id == cid).delete(**s)
    db.query(LeaveRequest).filter(LeaveRequest.company_id == cid).delete(**s)
    db.query(AttendanceSession).filter(AttendanceSession.company_id == cid).delete(**s)
    emp_ids = db.query(Employee.id).filter(Employee.company_id == cid).subquery()
    db.query(EmployeeLocation).filter(EmployeeLocation.employee_id.in_(emp_ids)).delete(**s)
    # Same shape as EmployeeLocation (no company_id column of its own, FKs
    # into employees/branches) — previously missing here too, so any
    # company that had granted multi-branch access (Branch Security Layer
    # Phase 3) would hit the same FK IntegrityError this whole block was
    # already written to avoid for GPS/RBAC/Leave.
    db.query(EmployeeBranchAccess).filter(EmployeeBranchAccess.employee_id.in_(emp_ids)).delete(**s)

    # Tier 2 — tables that cross-reference other data tables
    db.query(BankReconciliationMatch).filter(BankReconciliationMatch.company_id == cid).delete(**s)
    db.query(BankStatementLine).filter(BankStatementLine.company_id == cid).delete(**s)

    al_ids = db.query(AuditLog.id).filter(AuditLog.company_id == cid).subquery()
    db.query(AuditLogDetail).filter(AuditLogDetail.audit_log_id.in_(al_ids)).delete(**s)

    db.query(EventOutbox).filter(EventOutbox.company_id == cid).delete(**s)
    db.query(EventProcessingLog).filter(EventProcessingLog.company_id == cid).delete(**s)
    db.query(PostingJob).filter(PostingJob.company_id == cid).delete(**s)
    db.query(WpsBatch).filter(WpsBatch.company_id == cid).delete(**s)
    db.query(StockMovement).filter(StockMovement.company_id == cid).delete(**s)
    db.query(InventoryValuationLayer).filter(InventoryValuationLayer.company_id == cid).delete(**s)
    db.query(StockAdjustmentApproval).filter(StockAdjustmentApproval.company_id == cid).delete(**s)
    db.query(Payment).filter(Payment.company_id == cid).delete(**s)
    db.query(Receipt).filter(Receipt.company_id == cid).delete(**s)

    # Tier 3 — tables that reference accounts/voucher_types/users
    db.query(Voucher).filter(Voucher.company_id == cid).delete(**s)
    db.query(VoucherType).filter(VoucherType.company_id == cid).delete(**s)
    db.query(AuditLog).filter(AuditLog.company_id == cid).delete(**s)
    db.query(AuditLogDetail).filter(AuditLogDetail.company_id == cid).delete(**s)
    db.query(ExceptionEvent).filter(ExceptionEvent.company_id == cid).delete(**s)
    db.query(PeriodLock).filter(PeriodLock.company_id == cid).delete(**s)
    db.query(DomainEvent).filter(DomainEvent.company_id == cid).delete(**s)

    # Tier 4 — main data tables
    db.query(Invoice).filter(Invoice.company_id == cid).delete(**s)
    db.query(JournalEntry).filter(JournalEntry.company_id == cid).delete(**s)
    db.query(SourceTransaction).filter(SourceTransaction.company_id == cid).delete(**s)
    db.query(PayrollRun).filter(PayrollRun.company_id == cid).delete(**s)
    db.query(TaxLine).filter(TaxLine.company_id == cid).delete(**s)
    db.query(TaxPeriod).filter(TaxPeriod.company_id == cid).delete(**s)
    db.query(TaxCode).filter(TaxCode.company_id == cid).delete(**s)
    db.query(VatReturn).filter(VatReturn.company_id == cid).delete(**s)
    db.query(CorporateTaxReturn).filter(CorporateTaxReturn.company_id == cid).delete(**s)
    db.query(BankAccount).filter(BankAccount.company_id == cid).delete(**s)
    db.query(Warehouse).filter(Warehouse.company_id == cid).delete(**s)
    db.query(StockProductMapping).filter(StockProductMapping.company_id == cid).delete(**s)
    db.query(ItemUnitConversion).filter(ItemUnitConversion.company_id == cid).delete(**s)
    db.query(ItemUnit).filter(ItemUnit.company_id == cid).delete(**s)
    db.query(AttendanceDetail).filter(AttendanceDetail.company_id == cid).delete(**s)
    db.query(BiometricDevice).filter(BiometricDevice.company_id == cid).delete(**s)
    db.query(ApprovalMatrixRecord).filter(ApprovalMatrixRecord.company_id == cid).delete(**s)
    db.query(Employee).filter(Employee.company_id == cid).delete(**s)
    db.query(Document).filter(Document.company_id == cid).delete(**s)
    db.query(Job).filter(Job.company_id == cid).delete(**s)
    db.query(AppDataRecord).filter(AppDataRecord.company_id == cid).delete(**s)
    db.query(ClientError).filter(ClientError.company_id == cid).delete(**s)
    # ImpersonationSession.target_user_id/target_branch_id FK into this
    # company's users/branches (always set together with company_id == cid
    # by impersonate_company() — see superadmin_id there, which belongs to
    # the SUPERADMIN's own company instead and is unaffected by this
    # delete). Previously missing entirely, so any company that had ever
    # been impersonated (Super Admin > Impersonate) could no longer be
    # deleted at all — surfaced only as a generic "An internal error
    # occurred" from the FK IntegrityError, same failure shape as the
    # GPS/RBAC/Leave gaps fixed above.
    db.query(ImpersonationSession).filter(ImpersonationSession.company_id == cid).delete(**s)

    # Role/CompanyLocation can only go now — Employee.role_id and
    # Employee.work_location_id reference them, and Employee is deleted above.
    role_ids = db.query(Role.id).filter(Role.company_id == cid).subquery()
    db.query(RolePermission).filter(RolePermission.role_id.in_(role_ids)).delete(**s)
    db.query(Role).filter(Role.company_id == cid).delete(**s)
    db.query(CompanyLocation).filter(CompanyLocation.company_id == cid).delete(**s)
    db.query(Branch).filter(Branch.company_id == cid).delete(**s)

    # Tier 5 — snapshot / reporting tables (company_id only)
    for Model in (
        DailyGlBalance, InventoryBalanceSnapshot, CustomerAgingSnapshot,
        VatReturnSnapshot, CorporateTaxRecord, FixedAssetRecord,
        AccrualPrepaymentRecord, CostCenterRecord, BudgetRecord,
        CashFlowForecastRecord, CreditControlRecord, MonthEndCloseRecord,
        ConsolidationRecord,
    ):
        db.query(Model).filter(Model.company_id == cid).delete(**s)

    # Tier 6 — Account (self-referential FK: null parent first, then delete)
    db.query(Account).filter(Account.company_id == cid).update(
        {"parent_account_id": None}, synchronize_session=False
    )
    db.query(Account).filter(Account.company_id == cid).delete(**s)

    # Tier 7 — Users, then Company
    db.query(User).filter(User.company_id == cid).delete(**s)
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
    page: Optional[str] = None
    viewport: Optional[str] = None
    # navigator.sendBeacon (the primary transport in app.js — see
    # setupGlobalErrorCapture) cannot set custom headers, so the bearer
    # token can't travel as an Authorization header on that path. The
    # frontend puts it here instead so the user/company can still be
    # resolved; the Authorization header (used by the fetch() fallback)
    # takes precedence when both are present.
    token: Optional[str] = None


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
    bearer_token: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        bearer_token = authorization.removeprefix("Bearer ").strip()
    elif payload.token:
        bearer_token = payload.token.strip()
    if bearer_token:
        uid = user_id_from_token(bearer_token)
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
        page=(payload.page or "")[:80] or None,
        viewport=(payload.viewport or "")[:20] or None,
    )
    db.add(err)
    db.commit()
    return {"ok": True}


@router.get("/db-diagnostics")
def db_diagnostics(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    """Read-only sizing/connection diagnostics for the live Postgres
    instance — added because the managed database's own network access
    restrictions (correctly) block a direct psql/psycopg2 connection from
    outside DO's trusted sources, so this is checked through the app's own
    already-trusted DB connection instead. Nothing here writes anything."""
    if db.get_bind().dialect.name != "postgresql":
        return {"supported": False, "detail": f"Diagnostics need PostgreSQL (this environment runs {db.get_bind().dialect.name})"}
    db_size = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar()
    max_connections = db.execute(text("SHOW max_connections")).scalar()
    active_connections = db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar()
    running_queries = db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")).scalar()
    pg_version = db.execute(text("SELECT version()")).scalar()
    top_tables = db.execute(text("""
        SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size, n_live_tup
        FROM pg_catalog.pg_statio_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
        LIMIT 15
    """)).all()
    return {
        "supported": True,
        "database_size": db_size,
        "max_connections": max_connections,
        "active_connections": active_connections,
        "running_queries": running_queries,
        "postgres_version": pg_version,
        "top_tables": [{"table": r[0], "size": r[1], "row_estimate": r[2]} for r in top_tables],
    }


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

    user_ids = {r.user_id for r in rows if r.user_id}
    users_by_id = {
        u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
    } if user_ids else {}
    company_ids = {r.company_id for r in rows if r.company_id}
    companies_by_id = {
        c.id: c for c in db.query(Company).filter(Company.id.in_(company_ids)).all()
    } if company_ids else {}

    return {
        "total": total,
        "errors": [
            {
                "id": r.id,
                "company_id": r.company_id,
                "company_name": companies_by_id[r.company_id].name if r.company_id in companies_by_id else None,
                "user_id": r.user_id,
                "user_name": users_by_id[r.user_id].full_name if r.user_id in users_by_id else None,
                "user_email": users_by_id[r.user_id].email if r.user_id in users_by_id else None,
                "message": r.message,
                "stack": r.stack,
                "url": r.url,
                "context": r.context,
                "user_agent": r.user_agent,
                "page": r.page,
                "viewport": r.viewport,
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
            payload = _json.loads(row.payload) if isinstance(row.payload, str) else (row.payload or {})
        except Exception:
            payload = {}
        entries.append({
            "id": row.id,
            "company_id": row.company_id,
            "company_name": companies.get(row.company_id, row.company_id),
            "action": payload.get("action") or payload.get("event") or "—",
            "detail": payload.get("detail") or payload.get("record") or payload.get("description") or "",
            "status": payload.get("status") or payload.get("result") or "",
            "user": payload.get("user") or payload.get("email") or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # Platform jobs (e.g. the nightly backup) log to the SQL AuditLog table.
    platform_q = db.query(AuditLog).filter(AuditLog.module == "platform", AuditLog.created_at >= since)
    if company_id:
        platform_q = platform_q.filter(AuditLog.company_id == company_id)
    for log in platform_q.order_by(AuditLog.created_at.desc()).limit(limit).all():
        entries.append({
            "id": log.id,
            "company_id": log.company_id,
            "company_name": "Platform",
            "action": log.action.replace("_", " "),
            "detail": log.detail or "",
            "status": "Failed" if log.action.endswith("_failed") else "OK",
            "user": "System",
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    entries.sort(key=lambda e: e["created_at"] or "", reverse=True)
    entries = entries[:limit]

    return {"total": len(entries), "entries": entries}


# ── Usage analytics ───────────────────────────────────────────────────────────
# There's no billing data anywhere in this schema (no plan/price on Company, no
# payment gateway), so this tracks real usage activity instead of revenue. The
# frontend's audit() calls always write user="System User" (no real per-user
# identity), so "active users" here is intentionally not part of this — it
# would just be a fake-looking flat number. Everything below is built only
# from counts/timestamps that are genuinely real.

_COLLECTION_MODULE_MAP: dict[str, str] = {
    "salesInvoices": "sales", "salesCategories": "sales", "salesUnits": "sales", "customers": "sales",
    "quotations": "quotations",
    "purchaseDocuments": "purchase", "purchaseRecords": "purchase", "vendors": "purchase", "bills": "purchase",
    "products": "inventory", "warehouses": "inventory", "stockMovements": "inventory", "itemUnits": "inventory",
    "expenses": "expense",
    "bankAccounts": "bank", "payments": "bank", "receipts": "bank",
    "accounts": "accounting", "ledger": "accounting", "journalDrafts": "accounting",
    "employees": "hrms", "rotaShifts": "hrms", "rotaAssignments": "hrms", "rotaSwaps": "hrms",
    "rotaApprovals": "hrms", "leaveRequests": "hrms", "overtimeRequests": "hrms",
    "attendanceCorrections": "hrms", "payrollRuns": "hrms", "payrollAdjustments": "hrms", "hrUsers": "hrms",
}


@router.get("/usage-analytics")
def usage_analytics(
    days: int = 30,
    company_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    days = max(1, min(days, 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Previously pulled every matching AppDataRecord row (company_id,
    # collection, created_at) into Python and aggregated it there — fine at
    # today's volume, but a company with heavy activity over 90 days could
    # mean hundreds of thousands of rows crossing the wire just to be
    # counted. Replaced with GROUP BY queries: each returns at most a few
    # hundred rows (one per day, per company, or per collection — all
    # small, bounded result sets) regardless of how many records exist.
    base_filter = [AppDataRecord.created_at >= since]
    if company_id:
        base_filter.append(AppDataRecord.company_id == company_id)

    total_records = db.query(func.count(AppDataRecord.id)).filter(*base_filter).scalar() or 0

    daily_rows = (
        db.query(func.date(AppDataRecord.created_at), func.count(AppDataRecord.id))
        .filter(*base_filter)
        .group_by(func.date(AppDataRecord.created_at))
        .all()
    )
    daily_counts = {str(d): c for d, c in daily_rows}

    dates = [(datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    daily_trend = [{"date": d, "count": daily_counts.get(d, 0)} for d in dates]

    new_users_rows = (
        db.query(func.date(User.created_at), func.count(User.id))
        .filter(User.created_at >= since, User.role != "superadmin")
        .group_by(func.date(User.created_at))
        .all()
    )
    new_users_by_day = {str(d): c for d, c in new_users_rows}
    new_users_trend = [{"date": d, "count": new_users_by_day.get(d, 0)} for d in dates]

    top_company_rows = (
        db.query(AppDataRecord.company_id, func.count(AppDataRecord.id).label("cnt"))
        .filter(*base_filter)
        .group_by(AppDataRecord.company_id)
        .order_by(func.count(AppDataRecord.id).desc())
        .limit(10)
        .all()
    )
    top_company_ids = [cid for cid, _ in top_company_rows]
    companies_by_id = {c.id: c.name for c in db.query(Company).filter(Company.id.in_(top_company_ids)).all()} if top_company_ids else {}
    top_companies = [
        {"company_id": cid, "company_name": companies_by_id.get(cid, cid), "count": cnt}
        for cid, cnt in top_company_rows
    ]

    collection_rows = (
        db.query(AppDataRecord.collection, func.count(AppDataRecord.id))
        .filter(*base_filter)
        .group_by(AppDataRecord.collection)
        .all()
    )
    module_counts: dict[str, int] = {}
    for collection, cnt in collection_rows:
        module = _COLLECTION_MODULE_MAP.get(collection, "other")
        module_counts[module] = module_counts.get(module, 0) + cnt
    module_breakdown = sorted(
        ({"module": m, "count": c} for m, c in module_counts.items()),
        key=lambda x: x["count"], reverse=True,
    )

    return {
        "days": days,
        "total_records": total_records,
        "daily_trend": daily_trend,
        "new_users_trend": new_users_trend,
        "top_companies": top_companies,
        "module_breakdown": module_breakdown,
    }


# ── System health ─────────────────────────────────────────────────────────────
# No APM/monitoring tool exists in this stack, so this only reports what's
# actually checkable here: DB reachability/latency, error rate, and rough scale.

@router.get("/system-health")
def system_health(
    days: int = 7,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    days = max(1, min(days, 30))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    db_ok = True
    db_latency_ms = None
    try:
        start = datetime.now(timezone.utc)
        db.execute(text("SELECT 1"))
        db_latency_ms = round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 1)
    except Exception:
        db_ok = False

    error_rows = (
        db.query(ClientError.occurred_at)
        .filter(ClientError.occurred_at >= since)
        .all()
    )
    error_daily: dict[str, int] = {}
    for (occurred_at,) in error_rows:
        day = occurred_at.date().isoformat() if occurred_at else "unknown"
        error_daily[day] = error_daily.get(day, 0) + 1
    dates = [(datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    error_trend = [{"date": d, "count": error_daily.get(d, 0)} for d in dates]

    total_companies = db.query(func.count(Company.id)).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_records = db.query(func.count(AppDataRecord.id)).scalar() or 0

    from app.request_metrics import snapshot as _load_snapshot
    load = _load_snapshot()

    # QueuePool exposes size()/checkedout(); other pool classes (e.g. local
    # SQLite's) may not — this is a monitoring nicety, never worth a 500 if
    # the pool type doesn't support it. max_overflow read off the pool
    # itself (private attr, no public accessor exists) rather than
    # settings.db_max_overflow — database.py only passes that setting on
    # the Postgres branch, so for local SQLite it'd silently report a
    # ceiling the engine was never actually configured with.
    pool = db.get_bind().pool
    db_pool = None
    try:
        max_overflow = getattr(pool, "_max_overflow", 0)
        db_pool = {
            "checked_out": pool.checkedout(),
            "capacity": pool.size() + max_overflow,
        }
    except Exception:
        pass

    return {
        "db_ok": db_ok,
        "db_latency_ms": db_latency_ms,
        "error_trend": error_trend,
        "total_errors": len(error_rows),
        "scale": {
            "companies": total_companies,
            "users": total_users,
            "records": total_records,
        },
        "load": {
            "in_flight_requests": load["in_flight"],
            "requests_this_minute": load["requests_this_minute"],
            # False means these numbers are this one worker process only,
            # not the whole cluster — Redis isn't confirmed active in
            # production as of the 2026-08-14 scaling pass (docs/architecture.md §29.3).
            "cluster_wide": load["cluster_wide"],
        },
        "db_pool": db_pool,
    }


# ── Background job / queue health ───────────────────────────────────────────
# PostingJob is the accounting posting pipeline (queued -> processing ->
# posted, or failed — see module_integration.py/accounting_posting.py); Job
# is the generic async-task queue (currently just vat_summary — worker.py).
# Neither had any platform-wide visibility before this — a company's own
# Exception Center already surfaces ITS failed PostingJobs, but there was no
# way for a superadmin to see whether the worker is falling behind overall.

@router.get("/job-health")
def job_health(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    posting_counts = dict(
        db.query(PostingJob.status, func.count(PostingJob.id)).group_by(PostingJob.status).all()
    )
    job_counts = dict(
        db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )

    recent_failed = (
        db.query(PostingJob)
        .filter(PostingJob.status == "failed")
        .order_by(PostingJob.updated_at.desc())
        .limit(20)
        .all()
    )
    company_ids = {j.company_id for j in recent_failed}
    companies_by_id = {c.id: c.name for c in db.query(Company).filter(Company.id.in_(company_ids)).all()} if company_ids else {}

    return {
        "posting_jobs": {
            "queued": posting_counts.get("queued", 0),
            "processing": posting_counts.get("processing", 0),
            "posted": posting_counts.get("posted", 0),
            "failed": posting_counts.get("failed", 0),
        },
        "background_jobs": {
            "queued": job_counts.get(JobStatus.queued.value, 0),
            "running": job_counts.get(JobStatus.running.value, 0),
            "completed": job_counts.get(JobStatus.completed.value, 0),
            "failed": job_counts.get(JobStatus.failed.value, 0),
        },
        "recent_failed_postings": [
            {
                "id": j.id,
                "company_id": j.company_id,
                "company_name": companies_by_id.get(j.company_id, j.company_id),
                "retry_count": j.retry_count,
                "error_message": j.error_message,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            }
            for j in recent_failed
        ],
    }


# ── Impersonation ─────────────────────────────────────────────────────────────
# Superadmin can act as a company's admin for support. Every session start/end
# is written to the same AppDataRecord "audit" collection the Audit Log panel
# already reads, so it shows up there automatically — no new table needed.

def _write_audit_blob(db: Session, company_id: str, user_label: str, action: str, record: str, result: str) -> None:
    from app.models import uuid as _uuid
    entry = {
        "time": datetime.now(timezone.utc).strftime("%d/%m/%Y, %H:%M"),
        "user": user_label,
        "action": action,
        "record": record,
        "result": result,
    }
    db.add(AppDataRecord(
        id=_uuid(),
        company_id=company_id,
        collection="audit",
        record_key=None,
        payload=json.dumps(entry),
    ))


class ImpersonateIn(BaseModel):
    # Optional — omitted keeps the original "oldest active admin" default so
    # existing callers/behavior don't change.
    user_id: str | None = None
    # Impersonate a Branch Login identity instead of a company admin user —
    # mutually exclusive with user_id (branch_id wins if both are somehow
    # sent). See impersonate_company() below.
    branch_id: str | None = None


# Same value as branches.py's own _BRANCH_PREFIX / auth_principal.py's
# _BRANCH_PREFIX — duplicated rather than imported (matching how
# auth_principal.py already duplicates it independently of branches.py)
# to avoid a reverse import into a router module for one constant.
_BRANCH_TOKEN_PREFIX = "branch:"


@router.post("/companies/{company_id}/impersonate")
@limiter.limit("20/minute")
def impersonate_company(
    request: Request,
    company_id: str,
    body: ImpersonateIn = ImpersonateIn(),
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if body.branch_id:
        # Impersonate a Branch Login identity instead of a company admin
        # user — lands the superadmin on the branch-scoped main dashboard
        # (Dashboard branch-scoping, reports.py) exactly as that branch
        # would see it, not the company-wide admin view.
        branch = db.query(Branch).filter(Branch.id == body.branch_id, Branch.company_id == company_id).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found in this company")
        _write_audit_blob(
            db, company_id, f"Super Admin ({superadmin.email})",
            "Impersonation started", f"as branch {branch.name}", "Started",
        )
        token = create_access_token(_BRANCH_TOKEN_PREFIX + branch.id, impersonated_by=superadmin.id)
        revocation = impersonation_revocation_info(token)
        if revocation:
            jti, _ttl = revocation
            db.add(ImpersonationSession(
                superadmin_id=superadmin.id,
                target_branch_id=branch.id,
                company_id=company_id,
                token_jti=jti,
            ))
        db.commit()
        return {
            "ok": True,
            "access_token": token,
            "user": None,
            "branch": {"id": branch.id, "name": branch.name},
            "company": {"id": company.id, "name": company.name},
        }

    if body.user_id:
        # Previously always the company's oldest active admin — no way to
        # target a specific user for support/debugging a non-admin's view.
        target = (
            db.query(User)
            .filter(User.id == body.user_id, User.company_id == company_id, User.is_active == True)  # noqa: E712
            .first()
        )
        if not target:
            raise HTTPException(status_code=404, detail="User not found in this company, or inactive")
    else:
        target = (
            db.query(User)
            .filter(User.company_id == company_id, User.role == "admin", User.is_active == True)  # noqa: E712
            .order_by(User.created_at.asc())
            .first()
        )
        if not target:
            raise HTTPException(status_code=400, detail="This company has no active admin user to impersonate")

    _write_audit_blob(
        db, company_id, f"Super Admin ({superadmin.email})",
        "Impersonation started", f"as {target.full_name} ({target.email})", "Started",
    )

    token = create_access_token(target.id, impersonated_by=superadmin.id)
    revocation = impersonation_revocation_info(token)
    if revocation:
        jti, _ttl = revocation
        db.add(ImpersonationSession(
            superadmin_id=superadmin.id,
            target_user_id=target.id,
            company_id=company_id,
            token_jti=jti,
        ))
    db.commit()

    return {
        "ok": True,
        "access_token": token,
        "user": {"id": target.id, "email": target.email, "full_name": target.full_name},
        "branch": None,
        "company": {"id": company.id, "name": company.name},
    }


@router.post("/end-impersonation")
def end_impersonation(
    request: Request,
    db: Session = Depends(get_db),
    # Principal, not User — a branch-impersonation session's active token is
    # a Branch Login identity (see impersonate_company()), which
    # get_current_user (User-only) would simply 401 on, making this endpoint
    # uncallable from exactly the session it needs to end.
    principal: Principal = Depends(get_current_principal),
):
    auth = request.headers.get("authorization", "")
    raw_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    impersonator_id = impersonator_id_from_token(raw_token) if raw_token else None
    if not impersonator_id:
        raise HTTPException(status_code=400, detail="Current session is not an impersonation session")
    # Actually invalidate this token (not just log it as ended) — otherwise it
    # keeps working as the impersonated company's admin until it naturally
    # expires, up to access_token_expire_minutes later.
    revocation = impersonation_revocation_info(raw_token)
    if revocation:
        jti, ttl_seconds = revocation
        revoke_impersonation_jti(jti, ttl_seconds)
        session_row = db.query(ImpersonationSession).filter(ImpersonationSession.token_jti == jti).first()
        if session_row and not session_row.ended_at:
            session_row.ended_at = datetime.now(timezone.utc)
    impersonator = db.query(User).filter(User.id == impersonator_id).first()
    viewed_as = f"{principal.user.full_name} ({principal.user.email})" if principal.user else principal.display_name
    # "Super Admin" only when the impersonator actually is one -- this same
    # flow now also ends a company User's own branches.py
    # impersonate_branch() session (see that endpoint's docstring), where
    # labeling a company's own admin "Super Admin" in their audit log would
    # misrepresent who actually did it.
    impersonator_label = (
        f"Super Admin ({impersonator.email})" if impersonator and impersonator.role == "superadmin"
        else f"{impersonator.full_name} ({impersonator.email})" if impersonator
        else impersonator_id
    )
    _write_audit_blob(
        db, principal.company_id, impersonator_label,
        "Impersonation ended", f"was viewing as {viewed_as}", "Ended",
    )
    db.commit()
    return {"ok": True}


@router.get("/impersonation-sessions")
def list_impersonation_sessions(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    q = db.query(ImpersonationSession).order_by(ImpersonationSession.created_at.desc())
    if active_only:
        q = q.filter(ImpersonationSession.ended_at.is_(None))
    rows = q.limit(100).all()

    user_ids = {r.superadmin_id for r in rows} | {r.target_user_id for r in rows if r.target_user_id}
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    branch_ids = {r.target_branch_id for r in rows if r.target_branch_id}
    branches_by_id = {b.id: b.name for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}
    company_ids = {r.company_id for r in rows}
    companies_by_id = {c.id: c.name for c in db.query(Company).filter(Company.id.in_(company_ids)).all()} if company_ids else {}

    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        superadmin_u = users_by_id.get(r.superadmin_id)
        target_u = users_by_id.get(r.target_user_id) if r.target_user_id else None
        is_branch = bool(r.target_branch_id)
        # Sessions naturally expire with the token (access_token_expire_minutes)
        # even if "End Impersonation" was never clicked — surfaced so a stale
        # row doesn't read as "still active" forever.
        started_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
        expires_at = started_at + timedelta(minutes=settings.access_token_expire_minutes)
        result.append({
            "id": r.id,
            "superadmin_email": superadmin_u.email if superadmin_u else r.superadmin_id,
            "target_kind": "branch" if is_branch else "user",
            "target_user_email": (target_u.email if target_u else r.target_user_id) if not is_branch else None,
            "target_user_name": (target_u.full_name if target_u else None) if not is_branch else branches_by_id.get(r.target_branch_id, r.target_branch_id),
            "company_id": r.company_id,
            "company_name": companies_by_id.get(r.company_id, r.company_id),
            "started_at": r.created_at.isoformat() if r.created_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "naturally_expired": r.ended_at is None and expires_at <= now,
        })
    return result


@router.post("/impersonation-sessions/{session_id}/force-end")
def force_end_impersonation_session(
    session_id: str,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    row = db.query(ImpersonationSession).filter(ImpersonationSession.id == session_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row.ended_at:
        revoke_impersonation_jti(row.token_jti, settings.access_token_expire_minutes * 60)
        row.ended_at = datetime.now(timezone.utc)
        _write_audit_blob(
            db, row.company_id, f"Super Admin ({superadmin.email})",
            "Impersonation force-ended", f"session {session_id}", "Ended",
        )
        db.commit()
    return {"ok": True}


@router.get("/trial-requests")
def list_trial_requests(db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    rows = db.query(TrialRequest).order_by(TrialRequest.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "full_name": r.full_name,
            "company_name": r.company_name,
            "email": r.email,
            "phone": r.phone,
            "employee_count": r.employee_count,
            "interest": r.interest,
            "notes": r.notes,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


class TrialStatusIn(BaseModel):
    status: str


@router.patch("/trial-requests/{request_id}")
def update_trial_status(request_id: str, body: TrialStatusIn, db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    row = db.query(TrialRequest).filter(TrialRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = body.status
    db.commit()
    return {"ok": True}


@router.delete("/trial-requests/{request_id}", status_code=204)
def delete_trial_request(request_id: str, db: Session = Depends(get_db), _: User = Depends(_require_superadmin)):
    # No _write_audit_blob() call here — that writes an AppDataRecord row
    # with a company_id FK to companies.id, and a trial request is a
    # pre-signup inbound lead with no company of its own to attach one to.
    row = db.query(TrialRequest).filter(TrialRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()


# ── Self-service account ────────────────────────────────────────────────────
# Deliberately scoped to "change my own password" only — managing OTHER
# superadmin accounts (who can create/remove one) is a bigger access-control
# decision than a dashboard-polish pass should make unprompted.

class ChangeMyPasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


@router.post("/change-my-password")
@limiter.limit("10/hour")
def change_my_password(
    request: Request,
    body: ChangeMyPasswordIn,
    db: Session = Depends(get_db),
    superadmin: User = Depends(_require_superadmin),
):
    if not superadmin.password_hash or not verify_password(body.current_password, superadmin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    superadmin.password_hash = hash_password(body.new_password)
    _write_audit_blob(db, superadmin.company_id, superadmin.email, "change_own_password", superadmin.email, "Done")
    db.commit()
    return {"ok": True}

