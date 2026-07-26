from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import User
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
