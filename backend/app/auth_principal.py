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

from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.models import Employee, Permission, Role, RolePermission, User
from app.security import is_impersonation_token_revoked, user_id_from_token

settings = get_settings()
_EMP_PREFIX = "emp:"


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
    kind: str  # "user" | "employee"
    company_id: str
    display_name: str
    is_admin: bool
    permissions: frozenset[str] = field(default_factory=frozenset)
    user: User | None = None
    employee: Employee | None = None
    role_name: str | None = None

    def has(self, *keys: str) -> bool:
        return self.is_admin or bool(self.permissions.intersection(keys))


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
    emp.last_activity = datetime.now(UTC)
    db.add(emp)
    db.commit()
    role = db.get(Role, emp.role_id) if emp.role_id else None
    return Principal(
        kind="employee", company_id=emp.company_id, display_name=emp.full_name,
        is_admin=False, permissions=frozenset(_role_permission_keys(db, role)),
        employee=emp, role_name=role.role_name if role else None,
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
    return Principal(
        kind="user", company_id=user.company_id, display_name=user.full_name,
        is_admin=True, permissions=frozenset(), user=user,
    )


def get_current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Resolves either a User bearer token or an Employee bearer token into
    one Principal. The two token shapes are mutually exclusive by
    construction (only Employee tokens carry the "emp:" subject prefix), so
    there's no ambiguity in trying one then the other."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = auth[7:]

    principal = _principal_from_employee_token(token, db)
    if principal:
        return principal
    principal = _principal_from_user_token(token, db)
    if principal:
        return principal
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
