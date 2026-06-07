"""
Thin Redis cache wrapper.
Falls back to a no-op when Redis is unavailable (local dev with memory://).
All values are JSON-serialised; keys are namespaced with "tf:".
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_tried = False


def _redis():
    global _client, _tried
    if _tried:
        return _client
    _tried = True
    try:
        from app.config import get_settings
        url = get_settings().redis_url
        if not url or url.startswith("memory"):
            return None
        import redis as _redis_lib
        c = _redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=2)
        c.ping()
        _client = c
        logger.info("Redis cache connected")
    except Exception as exc:
        logger.warning("Redis unavailable — caching disabled: %s", exc)
        _client = None
    return _client


def get(key: str) -> Any | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(f"tf:{key}")
        return json.loads(raw) if raw is not None else None
    except Exception:
        return None


def set(key: str, value: Any, ttl: int = 60) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.setex(f"tf:{key}", ttl, json.dumps(value, default=str))
    except Exception:
        pass


def delete(key: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.delete(f"tf:{key}")
    except Exception:
        pass


def delete_prefix(prefix: str) -> None:
    """Delete all keys matching tf:<prefix>*"""
    r = _redis()
    if r is None:
        return
    try:
        keys = r.keys(f"tf:{prefix}*")
        if keys:
            r.delete(*keys)
    except Exception:
        pass


def invalidate_company(company_id: str) -> None:
    """Bust all report caches for a company after a write operation."""
    delete(f"dashboard:{company_id}")
    delete(f"summary:{company_id}")
    delete(f"trial_balance:{company_id}")
    delete_prefix(f"vat_return:{company_id}:")
