"""RBAC, company geofencing, and GPS attendance for HRMS.

Extends the employee identity already used by app/routers/ess.py rather than
building a second auth system: tokens issued here use the same "emp:" JWT
subject prefix and secret key, so an /hr/login token also works against
/ess/* routes and vice versa. See docs/hrms-architecture.md for the design.
"""

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import jwt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth_principal import (
    OWN_DEPARTMENT,
    Principal,
    _role_department_scope,
    resolve_department_scope,
    _role_permission_keys,
    assert_employee_may_sign_in,
    get_current_employee,
    get_current_principal,
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
    AppDataRecord,
    AttendanceDetail,
    AttendanceSession,
    Branch,
    Company,
    CompanyLocation,
    Employee,
    EmployeeBranchAccess,
    EmployeeLocation,
    EmployeeLocationLog,
    ESS_REFRESH_IDLE_DAYS,
    EssRefreshToken,
    Permission,
    Role,
    RolePermission,
)
from app.security import pwd_context, verify_employee_password
import app.timezone_utils as timezone_utils

# /login and /logout stay on the ungated `router` (issuing/discarding a token
# can't itself require a module check — there's no principal yet); every
# other HRMS-admin/employee endpoint here requires the "hrms" module,
# checked server-side (not just hidden in the sidebar — see require_module).
router = APIRouter(prefix="/hr", tags=["hr-access"])
gated_router = APIRouter(prefix="/hr", tags=["hr-access"], dependencies=[Depends(require_module("hrms"))])
settings = get_settings()

_EMP_PREFIX = "emp:"
_AUTO_CHECKOUT_GRACE_MINUTES = 5
# A phone reporting its position as "somewhere within N metres" can't prove it is inside a
# geofence that is smaller than N. Readings less precise than this (or than the site's
# own radius, whichever is larger) are refused instead of trusted.
_MAX_GPS_ACCURACY_METERS = 100
_FORGOTTEN_CHECKOUT_HOURS = 16  # same as worker._STALE_SESSION_HOURS
logger = logging.getLogger(__name__)

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
    "sales": ["view", "edit", "delete", "view_all_branches"],
    "quotations": ["view", "edit", "delete"],
    "pos": ["view", "edit", "delete", "view_all_branches"],
    "purchase": ["view", "edit", "delete", "view_all_branches"],
    "inventory": ["view", "edit", "delete", "view_all_branches"],
    "expense": ["view", "edit", "delete"],
    "bank": ["view", "edit", "delete"],
    "accounting": ["view", "edit", "delete", "view_all_branches"],
    "corporate": ["view", "edit", "delete"],
    "notifications": ["view", "edit", "delete"],
    "expert": ["view"],
}

_DEFAULT_ROLES: dict[str, list[str]] = {
    "Administrator": ["*"],  # all permissions
    "HR Manager": [
        "hr:manage_roles", "hr:manage_locations", "hr:manage_employees", "hr:view_all_attendance",
        "attendance:check_in_out", "attendance:view_own_attendance", "attendance:view", "attendance:edit",
        "dashboard:hr", "dashboard:view",
        "employees:view", "employees:edit", "employees:delete", "employees:view_salary",
        "leave:view", "leave:edit", "leave:delete",
        "rota:view", "rota:edit", "rota:delete",
        "overtime:view", "overtime:edit", "overtime:delete",
        "loans:view", "loans:edit", "loans:delete",
        "hr_workflow:view", "hr_workflow:edit", "hr_workflow:delete",
        "recruitment:view", "recruitment:edit", "recruitment:delete",
        "performance:view", "performance:edit", "performance:delete",
        "payroll:view", "payroll:view_payroll",
        "reports:view", "ai_insights:view",
        "hr_settings:view", "hr_settings:edit",
    ],
    "Payroll Officer": [
        "payroll:run_payroll", "payroll:view_payroll", "payroll:view", "payroll:edit",
        "attendance:check_in_out", "attendance:view_own_attendance", "attendance:view",
        "dashboard:payroll", "dashboard:view",
        "employees:view", "employees:view_salary",
        "overtime:view", "loans:view", "reports:view",
    ],
    "Manager": [
        "hr:view_all_attendance", "attendance:check_in_out", "attendance:view_own_attendance", "attendance:view",
        "dashboard:manager", "dashboard:view",
        "employees:view", "leave:view", "leave:edit", "rota:view", "rota:edit",
        "overtime:view", "overtime:edit", "hr_workflow:view", "hr_workflow:edit",
    ],
    "Employee": [
        "attendance:check_in_out", "attendance:view_own_attendance",
        "dashboard:employee", "dashboard:view",
    ],
}
# Built-in roles get their defaults when created, plus once per bump of this flag at startup.
# Bump it whenever _DEFAULT_ROLES gains permissions; admins' own edits are kept otherwise.
_DEFAULT_ROLES_FLAG = "default_roles_v2"
# The Employee role is the self-service default every new login falls back to — not editable.
_LOCKED_SYSTEM_ROLES = frozenset({"Employee"})


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


def _role_permission_keys_bulk(db: Session, roles: list[Role]) -> dict[str, set[str]]:
    """{role_id: {"module:name", ...}} for many roles in ONE query -- the list
    endpoints used to call _role_permission_keys() once per role."""
    out: dict[str, set[str]] = {r.id: set() for r in roles}
    if not roles:
        return out
    rows = (
        db.query(RolePermission.role_id, Permission.module, Permission.permission_name)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .filter(RolePermission.role_id.in_(list(out)))
        .all()
    )
    for role_id, module, name in rows:
        out[role_id].add(f"{module}:{name}")
    return out


def _default_grant_keys(catalog: dict[str, Permission], perm_keys: list[str]) -> list[str]:
    # "*" never implies the cross-branch opt-ins — those must be granted per role explicitly.
    if perm_keys == ["*"]:
        return [k for k in catalog if not k.endswith(":view_all_branches")]
    return perm_keys


def _ensure_default_roles(db: Session, company_id: str, refresh_permissions: bool = False) -> dict[str, Role]:
    """Creates any missing built-in role with its defaults. Existing roles keep the permissions
    an admin gave them unless refresh_permissions (the one-time startup upgrade) is set."""
    catalog = _ensure_permission_catalog(db)
    roles = {r.role_name: r for r in db.query(Role).filter(Role.company_id == company_id).all()}
    links_by_role: dict[str, set[str]] = {}
    if roles and refresh_permissions:
        for role_id, permission_id in db.query(RolePermission.role_id, RolePermission.permission_id).filter(
            RolePermission.role_id.in_([r.id for r in roles.values()])
        ).all():
            links_by_role.setdefault(role_id, set()).add(permission_id)
    changed = False
    for role_name, perm_keys in _DEFAULT_ROLES.items():
        role = roles.get(role_name)
        created = False
        if not role:
            # Manager defaults to the login's own department; the other presets are company-wide.
            department_scope = json.dumps([OWN_DEPARTMENT]) if role_name == "Manager" else None
            role = Role(company_id=company_id, role_name=role_name, is_system_role=True, department_scope=department_scope)
            db.add(role)
            db.flush()
            roles[role_name] = role
            changed = created = True
        if not (created or (refresh_permissions and role.is_system_role)):
            continue
        existing_links = links_by_role.setdefault(role.id, set())
        for key in _default_grant_keys(catalog, perm_keys):
            perm = catalog.get(key)
            if perm and perm.id not in existing_links:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
                existing_links.add(perm.id)
                changed = True
    if changed:
        db.commit()
    return roles


def backfill_default_role_permissions(db: Session) -> None:
    """Startup, once per _DEFAULT_ROLES_FLAG: give every company's built-in roles any newly added default."""
    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    if db.execute(text("SELECT 1 FROM schema_flags WHERE name = :n"), {"n": _DEFAULT_ROLES_FLAG}).first():
        return
    company_ids = [r[0] for r in db.query(Role.company_id).filter(Role.is_system_role.is_(True)).distinct().all()]
    for company_id in company_ids:
        _ensure_default_roles(db, company_id, refresh_permissions=True)
    db.execute(text("INSERT INTO schema_flags (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"), {"n": _DEFAULT_ROLES_FLAG})
    db.commit()


_MAIN_EDITABLE_MODULES = ("sales", "quotations", "pos", "purchase", "inventory", "expense", "bank", "accounting", "corporate", "notifications")


def backfill_main_module_edit(db: Session) -> None:
    """Startup, once: main-app modules gained Edit/Delete (View used to allow changes too), so every
    role that could view a module keeps changing it until an admin narrows it."""
    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    if db.execute(text("SELECT 1 FROM schema_flags WHERE name = 'main_module_edit_v1'")).first():
        return
    catalog = _ensure_permission_catalog(db)
    roles = db.query(Role).all()
    for role in roles:
        held = _role_permission_keys_bulk(db, [role])[role.id]
        for module in _MAIN_EDITABLE_MODULES:
            if f"{module}:view" in held or f"{module}:view_all_branches" in held:
                for level in ("edit", "delete"):
                    perm = catalog.get(f"{module}:{level}")
                    if perm and f"{module}:{level}" not in held:
                        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.execute(text("INSERT INTO schema_flags (name) VALUES ('main_module_edit_v1') ON CONFLICT (name) DO NOTHING"))
    db.commit()


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


def _assigned_locations(db: Session, employee_id: str) -> list[CompanyLocation]:
    """Every active work location assigned to this employee, primary first."""
    rows = (
        db.query(CompanyLocation)
        .join(EmployeeLocation, EmployeeLocation.location_id == CompanyLocation.id)
        .filter(EmployeeLocation.employee_id == employee_id)
        .order_by(EmployeeLocation.is_primary.desc(), CompanyLocation.location_name)
        .all()
    )
    return [loc for loc in rows if str(loc.status or "active").strip().lower() == "active"]


def _nearest_assigned_location(
    db: Session, employee_id: str, latitude: float, longitude: float,
) -> tuple[CompanyLocation, float] | None:
    """(location, distance in metres) of the employee's assigned site closest to this point,
    so someone assigned to several clinics can check in at whichever one they're at
    (this used to check only the primary site). None when no active site is assigned."""
    best: tuple[CompanyLocation, float] | None = None
    for loc in _assigned_locations(db, employee_id):
        distance = _distance_meters(latitude, longitude, float(loc.latitude), float(loc.longitude))
        if best is None or distance < best[1]:
            best = (loc, distance)
    return best


# HR > GPS Attendance > Check-in method: "both" (default) or "biometric" (no phone check-in).
_ATTENDANCE_METHOD_KEY = "attendance-method-config"
ATTENDANCE_METHODS = ("both", "biometric")


def attendance_method(db: Session, company_id: str) -> str:
    row = db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == _ATTENDANCE_METHOD_KEY).first()
    try:
        mode = str((json.loads(row[0]) if row else {}).get("mode") or "both").lower()
    except (TypeError, ValueError, AttributeError):
        mode = "both"
    return mode if mode in ATTENDANCE_METHODS else "both"


def _open_day_punch(db: Session, emp: Employee) -> dict | None:
    """Today's attendance (any source: biometric device, GPS, import) when its last session
    is still open: {"at": first in of that session, "source": "device" | "gps" | ...}."""
    from app.attendance_store import _company_offset, _local_date, _pair_day_punches

    now = datetime.now(UTC)
    work_date = _local_date(now, _company_offset(db, emp.company_id))
    row = db.query(AttendanceDetail.raw_events).filter(
        AttendanceDetail.company_id == emp.company_id, AttendanceDetail.employee_id == emp.employee_no,
        AttendanceDetail.work_date == work_date).first()
    try:
        events = json.loads(row[0] or "[]") if row else []
    except (TypeError, ValueError):
        return None
    parsed = sorted(((datetime.fromisoformat(e["punch_time"]), e.get("direction") or "in", e.get("source") or "device")
                     for e in events if e.get("punch_time")), key=lambda t: t[0])
    if not parsed:
        return None
    pairs = _pair_day_punches([(ts, d) for ts, d, _src in parsed])
    if not pairs or pairs[-1][1] is not None:
        return None
    opened = pairs[-1][0]
    source = next((src for ts, _d, src in parsed if ts == opened), "device")
    return {"at": opened if opened.tzinfo else opened.replace(tzinfo=UTC), "source": source}


def own_check_status(db: Session, emp: Employee) -> dict:
    """The employee's own "am I checked in" for ESS, from a GPS session or the day's punches
    from any source -- a device check-in used to leave ESS showing "Check in"."""
    open_session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    day = None if open_session else _open_day_punch(db, emp)
    at = open_session.check_in if open_session else (day["at"] if day else None)
    return {
        "checked_in": bool(open_session or day),
        "check_in_time": str(at) if at else None,
        "check_in_at": _iso_utc(at) if at else None,
        "check_in_source": "gps" if open_session else ("device" if day and day["source"] != "gps" else ("gps" if day else None)),
        "gps_enabled": attendance_method(db, emp.company_id) != "biometric",
    }


def _record_gps_punch(db: Session, emp: Employee, when: datetime, direction: str, location_name: str | None) -> None:
    """Writes a GPS check-in/out into the same attendance records biometric punches use, so it
    shows in Today's Attendance, the monthly and late reports, payroll overtime and ESS history.
    The GPS session is already saved; a failure here is logged, never turned into an error."""
    from app import attendance_store

    if when.tzinfo is None:  # some databases hand stored times back without a zone; they are UTC
        when = when.replace(tzinfo=UTC)
    try:
        result = attendance_store.upsert_attendance_event(
            db, company_id=emp.company_id, employee_id=emp.employee_no, punch_time=when, direction=direction,
            employee_name=emp.full_name, device_id="gps", device_name=f"GPS · {location_name}" if location_name else "GPS",
            source="gps",
        )
        if not result.get("ok"):
            logger.warning("GPS %s for employee %s not recorded in attendance: %s", direction, emp.id, result.get("error"))
    except Exception:
        db.rollback()
        logger.exception("GPS %s for employee %s not recorded in attendance", direction, emp.id)


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
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0)
    device: str | None = Field(default=None, max_length=200)
    battery: int | None = Field(default=None, ge=0, le=100)
    # Android marks positions from mock-location ("fake GPS") apps; the mobile app passes it on.
    is_mock: bool | None = None


_FAKE_GPS_DETAIL = ("Your phone is using a fake location app, so GPS check-in is blocked. "
                    "Turn off any mock-location app (Settings > Developer options > Select mock location app) "
                    "and try again. HR has been notified of this attempt.")


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

    if not verify_employee_password(payload.password, emp.password_hash, emp.employee_no):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in — check your username and password and try again")

    assert_employee_may_sign_in(emp)

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
        department_scope=resolve_department_scope(role, emp),
    )


# ── role-based dashboard ────────────────────────────────────────────────────

@gated_router.get("/dashboard")
def hr_dashboard(db: Session = Depends(get_db), emp: Employee = Depends(get_current_employee)) -> dict:
    role = db.get(Role, emp.role_id) if emp.role_id else None
    role_name = role.role_name if role else "Employee"
    company_id = emp.company_id
    mine = own_check_status(db, emp)

    if role_name in ("Administrator", "HR Manager"):
        return {
            **mine,
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
            **mine,
            "role": role_name,
            "latest_run_period": latest.period if latest else None,
            "latest_run_status": latest.status if latest else None,
            "latest_run_net_total": net_total,
        }
    if role_name == "Manager":
        return {
            **mine,
            "role": role_name,
            "team_active_sessions": _scope_attendance_to_branch(
                db.query(AttendanceSession).filter(
                    AttendanceSession.company_id == company_id, AttendanceSession.status == "open"
                ),
                emp, db,
            ).count(),
        }

    # Employee dashboard — own status only
    # Sessions since company-local midnight, for the ESS mobile In/Out/Hours tiles
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    offset = timezone_utils.company_utc_offset(country)
    local_now = datetime.now(UTC) + offset
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    today_sessions = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.check_in >= day_start)
        .order_by(AttendanceSession.check_in)
        .all()
    )
    return {
        **mine,
        "role": role_name,
        "today_sessions": [
            {"check_in": _iso_utc(s.check_in), "check_out": _iso_utc(s.check_out) if s.check_out else None}
            for s in today_sessions
        ],
    }


def _iso_utc(dt: datetime) -> str:
    """ISO-8601 with an explicit UTC offset (SQLite hands back naive datetimes)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


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


def _assert_can_grant(principal: Principal, keys: set[str] | list[str], department_scope: list[str] | None) -> None:
    """A non-admin can't hand out more than they hold: every permission must be in their own role,
    and a department-scoped login can only give out its own departments (never company-wide).
    Without this, "HR settings: edit" could create or assign a role with everything -- including
    the Administrator role -- and take it."""
    if principal.is_admin:
        return
    missing = sorted(set(keys) - set(principal.permissions))
    if missing:
        raise HTTPException(
            status_code=403,
            detail=f"You can't grant permissions your own role doesn't have: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}",
        )
    if principal.is_dept_scoped:
        wanted = {str(d).strip().lower() for d in (department_scope or []) if str(d).strip()}
        # "@own" = the holder's own department; assigning already requires the employee to be in
        # the assigner's departments (assert_employee_in_scope), so it can't reach outside them.
        if not wanted or not (wanted - {OWN_DEPARTMENT.lower()}) <= principal.department_scope:
            raise HTTPException(status_code=403, detail="You can only give access to your own departments")


def _assert_can_manage_role(db: Session, principal: Principal, role: Role) -> None:
    """Editing or deleting an existing role: on top of _assert_can_grant() for the new values,
    a department-scoped login may only touch a role that already belongs to its own departments
    and is held only by employees in them. Otherwise a Sales-only HR editor could re-scope the
    Warehouse manager's role to Sales (or strip it) and cut Warehouse off."""
    if not principal.is_dept_scoped:
        return
    current = {d.lower() for d in _role_department_scope(role)}
    if not current or not (current - {OWN_DEPARTMENT.lower()}) <= principal.department_scope:
        raise HTTPException(status_code=403, detail="This role covers other departments too, so only an administrator can change it")
    holder_departments = {
        str(d or "").strip().lower()
        for (d,) in db.query(Employee.department).filter(Employee.company_id == principal.company_id, Employee.role_id == role.id).distinct()
    }
    if not holder_departments <= principal.department_scope:
        raise HTTPException(status_code=403, detail="This role is held by employees in other departments, so only an administrator can change it")


def _normalize_department_scope(names: list[str]) -> str | None:
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()})
    return json.dumps(cleaned) if cleaned else None


@gated_router.get("/roles", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr_settings:view", "hr:manage_roles")),
) -> list[RoleOut]:
    _ensure_default_roles(db, emp.company_id)
    roles = db.query(Role).filter(Role.company_id == emp.company_id).order_by(Role.role_name).all()
    perms_by_role = _role_permission_keys_bulk(db, roles)
    return [
        RoleOut(
            id=r.id, role_name=r.role_name, description=r.description, is_system_role=r.is_system_role,
            permissions=sorted(perms_by_role[r.id]),
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
    # Same module rule as admin_create_role(): nothing from a module the company hasn't enabled.
    company_modules = db.query(Company.modules_enabled).filter(Company.id == emp.company_id).scalar()
    allowed_keys = _company_allowed_catalog_keys(catalog, company_modules)
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm and key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"'{perm.module}' is not enabled for your company — cannot grant this permission")
    holder = db.get(Role, emp.role_id) if emp.role_id else None
    _assert_can_grant(
        Principal(kind="employee", company_id=emp.company_id, display_name=emp.full_name, is_admin=False,
                  permissions=frozenset(_role_permission_keys(db, holder)),
                  department_scope=frozenset(d.lower() for d in resolve_department_scope(holder, emp))),
        payload.permission_keys, payload.department_scope,
    )
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
def list_permissions(
    db: Session = Depends(get_db),
    emp: Employee = Depends(require_permission("hr_settings:view", "hr:manage_roles")),
) -> list[dict]:
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
    perms_by_role = _role_permission_keys_bulk(db, roles)
    return [
        RoleOut(
            id=r.id, role_name=r.role_name, description=r.description, is_system_role=r.is_system_role,
            permissions=sorted(perms_by_role[r.id]),
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
    _assert_can_grant(principal, payload.permission_keys, payload.department_scope)
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
    if role.is_system_role and role.role_name in _LOCKED_SYSTEM_ROLES:
        raise HTTPException(status_code=400, detail=f"The default {role.role_name} role can't be edited — create a custom role instead")
    catalog = _ensure_permission_catalog(db)
    company_modules = db.query(Company.modules_enabled).filter(Company.id == principal.company_id).scalar()
    allowed_keys = _company_allowed_catalog_keys(catalog, company_modules)
    for key in payload.permission_keys:
        perm = catalog.get(key)
        if perm and key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"'{perm.module}' is not enabled for your company — cannot grant this permission")
    # Both the new permissions and the role as it stands: a non-admin can't edit a role that
    # already holds more than they do (e.g. strip it, or rename it into something they can take).
    _assert_can_manage_role(db, principal, role)
    _assert_can_grant(principal, set(payload.permission_keys) | _role_permission_keys(db, role), payload.department_scope)
    # Built-in roles are looked up by name (_ensure_default_roles), so their name never changes.
    role_name = role.role_name if role.is_system_role else payload.role_name.strip()
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
        id=role.id, role_name=role.role_name, description=role.description, is_system_role=role.is_system_role,
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
    _assert_can_manage_role(db, principal, role)
    # Same rule as editing it: a non-admin can't delete a role that holds more than they do.
    _assert_can_grant(principal, _role_permission_keys(db, role), _role_department_scope(role) or None)
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
            if role.id != target.role_id:
                _assert_can_grant(principal, _role_permission_keys(db, role), _role_department_scope(role))
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


# ── signed-in devices (ESS mobile app refresh tokens) ────────────────────────
#
# The Android app stays signed in with a refresh token (ess.py, POST /ess/refresh), so a
# lost or handed-in phone keeps working until HR signs it out here.

class EmployeeDeviceOut(BaseModel):
    id: str
    device: str | None = None
    signed_in_at: str
    last_used_at: str
    active: bool
    status: str        # "Signed in", "Signed out", "Expired", "Password changed"


def _device_status(row: EssRefreshToken, emp: Employee, now: datetime) -> str:
    def aware(dt):
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)
    if row.revoked_at is not None:
        return "Signed out"
    changed = aware(emp.password_changed_at)
    if changed and changed > aware(row.issued_at):
        return "Password changed"
    if now - aware(row.last_used_at) > timedelta(days=ESS_REFRESH_IDLE_DAYS):
        return "Expired"
    return "Signed in"


def _device_target(db: Session, principal: Principal, employee_id: str) -> Employee:
    target = db.query(Employee).filter(Employee.id == employee_id, Employee.company_id == principal.company_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_employee_in_scope(principal, target)  # department-scoped role: only its departments
    return target


@gated_router.get("/admin/employees/{employee_id}/devices", response_model=list[EmployeeDeviceOut])
def list_employee_devices(
    employee_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:view", "hr_settings:edit")),
) -> list[EmployeeDeviceOut]:
    """Phones this employee signed in to the ESS app on, most recently used first."""
    target = _device_target(db, principal, employee_id)
    now = datetime.now(UTC)
    rows = (
        db.query(EssRefreshToken)
        .filter(EssRefreshToken.employee_id == target.id, EssRefreshToken.company_id == principal.company_id)
        .order_by(EssRefreshToken.last_used_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for row in rows:
        state = _device_status(row, target, now)
        out.append(EmployeeDeviceOut(
            id=row.id, device=row.device, signed_in_at=_iso_utc(row.issued_at), last_used_at=_iso_utc(row.last_used_at),
            active=state == "Signed in", status=state,
        ))
    return out


@gated_router.post("/admin/employees/{employee_id}/devices/{device_id}/sign-out")
def sign_out_employee_device(
    employee_id: str,
    device_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> dict:
    """Signs one phone out. Its current 24h access token still works until it expires (tokens
    are stateless), but it can't renew, so automatic check-in on it stops within a day."""
    target = _device_target(db, principal, employee_id)
    row = db.query(EssRefreshToken).filter(EssRefreshToken.id == device_id, EssRefreshToken.employee_id == target.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
    return {"ok": True}


@gated_router.post("/admin/employees/{employee_id}/devices/sign-out-all")
def sign_out_all_employee_devices(
    employee_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr_settings:edit")),
) -> dict:
    target = _device_target(db, principal, employee_id)
    count = (
        db.query(EssRefreshToken)
        .filter(EssRefreshToken.employee_id == target.id, EssRefreshToken.revoked_at.is_(None))
        .update({EssRefreshToken.revoked_at: datetime.now(UTC)}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "signed_out": count}


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
    location_name: str = Field(min_length=1, max_length=120)
    branch_id: str | None = None
    address: str | None = Field(default=None, max_length=400)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Under ~20 m a phone's own GPS error alone would refuse people standing inside.
    allowed_radius_meters: int = Field(default=200, ge=20, le=5000)
    # "active" / "inactive"; left out = unchanged on edit, active on create.
    status: str | None = None

    @field_validator("location_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Enter a location name")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in ("active", "inactive"):
            raise ValueError("Status must be active or inactive")
        return v


def _location_out(loc: CompanyLocation) -> CompanyLocationOut:
    return CompanyLocationOut(
        id=loc.id, location_name=loc.location_name, address=loc.address,
        latitude=float(loc.latitude), longitude=float(loc.longitude),
        allowed_radius_meters=loc.allowed_radius_meters, status=loc.status,
    )


class AttendanceMethodIn(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ATTENDANCE_METHODS:
            raise ValueError("mode must be both or biometric")
        return v


@gated_router.get("/attendance-method")
def get_attendance_method(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict:
    return {"mode": attendance_method(db, principal.company_id)}


@gated_router.put("/attendance-method")
def set_attendance_method(
    payload: AttendanceMethodIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr:manage_locations")),
) -> dict:
    row = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == principal.company_id, AppDataRecord.collection == "hr_settings",
        AppDataRecord.record_key == _ATTENDANCE_METHOD_KEY).first()
    data = json.dumps({"id": _ATTENDANCE_METHOD_KEY, "mode": payload.mode})
    if row:
        row.payload = data
    else:
        db.add(AppDataRecord(company_id=principal.company_id, collection="hr_settings", record_key=_ATTENDANCE_METHOD_KEY, payload=data))
    db.commit()
    return {"mode": payload.mode}


@gated_router.get("/company-locations", response_model=list[CompanyLocationOut])
def list_company_locations(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> list[CompanyLocationOut]:
    query = db.query(CompanyLocation).filter(CompanyLocation.company_id == principal.company_id)
    # Same accessible-branches + NULL-stays-visible shape as
    # _scope_attendance_to_branch() — previously this returned every
    # branch's work locations to any branch-assigned employee. A company
    # admin's own HRMS login (no branch) sees them all.
    if principal.branch_id and not principal.is_admin:
        accessible = set(principal.accessible_branch_ids) | {principal.branch_id}
        query = query.filter((CompanyLocation.branch_id.in_(accessible)) | (CompanyLocation.branch_id.is_(None)))
    rows = query.order_by(CompanyLocation.location_name).all()
    return [_location_out(r) for r in rows]


@gated_router.post("/company-locations", response_model=CompanyLocationOut, status_code=201)
def create_company_location(
    payload: CompanyLocationRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr:manage_locations")),
) -> CompanyLocationOut:
    loc = CompanyLocation(
        company_id=principal.company_id, location_name=payload.location_name.strip(), branch_id=payload.branch_id,
        address=payload.address, latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
        allowed_radius_meters=payload.allowed_radius_meters, status=payload.status or "active",
    )
    db.add(loc)
    db.commit()
    return _location_out(loc)


@gated_router.put("/company-locations/{location_id}", response_model=CompanyLocationOut)
def update_company_location(
    location_id: str,
    payload: CompanyLocationRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr:manage_locations")),
) -> CompanyLocationOut:
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == location_id, CompanyLocation.company_id == principal.company_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.location_name = payload.location_name.strip()
    loc.branch_id = payload.branch_id
    loc.address = payload.address
    loc.latitude = Decimal(str(payload.latitude))
    loc.longitude = Decimal(str(payload.longitude))
    loc.allowed_radius_meters = payload.allowed_radius_meters
    if payload.status:
        loc.status = payload.status
    db.add(loc)
    db.commit()
    return _location_out(loc)


@gated_router.delete("/company-locations/{location_id}", status_code=204, response_model=None)
def delete_company_location(
    location_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr:manage_locations")),
) -> None:
    """Deletes a location even when it is in use: employees assigned to it are unassigned
    (their next assigned site becomes primary), anyone checked in there is checked out now,
    and past check-ins keep their times but no longer point at it. Deleting it used to fail
    on PostgreSQL with a foreign-key error once any employee had been assigned."""
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == location_id, CompanyLocation.company_id == principal.company_id).first()
    if not loc:
        return
    now = datetime.now(UTC)
    for session in db.query(AttendanceSession).filter(AttendanceSession.location_id == loc.id, AttendanceSession.status == "open").all():
        session.check_out = now
        session.status = "closed"
        session.auto_checkout = True
        emp = db.get(Employee, session.employee_id)
        db.commit()
        if emp:
            _record_gps_punch(db, emp, now, "out", loc.location_name)
    db.query(AttendanceSession).filter(AttendanceSession.location_id == loc.id).update({"location_id": None}, synchronize_session=False)
    affected = [eid for (eid,) in db.query(EmployeeLocation.employee_id).filter(EmployeeLocation.location_id == loc.id)]
    db.query(EmployeeLocation).filter(EmployeeLocation.location_id == loc.id).delete(synchronize_session=False)
    for emp in db.query(Employee).filter(Employee.company_id == principal.company_id,
                                         (Employee.work_location_id == loc.id) | (Employee.id.in_(affected or ["-"]))).all():
        remaining = (
            db.query(EmployeeLocation).filter(EmployeeLocation.employee_id == emp.id)
            .order_by(EmployeeLocation.is_primary.desc()).first()
        )
        if remaining and not remaining.is_primary:
            remaining.is_primary = True
        emp.work_location_id = remaining.location_id if remaining else None
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
    principal: Principal = Depends(require_principal_permission("hr:manage_locations", "hr:manage_employees", "hr:view_all_attendance")),
) -> list[EmployeeLocationOut]:
    rows = (
        db.query(EmployeeLocation, Employee, CompanyLocation)
        .join(Employee, Employee.id == EmployeeLocation.employee_id)
        .join(CompanyLocation, CompanyLocation.id == EmployeeLocation.location_id)
        .filter(Employee.company_id == principal.company_id)
        .order_by(EmployeeLocation.is_primary.desc())
    )
    rows = scope_employee_query_by_names(rows, principal.department_scope).all()  # department-scoped role: only its departments
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
    principal: Principal = Depends(require_principal_permission("hr:manage_locations", "hr:manage_employees")),
) -> None:
    link = (
        db.query(EmployeeLocation)
        .join(Employee, Employee.id == EmployeeLocation.employee_id)
        .filter(EmployeeLocation.id == link_id, Employee.company_id == principal.company_id)
        .first()
    )
    if link:
        target = db.get(Employee, link.employee_id)
        assert_employee_in_names_scope(principal.department_scope, target)
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
    principal: Principal = Depends(require_principal_permission("hr:manage_locations", "hr:manage_employees")),
) -> dict:
    target = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.company_id == principal.company_id).first()
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == payload.location_id, CompanyLocation.company_id == principal.company_id).first()
    if not target or not loc:
        raise HTTPException(status_code=404, detail="Employee or location not found")
    assert_employee_in_names_scope(principal.department_scope, target)
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
    if attendance_method(db, emp.company_id) == "biometric":
        raise HTTPException(status_code=403, detail="Your company records attendance on the biometric device only, so phone check-in is off.")
    if payload.is_mock:
        # Refused, but kept: the attempt is what HR needs to see (GET /hr/gps-alerts).
        db.add(EmployeeLocationLog(
            company_id=emp.company_id, employee_id=emp.id, session_id=None,
            latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
            accuracy=Decimal(str(payload.accuracy)) if payload.accuracy is not None else None,
            inside_geofence=False, device=payload.device, battery=payload.battery, is_mock=True,
        ))
        db.commit()
        logger.warning("GPS check-in refused for employee %s: mock location", emp.id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FAKE_GPS_DETAIL)
    existing = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    if not existing:
        day = _open_day_punch(db, emp)
        if day and day["source"] != "gps":
            local = day["at"] + timezone_utils.company_utc_offset(db.query(Company.country).filter(Company.id == emp.company_id).scalar())
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"You are already checked in on the attendance device (since {local.strftime('%H:%M')}).")
    if existing:
        started = existing.check_in if existing.check_in.tzinfo else existing.check_in.replace(tzinfo=UTC)
        if datetime.now(UTC) - started < timedelta(hours=_FORGOTTEN_CHECKOUT_HOURS):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already checked in")
        # A session from an earlier shift whose check-out was forgotten (and the background
        # sweep hasn't closed yet) must not block today's check-in. Its day keeps the check-in
        # with no check-out time, which the employee fixes with a correction request.
        existing.check_out = datetime.now(UTC)
        existing.status = "closed"
        existing.auto_checkout = True
        db.add(existing)
        db.commit()

    if payload.latitude == 0 and payload.longitude == 0:
        # 0,0 is what a phone sends when it has no position at all.
        raise HTTPException(status_code=422, detail="Your phone did not report a location. Turn on location services and try again.")
    nearest = _nearest_assigned_location(db, emp.id, payload.latitude, payload.longitude)
    if not nearest:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have no work location for GPS check-in yet. Ask HR to assign you to one.")
    loc, distance = nearest
    max_accuracy = max(_MAX_GPS_ACCURACY_METERS, loc.allowed_radius_meters)
    if payload.accuracy is None or payload.accuracy > max_accuracy:
        shown = "unknown" if payload.accuracy is None else f"±{int(payload.accuracy)}m"
        raise HTTPException(
            status_code=422,
            detail=f"GPS signal too weak to confirm you're on site ({shown}; needs ±{max_accuracy}m or better). "
                   "Move near a window or outdoors, make sure precise location is on, and try again.",
        )
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
    _record_gps_punch(db, emp, now, "in", loc.location_name)
    return CheckInOut(session_id=session.id, status="open", check_in=str(session.check_in), location_name=loc.location_name, distance_meters=distance)


class CheckOutIn(BaseModel):
    """Check-out body: where the phone is (optional) and, from the mobile app's automatic
    check-out, when the employee left the site (left_at)."""
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0)
    device: str | None = Field(default=None, max_length=200)
    battery: int | None = Field(default=None, ge=0, le=100)
    left_at: datetime | None = None


# How old a phone-reported leaving time may be (the phone was offline when the employee left).
_MAX_LEFT_AT_AGE = timedelta(hours=12)


def _check_out_time(left_at: datetime | None, checked_in: datetime, now: datetime) -> datetime:
    """When the day ends: the time the phone says the employee left the site, if it is
    plausible, else now. A phone that was offline when its owner walked out sends the check-out
    later, and recording that later time paid hours nobody worked. The phone's clock is only
    trusted to make the day shorter, never longer: the time must be after the check-in, not in
    the future, and at most _MAX_LEFT_AT_AGE ago."""
    if left_at is None:
        return now
    left = (left_at if left_at.tzinfo else left_at.replace(tzinfo=UTC)).astimezone(UTC)
    start = checked_in if checked_in.tzinfo else checked_in.replace(tzinfo=UTC)
    if left > now or left <= start or now - left > _MAX_LEFT_AT_AGE:
        return now
    return left


@gated_router.post("/check-out")
def check_out(
    payload: CheckOutIn | None = None,
    db: Session = Depends(get_db),
    emp: Employee = Depends(get_current_employee),
) -> dict:
    has_point = payload is not None and payload.latitude is not None and payload.longitude is not None
    left_at = payload.left_at if payload else None
    session = (
        db.query(AttendanceSession)
        .filter(AttendanceSession.employee_id == emp.id, AttendanceSession.status == "open")
        .first()
    )
    if not session:
        # Checked in on the biometric device: the phone may end the day (a GPS check-out).
        day = _open_day_punch(db, emp)
        if day:
            when = _check_out_time(left_at, day["at"], datetime.now(UTC))
            where = None
            if has_point:
                nearest = _nearest_assigned_location(db, emp.id, payload.latitude, payload.longitude)
                where = nearest[0].location_name if nearest else None
            _record_gps_punch(db, emp, when, "out", where)
            return {"ok": True, "session_id": None, "check_out": str(when), "source": "device"}
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not checked in")
    when = _check_out_time(left_at, session.check_in, datetime.now(UTC))
    session.check_out = when
    session.status = "closed"
    if has_point:
        session.check_out_lat = Decimal(str(payload.latitude))
        session.check_out_lng = Decimal(str(payload.longitude))
    db.add(session)
    db.commit()
    loc = db.get(CompanyLocation, session.location_id) if session.location_id else None
    _record_gps_punch(db, emp, when, "out", loc.location_name if loc else None)
    return {"ok": True, "session_id": session.id, "check_out": str(when)}


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
    if payload.is_mock:
        # A faked position proves nothing about where the phone is: count it as outside, so
        # the usual grace-period auto check-out applies, and list it for HR.
        inside = False

    db.add(EmployeeLocationLog(
        company_id=emp.company_id, employee_id=emp.id, session_id=session.id,
        latitude=Decimal(str(payload.latitude)), longitude=Decimal(str(payload.longitude)),
        accuracy=Decimal(str(payload.accuracy)) if payload.accuracy is not None else None,
        inside_geofence=inside, device=payload.device, battery=payload.battery, is_mock=bool(payload.is_mock),
    ))
    db.commit()

    auto_closed = False
    if not inside:
        auto_closed = _maybe_auto_checkout(db, session)
        if auto_closed:
            _record_gps_punch(db, emp, session.check_out, "out", loc.location_name if loc else None)

    return {
        "inside_geofence": inside, "distance_meters": distance, "auto_checked_out": auto_closed,
        "location_name": loc.location_name if loc else None,
        "allowed_radius_meters": loc.allowed_radius_meters if loc else None,
        "grace_minutes": _AUTO_CHECKOUT_GRACE_MINUTES,
        "check_out": session.check_out.isoformat() if auto_closed and session.check_out else None,
    }


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
    # Checked out at the moment they were first seen outside, not when the grace ran out.
    session.check_out = ref
    session.status = "closed"
    session.auto_checkout = True
    db.add(session)
    db.commit()
    return True


# ── live tracking ────────────────────────────────────────────────────────────

class GpsAlertOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    employee_no: str
    department: str | None = None
    at: str
    kind: str          # "check-in" (refused) or "while checked in" (a ping)
    latitude: float
    longitude: float
    device: str | None = None


@gated_router.get("/gps-alerts", response_model=list[GpsAlertOut])
def gps_alerts(
    days: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("hr:view_all_attendance")),
) -> list[GpsAlertOut]:
    """Fake-GPS attempts (positions the phone flagged as mock), newest first, for the
    departments this HR user can see -- the same reach as Live Employee Locations."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        db.query(EmployeeLocationLog, Employee)
        .join(Employee, Employee.id == EmployeeLocationLog.employee_id)
        .filter(EmployeeLocationLog.company_id == principal.company_id, EmployeeLocationLog.is_mock.is_(True),
                EmployeeLocationLog.created_at >= since)
    )
    rows = scope_employee_query_by_names(rows, principal.department_scope)
    out = []
    for log, e in rows.order_by(EmployeeLocationLog.created_at.desc()).limit(200).all():
        out.append(GpsAlertOut(
            id=log.id, employee_id=e.id, employee_name=e.full_name, employee_no=e.employee_no, department=e.department,
            at=_iso_utc(log.created_at), kind="check-in" if log.session_id is None else "while checked in",
            latitude=float(log.latitude), longitude=float(log.longitude), device=log.device,
        ))
    return out


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
    principal: Principal = Depends(require_principal_permission("hr:view_all_attendance")),
) -> list[LiveLocationOut]:
    query = db.query(AttendanceSession).filter(
        AttendanceSession.company_id == principal.company_id, AttendanceSession.status == "open",
    )
    if principal.employee is not None:
        # An employee login keeps its branch and department limits.
        query = _scope_attendance_to_branch(query, principal.employee, db, branch_id)
    elif branch_id:
        query = query.filter(AttendanceSession.branch_id == branch_id)
    sessions = query.all()
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
