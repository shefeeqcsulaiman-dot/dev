import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User


logger = logging.getLogger(__name__)
settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"

# Never the key itself — a short, stable hash prefix logged alongside a
# decode failure. If SECRET_KEY somehow differs across instances/workers
# (e.g. a stale value left over from a prior redeploy), every instance would
# otherwise fail identically-looking-but-differently-signed tokens with no
# way to tell that apart from a genuinely invalid/expired token — this shows
# up directly in logs as differing prefixes across instances.
_SECRET_KEY_FINGERPRINT = hashlib.sha256(settings.secret_key.encode()).hexdigest()[:8]

# Pre-computed dummy hash used when email not found — ensures constant-time
# response regardless of whether the email exists (prevents timing enumeration)
_DUMMY_HASH = "$2b$12$Z2HUw9SswHis7rcngsd7iOdXn/b9HafcmcwJx9D39ozeKwrSy22r."


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, impersonated_by: str | None = None) -> str:
    expires = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict = {"sub": subject, "exp": expires}
    if impersonated_by:
        payload["imp"] = impersonated_by
        # Only impersonation tokens carry a jti — it's what lets "End Impersonation"
        # actually revoke this specific token instead of just logging that it happened.
        payload["jti"] = secrets.token_urlsafe(16)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


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
    except JWTError as exc:
        # Diagnostic only, no behavior change — added after a load test found
        # a single valid, unexpired token intermittently returning 401 under
        # concurrent load, with no logged reason. secret_fp lets a genuine
        # SECRET_KEY mismatch across instances/workers be spotted directly
        # (differing fingerprints on the same "valid" token) instead of
        # guessed at.
        logger.warning("JWT decode failed: %s: %s (secret_fp=%s)", type(exc).__name__, exc, _SECRET_KEY_FINGERPRINT)
        return None


def impersonator_id_from_token(token: str) -> str | None:
    """Returns the superadmin user id if this token was issued as an impersonation session."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload.get("imp")
    except JWTError:
        return None


def impersonation_revocation_info(token: str) -> tuple[str, int] | None:
    """For an impersonation token, returns (jti, seconds_until_natural_expiry).

    Returns None for non-impersonation tokens (no "imp"/"jti" claim) or invalid
    tokens — callers should treat that as "nothing to revoke."
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not payload.get("imp") or not jti or not exp:
        return None
    remaining = int(exp - datetime.now(UTC).timestamp())
    return (jti, max(remaining, 1))


def is_impersonation_token_revoked(token: str) -> bool:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return False
    jti = payload.get("jti")
    if not payload.get("imp") or not jti:
        return False
    import app.cache as cache
    return bool(cache.get(f"revoked_imp:{jti}"))
