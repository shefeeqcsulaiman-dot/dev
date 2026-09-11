"""Unified auth: resolves either a User (TaxFlow admin) or Employee (HRMS
RBAC sub-user) bearer token into one Principal shape, so endpoints shared
between both (Payroll, Leave, Attendance, HR admin screens) can serve either
without duplicating auth logic per router.

get_current_employee / require_permission / _role_permission_keys used to
live in app/routers/hr_access.py — moved here since nothing else imported
them from there (confirmed before the move), and employee-auth is a
cross-cutting concern, not something that belongs bundled with the
GPS/geofencing routes. hr_access.py imports them back from here.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.models import Branch, Company, Employee, EmployeeBranchAccess, Permission, Role, RolePermission, User
from app.module_catalog import BRANCH_ELIGIBLE_MODULES
from app.security import is_impersonation_token_revoked, user_id_from_token

logger = logging.getLogger(__name__)

settings = get_settings()
_EMP_PREFIX = "emp:"
_BRANCH_PREFIX = "branch:"


def assert_company_active(subscription_expires_at: str | None) -> None:
    """Enforce superadmin's per-company subscription expiry — reuses the
    existing Set Expiry field as the suspend control (a past/today date =
    suspended, blank/future = active) rather than adding a separate flag.
    NULL subscription_expires_at = unrestricted, matching the same
    NULL-means-unrestricted convention already used by company_allows_module()
    (dependencies.py) — this is also what protects superadmin's own sentinel
    company (SUPERADMIN-INTERNAL, never given an expiry) with no special-case
    code. 403, not 401: matches the existing is_active/require_module()
    precedent (account-disabled and module-not-enabled are both 403 today)
    and avoids authenticatedFetch()'s 401 handling (app.js), which clears the
    token and attempts a silent re-login — a 403 leaves the session intact so
    the frontend can show a specific "subscription expired" message instead.
    Takes the raw date string (not a Company object) so both the User path
    (already has it via a joinedload) and the Employee path (needs a fresh,
    minimal scalar query — Employee has no `company` relationship) can call
    this the same way without either constructing a fake Company object."""
    if not subscription_expires_at:
        return
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # <=, not <: a same-day expiry (e.g. superadmin's "Suspend Now", which
    # sets today's date) must block immediately, not tomorrow.
    if subscription_expires_at <= today:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your company's subscription has expired. Contact support to renew.",
        )


# ── employee-token auth (moved from hr_access.py) ───────────────────────────

def _employee_id_from_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        sub: str | None = payload.get("sub")
        if sub and sub.startswith(_EMP_PREFIX):
            return sub[len(_EMP_PREFIX):]
    except JWTError:
        pass
    return None


def get_current_employee(request: Request, db: Session = Depends(get_db)) -> Employee:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    emp_id = _employee_id_from_token(auth[7:])
    if not emp_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Employee not found")
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == emp.company_id).scalar()
    assert_company_active(expires_at)
    emp.last_activity = datetime.now(UTC)
    db.add(emp)
    db.commit()
    return emp


def _role_permission_keys(db: Session, role: Role | None) -> set[str]:
    if not role:
        return set()
    rows = (
        db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role.id)
        .all()
    )
    return {f"{p.module}:{p.permission_name}" for p in rows}


def _role_department_scope(role: Role | None) -> list[str]:
    """Departments a role's holder can see the roster of, once granted ESS
    Portal Access -- see GET /ess/team. Empty for a role with no department
    scoping configured (the default, and every role created before this
    field existed)."""
    if not role or not role.department_scope:
        return []
    try:
        parsed = json.loads(role.department_scope)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(d).strip() for d in parsed if str(d).strip()]


def require_permission(*keys: str):
    """Dependency factory for Employee-only routes — caller's role must grant
    at least one of the given permission keys. (For routes shared with User
    admins, use require_principal_permission below instead.)"""
    def _check(
        db: Session = Depends(get_db),
        emp: Employee = Depends(get_current_employee),
    ) -> Employee:
        role = db.get(Role, emp.role_id) if emp.role_id else None
        granted = _role_permission_keys(db, role)
        if not granted.intersection(keys):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
        return emp
    return _check


# ── unified principal (User admin OR Employee sub-user) ─────────────────────

@dataclass
class Principal:
    """Normalizes a User (admin) or Employee (RBAC sub-user) into one shape
    so shared endpoints don't need two separate code paths. Admins implicitly
    pass every permission check (`is_admin=True`); an Employee's `permissions`
    set is exactly what their assigned Role grants."""
    kind: str  # "user" | "employee" | "branch"
    company_id: str
    display_name: str
    is_admin: bool
    permissions: frozenset[str] = field(default_factory=frozenset)
    user: User | None = None
    employee: Employee | None = None
    # Branch Login Phase 2 — set only for kind="branch" (the Branch entity's
    # own shared login), mirroring user/employee above.
    branch: Branch | None = None
    role_name: str | None = None
    # None for a User (admin) principal, or an Employee not assigned to a
    # branch — both mean "company-wide", matching pre-Branch-Management
    # behavior. Set only for an Employee principal assigned to a Branch.
    branch_id: str | None = None
    # Branch Security Layer Phase 3: every branch this identity may switch
    # into — the primary branch_id above plus any EmployeeBranchAccess
    # rows. Empty for a User (admin, unrestricted by construction) and for
    # an Employee with no branch_id and no extra grants.
    accessible_branch_ids: frozenset[str] = field(default_factory=frozenset)

    def has(self, *keys: str) -> bool:
        return self.is_admin or bool(self.permissions.intersection(keys))

    def can_cross_branch(self, module: str) -> bool:
        """Opt-in cross-branch visibility (Branch Security Layer Phase 2) —
        separate from `branch_id` (which branch this identity is tied to)
        and from ordinary permissions (whether it can see the module at
        all). A branch-locked Employee normally only sees their own
        branch's data on every branch-aware endpoint; granting
        "<module>:view_all_branches" lets a specific employee see every
        branch's data for that one module without becoming a full admin."""
        return self.is_admin or self.has(f"{module}:view_all_branches")


def resolve_active_branch(principal: Principal, requested: str | None) -> str | None:
    """Branch Security Layer Phase 3 — the single resolver every branch-
    filtered endpoint calls instead of reading `principal.branch_id`
    directly, so a multi-branch employee's `?branch_id=` switcher works
    consistently everywhere (previously only app_data.py's generic
    collection endpoint had this opt-in, with no membership check).

    - Admin (unrestricted): `requested` passes straight through, same as
      today's admin branch-switcher opt-in.
    - An Employee with 0 or 1 accessible branches: `requested` is always
      ignored, returns `principal.branch_id` unchanged — this is the
      overwhelmingly common case (every employee before this phase) and
      preserves the already-tested "cannot escalate via the query param"
      guarantee exactly as before Phase 3 existed.
    - A genuinely multi-branch Employee: `requested` is honored only if
      it's one of their actually-assigned branches (never lets them peek
      at an unassigned branch); otherwise falls back to their primary
      branch, or an arbitrary accessible one if they have no primary.
    """
    if principal.is_admin:
        return requested
    accessible = principal.accessible_branch_ids
    if len(accessible) <= 1:
        return principal.branch_id
    if requested and requested in accessible:
        return requested
    return principal.branch_id or next(iter(accessible))


def _principal_from_employee_token(token: str, db: Session) -> Principal | None:
    # Employee tokens are self-describing (subject prefixed "emp:") — if the
    # prefix isn't present, this returns None immediately with no DB query,
    # so trying this path first costs nothing for the common admin case.
    emp_id = _employee_id_from_token(token)
    if not emp_id:
        return None
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Employee not found")
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    # Employee has no `company` relationship (just company_id) — a minimal
    # scalar query, same pattern require_module() already uses for its
    # employee-path fallback.
    expires_at = db.query(Company.subscription_expires_at).filter(Company.id == emp.company_id).scalar()
    assert_company_active(expires_at)
    emp.last_activity = datetime.now(UTC)
    db.add(emp)
    db.commit()
    role = db.get(Role, emp.role_id) if emp.role_id else None
    accessible_branch_ids = {row[0] for row in db.query(EmployeeBranchAccess.branch_id).filter(EmployeeBranchAccess.employee_id == emp.id).all()}
    if emp.branch_id:
        accessible_branch_ids.add(emp.branch_id)
    return Principal(
        kind="employee", company_id=emp.company_id, display_name=emp.full_name,
        is_admin=False, permissions=frozenset(_role_permission_keys(db, role)),
        employee=emp, role_name=role.role_name if role else None,
        branch_id=emp.branch_id, accessible_branch_ids=frozenset(accessible_branch_ids),
    )


def _branch_id_from_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        sub: str | None = payload.get("sub")
        if sub and sub.startswith(_BRANCH_PREFIX):
            return sub[len(_BRANCH_PREFIX):]
    except JWTError:
        pass
    return None


def _principal_from_branch_token(token: str, db: Session) -> Principal | None:
    """Branch Login Phase 2 — a Branch entity's own shared login, a third
    identity kind alongside User (admin) and Employee (RBAC sub-user).
    Branch tokens are self-describing (subject prefixed "branch:"), same
    mutual-exclusivity trick as Employee tokens' "emp:" prefix.

    Per the "module toggle alone = full access" design decision: a Branch
    principal has no Role of its own — its `permissions` are synthesized
    directly from the intersection of company-enabled and branch-enabled
    modules (BRANCH_ELIGIBLE_MODULES), granting "<module>:view" for each.
    This is enough to satisfy every existing require_principal_permission
    ("<module>:view") check without inventing a parallel permission model.
    `accessible_branch_ids` is exactly {branch.id} — no switching, matching
    "branches must be completely isolated" (no multi-branch concept for a
    Branch identity, unlike a multi-branch Employee)."""
    branch_id = _branch_id_from_token(token)
    if not branch_id:
        return None
    if is_impersonation_token_revoked(token):
        # Mirrors _principal_from_user_token()'s check — without this, an
        # ended "impersonate as branch" session (superadmin.py) kept working
        # as that branch until the token's natural expiry, defeating End
        # Impersonation entirely for this principal kind.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Impersonation session has ended")
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Branch not found")
    if branch.status != "Active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This branch is disabled")
    company = db.query(Company).filter(Company.id == branch.company_id).first()
    assert_company_active(company.subscription_expires_at if company else None)
    # Deferred import — dependencies.py imports Principal/get_current_principal
    # etc. FROM this module, so a module-level import here would be circular.
    from app.dependencies import branch_allows_module, company_allows_module
    company_modules = company.modules_enabled if company else None
    enabled = [
        m for m in BRANCH_ELIGIBLE_MODULES
        if company_allows_module(company_modules, m) and branch_allows_module(branch.modules_enabled, m)
    ]
    branch.last_activity = datetime.now(UTC)
    db.add(branch)
    db.commit()
    return Principal(
        kind="branch", company_id=branch.company_id, display_name=branch.name,
        is_admin=False, permissions=frozenset(f"{m}:view" for m in enabled),
        branch=branch, branch_id=branch.id, accessible_branch_ids=frozenset({branch.id}),
    )


def _principal_from_user_token(token: str, db: Session) -> Principal | None:
    user_id = user_id_from_token(token)
    if not user_id:
        return None
    if is_impersonation_token_revoked(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Impersonation session has ended")
    user = db.query(User).options(joinedload(User.company)).filter(User.id == user_id).first()
    if not user:
        return None
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    assert_company_active(user.company.subscription_expires_at if user.company else None)
    return Principal(
        kind="user", company_id=user.company_id, display_name=user.full_name,
        is_admin=True, permissions=frozenset(), user=user,
    )


def get_current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Resolves a User, Employee, or Branch bearer token into one Principal.
    The three token shapes are mutually exclusive by construction (only
    Employee tokens carry the "emp:" prefix, only Branch tokens "branch:"),
    so there's no ambiguity in trying one then the other."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = auth[7:]

    principal = _principal_from_employee_token(token, db)
    if principal:
        return principal
    principal = _principal_from_branch_token(token, db)
    if principal:
        return principal
    principal = _principal_from_user_token(token, db)
    if principal:
        return principal
    # Diagnostic only, no behavior change — see user_id_from_token()'s log
    # line for the underlying decode failure (this fires once all three
    # employee/branch/user resolution attempts came back empty for a
    # request that looked like a normal Bearer token).
    logger.warning("get_current_principal: all three token resolvers failed, returning 401")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")


def require_principal_permission(*keys: str):
    """Dependency factory for routes shared between User admins and Employee
    sub-users — admins always pass; an Employee principal needs at least one
    of the given permission keys."""
    def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has(*keys):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
        return principal
    return _check
