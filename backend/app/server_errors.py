"""Recent server errors for Super Admin > Errors: every 500 (a bug) and 503 (overloaded:
the database connection pool ran out, e.g. a page firing hundreds of saves at once).

Kept in Redis -- a capped list shared by every instance -- rather than the database,
because the moment worth recording most is the one where the database can't hand out a
connection: writing the error there would wait for that same pool. Without Redis (local
runs) each process keeps its own list in memory. Recording never raises or slows a
response: one LPUSH with the cache client's short timeouts.
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app import cache

logger = logging.getLogger(__name__)

_KEY = "tf:server_errors"
_MAX = 1000
_memory: deque[dict[str, Any]] = deque(maxlen=_MAX)
_lock = threading.Lock()


def _who(authorization: str | None) -> dict[str, str | None]:
    """The token's subject, without touching the database: a user id, or "emp:<id>" /
    "branch:<id>" for employee and branch logins. Super Admin resolves names on read."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"subject": None}
    try:
        import jwt

        from app.config import get_settings

        payload = jwt.decode(authorization[7:], get_settings().secret_key, algorithms=["HS256"],
                             options={"verify_exp": False})
        sub = payload.get("sub")
        return {"subject": str(sub)[:80] if sub else None}
    except Exception:
        return {"subject": None}


def record(*, status: int, method: str, path: str, query: str, exc: BaseException,
           authorization: str | None, build: str | None = None) -> None:
    try:
        action = ""
        for part in (query or "").split("&"):
            if part.startswith("action="):
                action = part[7:][:40]
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "kind": "overload" if status == 503 else "error",
            "status": status,
            "method": method,
            "path": path[:200],
            "action": action or None,
            "error": type(exc).__name__,
            "message": str(exc)[:400],
            "trace": None if status == 503 else "".join(traceback.format_exception(exc))[-3000:],
            "build": build,
            **_who(authorization),
        }
        r = cache._redis()
        if r is not None:
            try:
                pipe = r.pipeline()
                pipe.lpush(_KEY, json.dumps(entry))
                pipe.ltrim(_KEY, 0, _MAX - 1)
                pipe.execute()
                return
            except Exception:
                pass
        with _lock:
            _memory.appendleft(entry)
    except Exception:
        logger.debug("could not record server error", exc_info=True)


def recent(limit: int = 500) -> tuple[list[dict[str, Any]], str]:
    """Newest first, and where they came from ("redis" = every instance, "memory" = this one)."""
    r = cache._redis()
    if r is not None:
        try:
            return [json.loads(x) for x in r.lrange(_KEY, 0, max(0, limit - 1))], "redis"
        except Exception:
            pass
    with _lock:
        return list(_memory)[:limit], "memory"


def clear() -> int:
    r = cache._redis()
    if r is not None:
        try:
            n = r.llen(_KEY)
            r.delete(_KEY)
            return int(n or 0)
        except Exception:
            pass
    with _lock:
        n = len(_memory)
        _memory.clear()
        return n
