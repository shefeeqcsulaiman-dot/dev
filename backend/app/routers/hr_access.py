"""RBAC, company geofencing, and GPS attendance for HRMS.

Extends the employee identity already used by app/routers/ess.py rather than
building a second auth system: tokens issued here use the same "emp:" JWT
subject prefix and secret key, so an /hr/login token also works against
/ess/* routes and vice versa. See docs/hrms-architecture.md for the design.
"""

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth_principal import (
    Principal,
    _role_department_scope,
    _role_permission_keys,
    get_current_employee,
    require_permission,
    require_principal_permission,
)
from app.config import get_settings
from app.database import get_db
from app.department_scope import (
    assert_employee_in_names_scope, assert_employee_in_scope, employee_scope, scope_employee_query,
    scope_employee_query_by_names, scoped_employee_id_select,
)
from app.dependencies import assert_company_active, company_allows_module, require_module
from app.module_catalog import ALL_MODULES
from app.limiter import limiter
from app.models import (
    AttendanceSession,
    Branch,
    Company,
    CompanyLocation,
    Employee,
    EmployeeBranchAccess,
    EmployeeLocation,
    EmployeeLocationLog,
    Permission,
    Role,
    RolePermission,
)
from app.security import pwd_context

# /login and /logout stay on the ungated `router` (issuing/discarding a token
# can't itself require a module check — there's no principal yet); every
# other HRMS-admin/employee endpoint here requires the "hrms" module,
# checked server-side (not just hidden in the sidebar — see require_module).
router = APIRouter(prefix="/hr", tags=["hr-access"])
gated_router = APIRouter(prefix="/hr", tags=["hr-access"], dependencies=[Depends(require_module("hrms"))])
settings = get_settings()

_EMP_PREFIX = "emp:"
_AUTO_CHECKOUT_GRACE_MINUTES = 5

# ── default RBAC catalog ───────────────────────────────────────────────────

_PERMISSION_CATALOG: dict[str, list[str]] = {
    "hr": ["manage_roles", "manage_locations", "manage_employees", "view_all_attendance"],
    # "view"/"edit" merged into the pre-existing payroll/attendance/dashboard
    # groups (rather than a separate "module_payroll" etc.) so the Add
    # Custom Role modal shows one "Payroll" section, not two.
    "payroll": ["run_payroll", "view_payroll", "view", "edit"],
    "attendance": ["check_in_out", "view_own_attendance", "view", "edit", "view_all_branches"],
    "dashboard": ["admin", "hr", "payroll", "manager", "employee", "view"],
    # Module-level view/edit/delete matrix backing the full HRMS sidebar —
    # see docs/hrms-architecture.md. "delete" is omitted for modules with no
    # delete action today (attendance, payroll) rather than adding a
    # misleading checkbox with nothing behind it. Performance/Training/Assets
    # share one "performance" key since they're one destination page
    # (go('hrms-ext')) in the sidebar today.
    # "view_salary" is a field-level add-on to "view", not a separate
    # module-view gate — a role can browse the Employee Directory
    # (employees:view) without seeing basic_salary/allowances unless this
    # is also granted. See app_data.py's _redact_employee_salary().
    "employees": ["view", "edit", "delete", "view_salary"],
    "rota": ["view", "edit", "delete"],
    "leave": ["view", "edit", "delete"],
    "overtime": ["view", "edit", "delete"],
    "loans": ["view", "edit", "delete"],
    "recruitment": ["view", "edit", "delete"],
    "performance": ["view", "edit", "delete"],
    "hr_workflow": ["view", "edit", "delete"],
    "reports": ["view", "view_all_branches"],
    "ai_insights": ["view"],
    "hr_settings": ["view", "edit", "delete"],
    # Main-dashboard (index.html) modules — Branch Management "Main Dashboard
    # Access" phase. "view" only: nothing in this phase backs an edit/delete
    # action for these modules yet (the corresponding accounting.py/etc.
    # write endpoints are still admin-only, a deliberately separate later
    # phase), so no edit/delete checkbox is added here, matching this
    # catalog's own convention of not offering a checkbox with nothing
    # behind it. Names reused verbatim from _COLLECTION_MODULE (app_data.py)
    # and the index.html sidebar's own data-module attribute values, so one
    # "module" vocabulary threads through the company-level Module
    # Permissions gate, the bootstrap collection allowlist, and this
    # per-role permission gate instead of three parallel naming schemes.
    "sales": ["view", "view_all_branches"],
    "quotations": ["view"],
    "pos": ["view", "view_all_branches"],
    "purchase": ["view", "view_all_branches"],
    "inventory": ["view", "view_all_branches"],
    "expense": ["view"],
    "bank": ["view"],
    "accounting": ["view", "view_all_branches"],
    "corporate": ["view"],
    "notifications": ["view"],
    "expert": ["view"],
}

_DEFAULT_ROLES: dict[str, list[str]] = {
    "Administrator": ["*"],  # all permissions
    "HR Manager": [
        "hr:manage_roles", "hr:manage_locations", "hr:manage_employees", "hr:view_all_attendance",
        "attendance:check_in_out", "attendance:view_own_attendance", "attendance:view", "attendance:edit",
        "dashboard:hr", "dashboard:view",
        "employees:view", "employees:edit", "employees:delete",
        "leave:view", "leave:edit", "leave:delete",
        "rota:view", "rota:edit", "rota:delete",
        "hr_settings:view", "hr_settings:edit",
    ],
    "Payroll Officer": [
        "payroll:run_payroll", "payroll:view_payroll", "payroll:view", "payroll:edit",
        "attendance:check_in_out", "attendance:view_own_attendance",
        "dashboard:payroll", "dashboard:view",
    ],
    "Manager": [
        "hr:view_all_attendance", "attendance:check_in_out", "attendance:view_own_attendance", "attendance:view",
        "dashboard:manager", "dashboard:view",
        "employees:view", "leave:view", "leave:edit", "rota:view",
    ],
    "Employee": [
        "attendance:check_in_out", "attendance:view_own_attendance",
        "dashboard:employee", "dashboard:view",
    ],
}


def _ensure_permission_catalog(db: Session) -> dict[str, Permission]:
    existing = {f"{p.module}:{p.permission_name}": p for p in db.query(Permission).all()}
    for module, names in _PERMISSION_CATALOG.items():
        for name in names:
            key = f"{module}:{name}"
            if key not in existing:
                perm = Permission(module=module, permission_name=name)
                db.add(perm)
                db.flush()
                existing[key] = perm
    return existing


def _company_allowed_catalog_keys(catalog: dict[str, Permission], company_modules_json: str | None) -> set[str]:
    """Branch Login Phase 1: a permission key is offerable/grantable only if
    its module has no company-level module-enablement concept at all (the
    HR-suite keys — hr, payroll, attendance, dashboard, employees, rota,
    leave, overtime, loans, recruitment, performance, hr_workflow,
    hr_settings, ai_insights — none of these appear in ALL_MODULES; they're
    all covered collectively by the single "hrms" company module via
    gated_router's own require_module("hrms")) or the company has that
    module enabled. Shared by admin_list_permissions (what's offered) and
    admin_create_role/admin_update_role (what's accepted)."""
    return {
        key for key, perm in catalog.items()
        if perm.module not in ALL_MODULES or company_allows_module(company_modules_json, perm.module)
    }


def _ensure_default_roles(db: Session, company_id: str) -> dict[str, Role]:
    """Idempotently seeds the default role set for a company on first use."""
    catalog = _ensure_permission_catalog(db)
    roles = {r.role_name: r for r in db.query(Role).filter(Role.company_id == company_id).all()}
    changed = False
    for role_name, perm_keys in _DEFAULT_ROLES.items():
        role = roles.get(role_name)
        if not role:
            role = Role(company_id=company_id, role_name=role_name, is_system_role=True)
            db.add(role)
            db.flush()
            roles[role_name] = role
            changed = True
        existing_links = {
            rp.permission_id for rp in db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        }
        # "*" (Administrator) picks up every ordinary permission key, but
        # NEVER the cross-branch opt-ins (Branch Security Layer Phase 2) —
        # those are a privilege escalation (company-wide visibility for a
        # branch-assigned identity), not an ordinary view/edit permission,
        # and must be granted explicitly per role, never implied by a
        # wildcard. Without this, every branch's "Administrator" role
        # (branch-manager-equivalent, not the same as a full company admin)
        # would silently see every OTHER branch's data too.
        if perm_keys == ["*"]:
            grant_keys = [k for k in catalog.keys() if not k.endswith(":view_all_branches")]
        else:
            grant_keys = perm_keys
        for key in grant_keys:
            perm = catalog.get(key)
            if perm and perm.id not in existing_links:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
                changed = True
    if changed:
        db.commit()
    return roles


# ── geofencing ──────────────────────────────────────────────────────────────

def _distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance between two lat/long points, in meters."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _scope_attendance_to_branch(query, emp: Employee, db: Session, requested_branch_id: str | None = None):
    """Branch scoping (below) plus department scoping: a login whose role has
    departments ticked only sees open sessions of employees in those departments."""
    query = _scope_attendance_to_branch_only(query, emp, db, requested_branch_id)
    scope = employee_scope(db, emp)
    if scope:
        query = query.filter(AttendanceSession.employee_id.in_(scoped_employee_id_select(emp.company_id, scope)))
    return query


def _scope_attendance_to_branch_only(query, emp: Employee, db: Session, requested_branch_id: str | None = None):
    """Branch Management, Phase 2: a branch-assigned employee viewing
    team/company-wide attendance (dashboard counts, live locations) only
    sees sessions belonging to their own branch — plus branch-less legacy
    sessions, so pre-existing data stays visible rather than vanishing.
    An employee with no branch_id (the common case pre-feature, and for
    companies that never set up branches) sees everything, unchanged from
    today's behavior. Branch Security Layer Phase 2: "attendance:view_all_
    branches" opts a specific branch employee out of this filter, same as
    every other cross-branch flag — checked here (not via a Principal,
    since this file works directly with Employee rows) by resolving the
    role's permission keys the same way require_permission() does. Phase 3:
    `requested_branch_id` (an explicit ?branch_id= choice) is honored only
    if it's one of this employee's own accessible branches (primary +
    EmployeeBranchAccess rows) — same "cannot escalate" guarantee as every
    other branch-filtered endpoint."""
    if not emp.branch_id:
        return query
    role = db.get(Role, emp.role_id) if emp.role_id else None
    if "attendance:view_all_branches" in _role_permission_keys(db, role):
        if requested_branch_id:
            return query.filter(AttendanceSession.branch_id == requested_branch_id)
        return query
    accessible = {row[0] for row in db.query(EmployeeBranchAccess.branch_id).filter(EmployeeBranchAccess.employee_id == emp.id).all()}
    accessible.add(emp.branch_id)
    active_branch = requested_branch_id if (requested_branch_id and requested_branch_id in accessible) else emp.branch_id
    return query.filter(
        (AttendanceSession.branch_id == active_branch) | (AttendanceSession.branch_id.is_(None))
    )


def _nearest_assigned_location(db: Session, employee_id: str) -> tuple[CompanyLocation, bool] | None:
    """Returns (location, is_primary) for the employee's primary assigned location, or None."""
    link = (
        db.query(EmployeeLocation)
        .filter(EmployeeLocation.employee_id == employee_id)
        .order_by(EmployeeLocation.is_primary.desc())
        .first()
    )
    if not link:
        return None
    loc = db.get(CompanyLocation, link.location_id)
    if not loc:
        return None
    return loc, link.is_primary


# ── auth ─────────────────────────────────────────────────────────────────────

class HrLoginRequest(BaseModel):
    username: str
    password: str
    # Optional — see ess.py's EssLoginRequest.company_id for why: portal
    # usernames are globally unique (uq_employees_username), so a
    # username-based login can resolve the company without it. Only
    # employee_no-based login (not unique platform-wide) requires it.
    company_id: str | None = None


class HrToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role_name: str | None = None


class GeoPoint(BaseModel):
    latitude: float
    longitude: float
    accuracy: float | None = None
    device: str | None = None
    battery: int | None = None


def _create_employee_token(employee_id: str, role_id: str | None) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict = {"sub": _EMP_PREFIX + employee_id, "exp": exp}
    if role_id:
        payload["rid"] = role_id
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


@router.post("/login", response_model=HrToken)
@limiter.limit("10/minute")
def hr_login(request: Request, payload: HrLoginRequest, db: Session = Depends(get_db)) -> HrToken:
    company_id = (payload.company_id or "").strip()
    username = payload.username.strip()

    if company_id:
        emp = (
            db.query(Employee)
            .filter(
                Employee.company_id == company_id,
                (Employee.username.ilike(username)) | (Employee.employee_no.ilike(username)),
            )
            .first()
        )
    else:
        # No company reference — resolve by the globally-unique portal
        # username only (employee_no is not unique platform-wide).
        emp = db.query(Employee).filter(Employee.username.ilike(username)).first()

    if not emp:
        pwd_context.verify(payload.password, "$2b$12$Z2HUw9SswHis7rcngsd7iOdXn/b9HafcmcwJx9D39ozeKwrSy22r.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")

    stored_hash = emp.password_hash
    if not stored_hash:
        if payload.password != emp.employee_no:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")
    elif not pwd_context.verify(payload.password, stored_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")

    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == emp.company_id).scalar()
    assert_company_active(expires_at)

    roles = _ensure_default_roles(db, emp.company_id)
    if not emp.role_id:
        emp.role_id = roles["Employee"].id

    now = datetime.now(UTC)
    emp.last_login = now
    emp.last_activity = now
    db.add(emp)
    db.commit()

    role = db.get(Role, emp.role_id)
    return HrToken(access_token=_create_employee_token(emp.id, emp.role_id), role_name=role.role_name if role else None)


@router.post("/logout")
def hr_logout(emp: Employee = Depends(get_current_employee)) -> dict:
    # Tokens are stateless JWTs with no server-side revocation list (same as
    # ess.py) — logout is a client-side action; this endpoint exists so
    # clients have a symmetric call and so a future revocation list has a
    # natural place to hook in.
    return {"ok": True}


class HrMeOut(BaseModel):
    id: str
    employee_no: str
    full_name: str
    department: str
    designation: str
    role_name: str | None = None
    permissions: list[str] = []
    work_location_id: str | None = None
    # Non-empty -> GET /ess/team is available for this employee (see there).
    department_scope: list[str] = []


@gated_router.get("/me", response_model=HrMeOut)
def hr_me(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> HrMeOut:
    role = db.get(Role, emp.role_id) if emp.role_id else None
    perms = sorted(_role_permission_keys(db, role))
    return HrMeOut(
        id=emp.id,
        employee_no=emp.employee_no,
        full_name=emp.full_name,
        department=emp.department,
        designation=emp.designation,
        role_name=role.role_name if role else None,
        permissions=perms,
        work_location_id=emp.work_location_id,
        department_scope=_role_department_scope(role),
    )


# ── role-based dashboard ────────────────────────────────────────────────────

@gated_router.get("/dashboard")
def hr_dashboard(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> dict:
    role = db.get(Role, emp.role_id) if emp.role_id else None
    role_name = role.role_name if role else "Employee"
    company_id = emp.company_id

    if role_name in ("Administrator", "HR Manager"):
        return {
            "role": role_name,
            "total_employees": scope_employee_query_by_names(
                db.query(Employee).filter(Employee.company_id == company_id), employee_scope(db, emp),
            ).count(),
            "active_sessions_now": _scope_attendance_to_branch(
                db.query(AttendanceSession).filter(
                    AttendanceSession.company_id == company_id, AttendanceSession.status == "open"
                ),
                emp, db,
            ).count(),
            "company_locations": db.query(CompanyLocation).filter(CompanyLocation.company_id == company_id).count(),
            "roles_configured": db.query(Role).filter(Role.company_id == company_id).count(),
        }
    if role_name == "Payroll Officer":
        from app.models import PayrollRun
        latest = (
            db.query(PayrollRun)
            .filter(PayrollRun.company_id == company_id)
            .order_by(PayrollRun.period.desc())
            .first()
        )
        net_total = float(latest.net_total) if latest else 0
        scope = employee_scope(db, emp)
        if latest and scope:
            # department-scoped: only the in-scope payslip lines, not the company total
            from app.models import PayrollItem
            in_scope = scoped_employee_id_select(company_id, scope)
            net_total = float(sum(
                (i.net_pay for i in db.query(PayrollItem).filter(PayrollItem.run_id == latest.id, PayrollItem.employee_id.in_(in_scope)).all()), 0,
            ))
        return {
            "role": role_name,
            "latest_run_period": latest.period if latest else None,
            "latest_run_status": latest.status if latest else None,
            "latest_run_net_total": net_total,
        }
    if role_name == "Manager":
        return {
            "role": role_name,
            "team_active_sessions": _scope_attendance_to_branch(
                db.query(AttendanceSession).filter(
                    AttendanceSession.company_id == company_id, AttendanceSession.status == "open"
                ),
                emp, db,
            ).count(),
        }

    # Employee dashboard — own status only
    open_session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    return {
        "role": role_name,
        "checked_in": bool(open_session),
        "check_in_time": str(open_session.check_in) if open_session else None,
    }


# ── roles & permissions ─────────────────────────────────────────────────────

class RoleOut(BaseModel):
    id: str
    role_name: str
    description: str | None = None
    is_system_role: bool
    permissions: list[str] = []
    # Non-empty means this role's holder, once granted ESS Portal Access,
    # sees the employee roster for these departments -- see GET /ess/team.
    department_scope: list[str] = []


class RoleCreateRequest(BaseModel):
    role_name: str
    description: str | None = None
    permission_keys: list[str] = []
    department_scope: list[str] = []


def _normalize_department_scope(names: list[str]) -> str | None:
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()})
    return json.dumps(cleaned) if cleaned else None


@gated_router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> list[RoleOut]:
    _ensure_default_roles(db, emp.company_id)
    roles = db.query(Role).filter(Role.company_id == emp.company_id).order_by(Role.role_name).all()
    return [
        RoleOut(
            id=r.id, role_name=r.role_name, description=r.description, is_system_role=r.is_system_role,
            permissions=sorted(_role_permission_keys(db, r)),
            department_scope=_role_department_scope(r),
        )
        for r in roles
    ]


@gated_router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(
    payload: RoleCreateRequest,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_roles")),
) -> RoleOut:
    catalog = _ensure_permission_catalog(db)
    role = Role(
        company_id=emp.company_id, role_name=payload.role_name.strip(), description=payload.description,
        department_scope=_normalize_department_scope(payload.department_scope),
    )
    db.add(role)
    db.flush()
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()
    return RoleOut(
        id=role.id, role_name=role.role_name, description=role.description, is_system_role=False,
        permissions=sorted(_role_permission_keys(db, role)),
        department_scope=_role_department_scope(role),
    )


@gated_router.get("/permissions")
def list_permissions(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> list[dict]:
    catalog = _ensure_permission_catalog(db)
    db.commit()
    return [{"key": key, "module": p.module, "permission_name": p.permission_name} for key, p in catalog.items()]


# ── admin-managed portal access (HRMS "Users & Roles" screen) ──────────────
# These routes authenticate as the company's own TaxFlow admin User (the same
# login already used to reach hrms.html), not as an Employee. They exist so
# an HR admin can grant an employee a username/password/role from inside
# HRMS without first needing an employee-RBAC login of their own — that
# employee-RBAC login (see /hr/login above) is still what the resulting
# credentials are checked against everywhere else (ESS, GPS check-in, etc).

class AdminEmployeePortalOut(BaseModel):
    id: str
    employee_no: str
    full_name: str
    department: str
    username: str | None = None
    role_id: str | None = None
    role_name: str | None = None
    is_active: bool
    has_password: bool
    # The assigned role's department scope, if any -- once this employee has
    # a portal username, this is exactly what they'll see in ESS's Team tab
    # (GET /ess/team). Surfaced here so Users & Roles shows it up front,
    # without an admin having to cross-reference the Roles & Permissions tab.
    department_scope: list[str] = []


@gated_router.get("/admin/permissions")
def admin_list_permissions(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:view")),
) -> list[dict]:
    catalog = _ensure_permission_catalog(db)
    db.commit()
    company_modules = db.query(Company.modules_enabled).filter(Company.id == principal.company_id).scalar()
    allowed_keys = _company_allowed_catalog_keys(catalog, company_modules)
    return [
        {"key": key, "module": p.module, "permission_name": p.permission_name}
        for key, p in catalog.items() if key in allowed_keys
    ]


@gated_router.get("/admin/roles", response_model=list[RoleOut])
def admin_list_roles(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:view")),
) -> list[RoleOut]:
    _ensure_default_roles(db, principal.company_id)
    roles = db.query(Role).filter(Role.company_id == principal.company_id).order_by(Role.role_name).all()
    return [
        RoleOut(
            id=r.id, role_name=r.role_name, description=r.description, is_system_role=r.is_system_role,
            permissions=sorted(_role_permission_keys(db, r)),
            department_scope=_role_department_scope(r),
        )
        for r in roles
    ]


@gated_router.post("/admin/roles", response_model=RoleOut, status_code=201)
def admin_create_role(
    payload: RoleCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> RoleOut:
    catalog = _ensure_permission_catalog(db)
    # Branch Login Phase 1: reject a permission key for a module the
    # company hasn't enabled, before any Role row is created — atomic, no
    # partial role left behind on failure.
    company_modules = db.query(Company.modules_enabled).filter(Company.id == principal.company_id).scalar()
    allowed_keys = _company_allowed_catalog_keys(catalog, company_modules)
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm and key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"'{perm.module}' is not enabled for your company — cannot grant this permission")
    role_name = payload.role_name.strip()
    if not role_name:
        raise HTTPException(status_code=400, detail="Role name is required")
    if db.query(Role).filter(Role.company_id == principal.company_id, Role.role_name.ilike(role_name)).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists")
    role = Role(
        company_id=principal.company_id, role_name=role_name, description=payload.description,
        department_scope=_normalize_department_scope(payload.department_scope),
    )
    db.add(role)
    db.flush()
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()
    return RoleOut(
        id=role.id, role_name=role.role_name, description=role.description, is_system_role=False,
        permissions=sorted(_role_permission_keys(db, role)),
        department_scope=_role_department_scope(role),
    )


@gated_router.put("/admin/roles/{role_id}", response_model=RoleOut)
def admin_update_role(
    role_id: str,
    payload: RoleCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> RoleOut:
    role = db.query(Role).filter(Role.id == role_id, Role.company_id == principal.company_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system_role:
        raise HTTPException(status_code=400, detail="Default system roles cannot be edited — create a custom role instead")
    catalog = _ensure_permission_catalog(db)
    company_modules = db.query(Company.modules_enabled).filter(Company.id == principal.company_id).scalar()
    allowed_keys = _company_allowed_catalog_keys(catalog, company_modules)
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm and key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"'{perm.module}' is not enabled for your company — cannot grant this permission")
    role_name = payload.role_name.strip()
    if not role_name:
        raise HTTPException(status_code=400, detail="Role name is required")
    if db.query(Role).filter(Role.company_id == principal.company_id, Role.role_name.ilike(role_name), Role.id != role.id).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists")
    role.role_name = role_name
    role.description = payload.description
    role.department_scope = _normalize_department_scope(payload.department_scope)
    db.add(role)
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()
    return RoleOut(
        id=role.id, role_name=role.role_name, description=role.description, is_system_role=False,
        permissions=sorted(_role_permission_keys(db, role)),
        department_scope=_role_department_scope(role),
    )


@gated_router.delete("/admin/roles/{role_id}", status_code=204, response_model=None)
def admin_delete_role(
    role_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:delete")),
) -> None:
    role = db.query(Role).filter(Role.id == role_id, Role.company_id == principal.company_id).first()
    if not role:
        return
    if role.is_system_role:
        raise HTTPException(status_code=400, detail="Default system roles cannot be deleted")
    if db.query(Employee).filter(Employee.role_id == role.id).first():
        raise HTTPException(status_code=409, detail="This role is still assigned to one or more employees — reassign them first")
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    db.delete(role)
    db.commit()


@gated_router.get("/admin/employees", response_model=list[AdminEmployeePortalOut])
def admin_list_employee_portal_access(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:view", "employees:view")),
) -> list[AdminEmployeePortalOut]:
    # Inactive employees with no portal access don't belong in a "grant
    # access" list (same "don't offer inactive staff in add/select surfaces"
    # rule already applied to the employee pickers elsewhere) -- but an
    # inactive employee who was ALREADY granted a login must stay visible
    # here, or there'd be no way left in the UI to revoke it, leaving a
    # terminated employee's ESS credentials silently active forever.
    employees = (
        db.query(Employee)
        .filter(
            Employee.company_id == principal.company_id,
            or_(Employee.status == "active", Employee.username.isnot(None)),
        )
        .order_by(Employee.employee_no)
    )
    employees = scope_employee_query(employees, principal).all()  # department-scoped role: only its departments
    role_ids = {e.role_id for e in employees if e.role_id}
    roles_by_id = {r.id: r for r in db.query(Role).filter(Role.id.in_(role_ids)).all()} if role_ids else {}
    return [
        AdminEmployeePortalOut(
            id=e.id, employee_no=e.employee_no, full_name=e.full_name, department=e.department,
            username=e.username, role_id=e.role_id,
            role_name=roles_by_id[e.role_id].role_name if e.role_id in roles_by_id else None,
            is_active=e.is_active, has_password=bool(e.password_hash),
            department_scope=_role_department_scope(roles_by_id.get(e.role_id)),
        )
        for e in employees
    ]


class PortalAccessIn(BaseModel):
    username: str | None = None
    password: str | None = None
    role_id: str | None = None
    is_active: bool | None = None


@gated_router.put("/admin/employees/{employee_id}/portal-access")
def set_employee_portal_access(
    employee_id: str,
    payload: PortalAccessIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> dict:
    target = db.query(Employee).filter(Employee.id == employee_id, Employee.company_id == principal.company_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, target)  # department-scoped role: only its departments

    if payload.username is not None:
        username = payload.username.strip()
        if username:
            # Global check, not scoped to this company — uq_employees_username
            # enforces uniqueness platform-wide so /ess and /hr/login can
            # resolve an employee by username alone, with no company link.
            dup = db.query(Employee).filter(Employee.username == username, Employee.id != target.id).first()
            if dup:
                raise HTTPException(status_code=409, detail="That username is already taken by another employee on this platform — choose a different one")
        target.username = username or None

    if payload.password:
        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        target.password_hash = pwd_context.hash(payload.password)
        target.password_changed_at = datetime.now(UTC)

    # role_id needs its own explicit-null-vs-omitted check ("is not None"
    # can't tell them apart, since None is itself the meaningful "unassign
    # the role" value) — model_fields_set tells us whether the client sent
    # the key at all, regardless of what value it sent.
    if "role_id" in payload.model_fields_set:
        if payload.role_id:
            role = db.query(Role).filter(Role.id == payload.role_id, Role.company_id == principal.company_id).first()
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")
        target.role_id = payload.role_id or None

    if payload.is_active is not None:
        target.is_active = payload.is_active

    db.add(target)
    db.commit()
    return {"ok": True}


@gated_router.delete("/admin/employees/{employee_id}/portal-access")
def revoke_employee_portal_access(
    employee_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:delete")),
) -> dict:
    target = db.query(Employee).filter(Employee.id == employee_id, Employee.company_id == principal.company_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, target)  # department-scoped role: only its departments
    # Clears login credentials rather than deleting the Employee record —
    # this screen manages *portal access*, the HR employee record itself
    # (attendance, payroll history, etc.) is untouched.
    target.username = None
    target.password_hash = None
    target.role_id = None
    target.is_active = False
    db.add(target)
    db.commit()
    return {"ok": True}


class BranchAccessIn(BaseModel):
    branch_ids: list[str] = []


class BranchAccessOut(BaseModel):
    id: str
    name: str
    is_primary: bool


@gated_router.get("/admin/employees/{employee_id}/branch-access", response_model=list[BranchAccessOut])
def get_employee_branch_access(
    employee_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:view")),
) -> list[dict]:
    target = db.query(Employee).filter(Employee.id == employee_id, Employee.company_id == principal.company_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, target)  # department-scoped role: only its departments
    out = []
    if target.branch_id:
        branch = db.query(Branch).filter(Branch.id == target.branch_id).first()
        if branch:
            out.append({"id": branch.id, "name": branch.name, "is_primary": True})
    extra = (
        db.query(EmployeeBranchAccess, Branch)
        .join(Branch, Branch.id == EmployeeBranchAccess.branch_id)
        .filter(EmployeeBranchAccess.employee_id == employee_id)
        .all()
    )
    out.extend({"id": b.id, "name": b.name, "is_primary": False} for _, b in extra)
    return out


@gated_router.put("/admin/employees/{employee_id}/branch-access")
def set_employee_branch_access(
    employee_id: str,
    payload: BranchAccessIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> dict:
    """Branch Security Layer Phase 3 — grants an employee ADDITIONAL
    branches beyond their primary Employee.branch_id (unchanged, still set
    separately via the employee form). Replace-all semantics, mirroring how
    portal-access sets role_id: the given branch_ids become the complete
    extra set, not an incremental add. Their primary branch is excluded
    automatically if included in the list (it's already implicitly
    accessible — no need for a redundant row)."""
    target = db.query(Employee).filter(Employee.id == employee_id, Employee.company_id == principal.company_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, target)  # department-scoped role: only its departments

    requested_ids = {b.strip() for b in payload.branch_ids if b and b.strip()}
    requested_ids.discard(target.branch_id or "")
    if requested_ids:
        valid = {
            row[0] for row in db.query(Branch.id).filter(Branch.id.in_(requested_ids), Branch.company_id == principal.company_id).all()
        }
        invalid = requested_ids - valid
        if invalid:
            raise HTTPException(status_code=404, detail="One or more branches were not found")

    db.query(EmployeeBranchAccess).filter(EmployeeBranchAccess.employee_id == employee_id).delete(synchronize_session=False)
    for branch_id in requested_ids:
        db.add(EmployeeBranchAccess(employee_id=employee_id, branch_id=branch_id))
    db.commit()
    return {"ok": True, "branch_ids": sorted(requested_ids)}


# ── company locations ───────────────────────────────────────────────────────

class CompanyLocationOut(BaseModel):
    id: str
    location_name: str
    address: str | None = None
    latitude: float
    longitude: float
    allowed_radius_meters: int
    status: str


class CompanyLocationRequest(BaseModel):
    location_name: str
    branch_id: str | None = None
    address: str | None = None
    latitude: float
    longitude: float
    allowed_radius_meters: int = 200


def _location_out(loc: CompanyLocation) -> CompanyLocationOut:
    return CompanyLocationOut(
        id=loc.id, location_name=loc.location_name, address=loc.address,
        latitude=float(loc.latitude), longitude=float(loc.longitude),
        allowed_radius_meters=loc.allowed_radius_meters, status=loc.status,
    )


@gated_router.get("/company-locations", response_model=list[CompanyLocationOut])
def list_company_locations(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> list[CompanyLocationOut]:
    query = db.query(CompanyLocation).filter(CompanyLocation.company_id == emp.company_id)
    # Same accessible-branches + NULL-stays-visible shape as
    # _scope_attendance_to_branch() — previously this returned every
    # branch's work locations to any branch-assigned employee.
    if emp.branch_id:
        accessible = {row[0] for row in db.query(EmployeeBranchAccess.branch_id).filter(EmployeeBranchAccess.employee_id == emp.id).all()}
        accessible.add(emp.branch_id)
        query = query.filter((CompanyLocation.branch_id.in_(accessible)) | (CompanyLocation.branch_id.is_(None)))
    rows = query.order_by(CompanyLocation.location_name).all()
    return [_location_out(r) for r in rows]


@gated_router.post("/company-locations", response_model=CompanyLocationOut, status_code=201)
def create_company_location(
    payload: CompanyLocationRequest,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations")),
) -> CompanyLocationOut:
    loc = CompanyLocation(
        company_id=emp.company_id, location_name=payload.location_name.strip(), branch_id=payload.branch_id,
        address=payload.address, latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
        allowed_radius_meters=payload.allowed_radius_meters,
    )
    db.add(loc)
    db.commit()
    return _location_out(loc)


@gated_router.put("/company-locations/{location_id}", response_model=CompanyLocationOut)
def update_company_location(
    location_id: str,
    payload: CompanyLocationRequest,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations")),
) -> CompanyLocationOut:
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == location_id, CompanyLocation.company_id == emp.company_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.location_name = payload.location_name.strip()
    loc.branch_id = payload.branch_id
    loc.address = payload.address
    loc.latitude = Decimal(str(payload.latitude))
    loc.longitude = Decimal(str(payload.longitude))
    loc.allowed_radius_meters = payload.allowed_radius_meters
    db.add(loc)
    db.commit()
    return _location_out(loc)


@gated_router.delete("/company-locations/{location_id}", status_code=204, response_model=None)
def delete_company_location(
    location_id: str,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations")),
) -> None:
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == location_id, CompanyLocation.company_id == emp.company_id).first()
    if loc:
        db.delete(loc)
        db.commit()


class EmployeeLocationAssignRequest(BaseModel):
    employee_id: str
    location_id: str
    is_primary: bool = True


class EmployeeLocationOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    is_primary: bool


@gated_router.get("/employee-locations", response_model=list[EmployeeLocationOut])
def list_employee_locations(
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations", "hr:manage_employees", "hr:view_all_attendance")),
) -> list[EmployeeLocationOut]:
    rows = (
        db.query(EmployeeLocation, Employee, CompanyLocation)
        .join(Employee, Employee.id == EmployeeLocation.employee_id)
        .join(CompanyLocation, CompanyLocation.id == EmployeeLocation.location_id)
        .filter(Employee.company_id == emp.company_id)
        .order_by(EmployeeLocation.is_primary.desc())
    )
    rows = scope_employee_query_by_names(rows, employee_scope(db, emp)).all()  # department-scoped role: only its departments
    return [
        EmployeeLocationOut(
            id=link.id, employee_id=e.id, employee_name=e.full_name, location_id=loc.id,
            location_name=loc.location_name, is_primary=link.is_primary,
        )
        for link, e, loc in rows
    ]


@gated_router.delete("/employee-locations/{link_id}", status_code=204, response_model=None)
def unassign_employee_location(
    link_id: str,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations", "hr:manage_employees")),
) -> None:
    link = (
        db.query(EmployeeLocation)
        .join(Employee, Employee.id == EmployeeLocation.employee_id)
        .filter(EmployeeLocation.id == link_id, Employee.company_id == emp.company_id)
        .first()
    )
    if link:
        target = db.get(Employee, link.employee_id)
        assert_employee_in_names_scope(employee_scope(db, emp), target)
        db.delete(link)
        db.flush()
        # If the deleted link was the primary, and no other assignment remains
        # primary, clear work_location_id so check-in correctly reports "no
        # location assigned" instead of pointing at a stale/removed link.
        if target and target.work_location_id == link.location_id:
            remaining = (
                db.query(EmployeeLocation)
                .filter(EmployeeLocation.employee_id == target.id, EmployeeLocation.is_primary == True)  # noqa: E712
                .first()
            )
            target.work_location_id = remaining.location_id if remaining else None
            db.add(target)
        db.commit()


@gated_router.post("/employee-locations", status_code=201)
def assign_employee_location(
    payload: EmployeeLocationAssignRequest,
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:manage_locations", "hr:manage_employees")),
) -> dict:
    target = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.company_id == emp.company_id).first()
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == payload.location_id, CompanyLocation.company_id == emp.company_id).first()
    if not target or not loc:
        raise HTTPException(status_code=404, detail="Employee or location not found")
    assert_employee_in_names_scope(employee_scope(db, emp), target)
    if payload.is_primary:
        db.query(EmployeeLocation).filter(EmployeeLocation.employee_id == target.id).update({"is_primary": False})
    link = EmployeeLocation(employee_id=target.id, location_id=loc.id, is_primary=payload.is_primary)
    db.add(link)
    target.work_location_id = loc.id
    db.add(target)
    db.commit()
    return {"ok": True}


# ── GPS attendance ───────────────────────────────────────────────────────────

class CheckInOut(BaseModel):
    session_id: str
    status: str
    check_in: str
    location_name: str | None = None
    distance_meters: float


@gated_router.post("/check-in", response_model=CheckInOut)
def check_in(
    payload: GeoPoint,
    db: Session = Depends(get_db),
    emp: Employee = Depends(get_current_employee),
) -> CheckInOut:
    existing = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already checked in")

    nearest = _nearest_assigned_location(db, emp.id)
    if not nearest:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No work location assigned")
    loc, _ = nearest
    distance = _distance_meters(payload.latitude, payload.longitude, float(loc.latitude), float(loc.longitude))
    if distance > loc.allowed_radius_meters:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Outside company location — {int(distance)}m from {loc.location_name} (allowed {loc.allowed_radius_meters}m)",
        )

    now = datetime.now(UTC)
    session = AttendanceSession(
        company_id=emp.company_id, employee_id=emp.id, location_id=loc.id, branch_id=emp.branch_id, check_in=now,
        check_in_lat=Decimal(str(payload.latitude)), check_in_lng=Decimal(str(payload.longitude)),
        status="open",
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        # The existence check above has a check-then-insert race (no lock) —
        # two near-simultaneous check-ins can both pass it. A partial unique
        # index (company_id, employee_id) WHERE status='open' (main.py
        # self-migration) is the actual guard; this turns the resulting
        # constraint violation into the same clean 409 the pre-check above
        # already returns, instead of an unhandled 500.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already checked in")
    db.add(EmployeeLocationLog(
        company_id=emp.company_id, employee_id=emp.id, session_id=session.id,
        latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
        accuracy=Decimal(str(payload.accuracy)) if payload.accuracy is not None else None,
        inside_geofence=True, device=payload.device, battery=payload.battery,
    ))
    db.commit()
    return CheckInOut(session_id=session.id, status="open", check_in=str(session.check_in), location_name=loc.location_name, distance_meters=distance)


@gated_router.post("/check-out")
def check_out(
    payload: GeoPoint | None = None,
    db: Session = Depends(get_db),
    emp: Employee = Depends(get_current_employee),
) -> dict:
    session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not checked in")
    now = datetime.now(UTC)
    session.check_out = now
    session.status = "closed"
    if payload:
        session.check_out_lat = Decimal(str(payload.latitude))
        session.check_out_lng = Decimal(str(payload.longitude))
    db.add(session)
    db.commit()
    return {"ok": True, "session_id": session.id, "check_out": str(now)}


@gated_router.post("/location")
def ping_location(
    payload: GeoPoint,
    db: Session = Depends(get_db),
    emp: Employee = Depends(get_current_employee),
) -> dict:
    session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not checked in")

    loc = db.get(CompanyLocation, session.location_id) if session.location_id else None
    inside = True
    distance = 0.0
    if loc:
        distance = _distance_meters(payload.latitude, payload.longitude, float(loc.latitude), float(loc.longitude))
        inside = distance <= loc.allowed_radius_meters

    db.add(EmployeeLocationLog(
        company_id=emp.company_id, employee_id=emp.id, session_id=session.id,
        latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
        accuracy=Decimal(str(payload.accuracy)) if payload.accuracy is not None else None,
        inside_geofence=inside, device=payload.device, battery=payload.battery,
    ))
    db.commit()

    auto_closed = False
    if not inside:
        auto_closed = _maybe_auto_checkout(db, session)

    return {"inside_geofence": inside, "distance_meters": distance, "auto_checked_out": auto_closed}


def _maybe_auto_checkout(db: Session, session: AttendanceSession) -> bool:
    """Closes the session if it has been continuously outside the geofence
    for at least _AUTO_CHECKOUT_GRACE_MINUTES. Called after each ping so
    an employee walking away gets auto-checked-out without waiting for the
    periodic sweep (see worker.py for the sweep covering dropped pings)."""
    logs = (
        db.query(EmployeeLocationLog)
        .filter(EmployeeLocationLog.session_id == session.id)
        .order_by(EmployeeLocationLog.created_at.desc())
        .limit(50)
        .all()
    )
    if not logs:
        return False
    earliest_outside = None
    for log in logs:
        if not log.inside_geofence:
            earliest_outside = log.created_at
        else:
            break
    if earliest_outside is None:
        return False
    now = datetime.now(UTC)
    ref = earliest_outside if earliest_outside.tzinfo else earliest_outside.replace(tzinfo=UTC)
    if now - ref < timedelta(minutes=_AUTO_CHECKOUT_GRACE_MINUTES):
        return False
    session.check_out = now
    session.status = "closed"
    session.auto_checkout = True
    db.add(session)
    db.commit()
    return True


# ── live tracking ────────────────────────────────────────────────────────────

class LiveLocationOut(BaseModel):
    employee_id: str
    employee_name: str
    session_id: str
    check_in: str
    latitude: float
    longitude: float
    inside_geofence: bool
    last_ping: str


@gated_router.get("/live-locations", response_model=list[LiveLocationOut])
def live_locations(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr:view_all_attendance")),
) -> list[LiveLocationOut]:
    sessions = _scope_attendance_to_branch(
        db.query(AttendanceSession)
        .filter(AttendanceSession.company_id == emp.company_id, AttendanceSession.status == "open"),
        emp, db, branch_id,
    ).all()
    out: list[LiveLocationOut] = []
    for session in sessions:
        latest = (
            db.query(EmployeeLocationLog)
            .filter(EmployeeLocationLog.session_id == session.id)
            .order_by(EmployeeLocationLog.created_at.desc())
            .first()
        )
        target = db.get(Employee, session.employee_id)
        if not target:
            continue
        lat = float(latest.latitude) if latest else float(session.check_in_lat or 0)
        lng = float(latest.longitude) if latest else float(session.check_in_lng or 0)
        inside = latest.inside_geofence if latest else True
        last_ping = str(latest.created_at) if latest else str(session.check_in)
        out.append(LiveLocationOut(
            employee_id=target.id, employee_name=target.full_name, session_id=session.id,
            check_in=str(session.check_in), latitude=lat, longitude=lng, inside_geofence=inside, last_ping=last_ping,
        ))
    return out
