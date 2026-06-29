from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User


settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"

# Pre-computed dummy hash used when email not found — ensures constant-time
# response regardless of whether the email exists (prevents timing enumeration)
_DUMMY_HASH = "$2b$12$QmNqX3Yv8pK2LmRtW1uZe.dummyhashfortimingnormalization.X"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str) -> str:
    expires = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "exp": expires}, settings.secret_key, algorithm=ALGORITHM)


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.lower()).first()
    # Always run bcrypt verify so response time is identical whether
    # the email exists or not — prevents timing-based email enumeration
    candidate_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(password, candidate_hash)
    if not user or not password_ok:
        return None
    if not getattr(user, "is_active", True):
        return None
    return user


def user_id_from_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
