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


limiter = Limiter(key_func=get_remote_address, storage_uri=_storage_uri())
