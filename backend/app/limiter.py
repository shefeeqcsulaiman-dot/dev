import os

from slowapi import Limiter
from slowapi.util import get_remote_address


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
limiter = Limiter(key_func=get_remote_address, storage_uri=_storage_uri(), enabled=_enabled)
