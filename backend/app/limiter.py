import os

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_client_ip(request: Request) -> str:
    # DigitalOcean's proxy APPENDS the real client IP to X-Forwarded-For
    # rather than replacing it — the FIRST entry is whatever the client
    # itself sent and is fully attacker-controlled (a client can set a
    # fresh fake value on every request to get a new rate-limit bucket each
    # time, bypassing every IP-keyed limit including /auth/login's). The
    # LAST entry is the one set by the nearest trusted hop — this app is
    # only ever reached through DigitalOcean App Platform's proxy, so that's
    # the value to trust.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


def _token_subject(token: str) -> str | None:
    """The user/employee id of a validly signed access token, else None (quietly: a bad
    token is the auth layer's to reject and log, not the rate limiter's)."""
    try:
        import jwt

        from app.config import get_settings
        from app.security import ALGORITHM

        sub = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM]).get("sub")
        return str(sub) if sub else None
    except Exception:  # JWTError, bad settings... -> fall back to the IP
        return None


def rate_limit_key(request: Request) -> str:
    """Signed-in requests are limited per login, not per IP: a whole office (or a
    company's staff on one mobile network) shares one public IP, and the PostgreSQL
    load test showed that IP hitting 429s long before the server was busy. Requests
    without a valid token (sign-in, sign-up, public pages) stay keyed by IP, so
    password guessing is still throttled. The signature is checked, so a client can't
    mint fresh buckets with made-up subjects."""
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        sub = _token_subject(auth[7:].strip())
        if sub:
            return f"user:{sub}"
    return _get_client_ip(request)


def _storage_uri() -> str:
    try:
        from app.config import get_settings
        url = get_settings().redis_url
        if url and not url.startswith("memory"):
            return url
    except Exception:
        pass
    return "memory://"


def _storage_options() -> dict:
    if _storage_uri().startswith("memory"):
        return {}
    from app.cache import CONNECTION_OPTIONS
    return dict(CONNECTION_OPTIONS)


# Disable rate limiting during automated tests so login fixtures never hit 429
_enabled = os.environ.get("TESTING", "").lower() not in ("1", "true", "yes")
limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=_storage_uri(),
    # Same timeouts as app.cache: a dropped Redis connection must not hang every API request.
    storage_options=_storage_options(),
    enabled=_enabled,
    default_limits=["300/minute"],
)
