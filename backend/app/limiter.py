import os

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_client_ip(request: Request) -> str:
    # DigitalOcean / proxies set X-Forwarded-For — use the real client IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


def _storage_uri() -> str:
    try:
        from app.config import get_settings
        url = get_settings().redis_url
        if url and not url.startswith("memory"):
            return url
    except Exception:
        pass
    return "memory://"


# Disable rate limiting during automated tests so login fixtures never hit 429
_enabled = os.environ.get("TESTING", "").lower() not in ("1", "true", "yes")
limiter = Limiter(
    key_func=_get_client_ip,
    storage_uri=_storage_uri(),
    enabled=_enabled,
    default_limits=["300/minute"],
)
