import json

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Company, User
from app.security import is_impersonation_token_revoked, user_id_from_token

# Re-exported so routers keep importing all request-auth dependencies from
# this one conventional module, whether the route needs admin-only,
# employee-only, or either (Principal-based) auth.
from app.auth_principal import (  # noqa: F401
    Principal,
    get_current_employee,
    get_current_principal,
    require_permission,
    require_principal_permission,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user_id = user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    # Only impersonation tokens carry a "jti", so this is a no-op (no extra Redis
    # round-trip) for every normal user session — it only applies to the rare
    # superadmin-impersonating-a-company case.
    if is_impersonation_token_revoked(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Impersonation session has ended")
    user = db.query(User).options(joinedload(User.company)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    return user


def company_allows_module(modules_enabled_json: str | None, module_key: str) -> bool:
    """`companies.modules_enabled` semantics: NULL/empty means unrestricted
    (every module allowed) — matches the frontend's own fallback in
    applyModulePermissionNav() and the bootstrap payload in app_data.py, so a
    company predating this column (or never explicitly restricted) never
    loses access as a side effect of this check existing."""
    if not modules_enabled_json:
        return True
    try:
        allowed = json.loads(modules_enabled_json)
    except (TypeError, ValueError):
        return True
    return module_key in allowed


def require_module(module_key: str):
    """Dependency factory enforcing superadmin's per-company Module
    Permissions server-side. The frontend sidebar hide (applyModulePermissionNav
    in app.js) is UI politeness only, same as the existing HRMS per-employee
    RBAC nav guard — the real boundary has to be here, otherwise a disabled
    module's data is still reachable via a direct API call. Built on
    get_current_principal (not get_current_user) so it composes with both
    User-admin and Employee-sub-user routes (Payroll/Leave/Attendance/HR
    admin) without duplicating the check per auth style."""
    def _check(
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> Principal:
        if principal.kind == "user" and principal.user is not None and principal.user.company is not None:
            modules_enabled = principal.user.company.modules_enabled
        else:
            modules_enabled = db.query(Company.modules_enabled).filter(Company.id == principal.company_id).scalar()
        if not company_allows_module(modules_enabled, module_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The '{module_key}' module is not enabled for your company",
            )
        return principal
    return _check
