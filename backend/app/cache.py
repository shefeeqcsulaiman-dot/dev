"""
Thin Redis cache wrapper.
Falls back to a no-op when Redis is unavailable (local dev with memory://).
All values are JSON-serialised; keys are namespaced with "tf:".
"""
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_tried = False
# Without socket_timeout a read on a connection the server (or the network) silently dropped
# blocks forever: on 2026-10-09/10 about 1 in 7 /health calls on e4cs.com hung 15-20 s after
# idle periods, tying up worker threads. health_check_interval pings a connection that sat idle
# before reusing it; a timeout makes a dead one fail fast (callers already treat errors as a miss).
CONNECTION_OPTIONS = {
    "socket_connect_timeout": 2,
    "socket_timeout": 2,
    "socket_keepalive": True,
    "health_check_interval": 30,
    # No retry_on_timeout: with health_check_interval it retries forever against a server that
    # accepts but never answers (redis-py 5.2), i.e. the very hang this is meant to prevent.
}
_retry_at = 0.0          # after a failed connect, try again from this time (monotonic)
_RETRY_SECONDS = 30
last_error: str | None = None  # error type of the last failed connect (shown by public /health)


def url_problem(url: str | None) -> str | None:
    """A likely mistake in REDIS_URL, described without revealing the value (it's
    encrypted in DigitalOcean, so this is the only way to see what was saved)."""
    if not url or url.startswith("memory"):
        return None
    if url != url.strip() or url[:1] in "'\"" or url[-1:] in "'\"":
        return "spaces or quotes around the value"
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "not a valid URL"
    if parts.scheme == "redis" and ".db.ondigitalocean.com" in (parts.hostname or ""):
        return "starts with redis:// - DigitalOcean needs rediss:// (two s)"
    if parts.scheme not in ("redis", "rediss", "unix"):
        return "must start with rediss://"
    if (parts.hostname or "").startswith("private-"):
        return "private (VPC) host - use the Public network connection string"
    if (parts.password or "").upper() == "PASSWORD":
        return "password is the placeholder PASSWORD"
    if ".db.ondigitalocean.com" not in (parts.hostname or ""):
        return None
    if not parts.password:
        return "no password in the URL"
    if port != 25061:
        return f"port {port} - DigitalOcean's is 25061"
    return None


def _redis():
    global _client, _tried, _retry_at, last_error
    if _tried and (_client is not None or time.monotonic() < _retry_at):
        return _client
    _tried = True
    try:
        from app.config import get_settings
        url = get_settings().redis_url
        if not url or url.startswith("memory"):
            return None
        import redis as _redis_lib
        c = _redis_lib.from_url(url, decode_responses=True, **CONNECTION_OPTIONS)
        c.ping()
        _client = c
        last_error = None
        logger.info("Redis cache connected")
    except Exception as exc:
        # Unreachable (e.g. trusted sources not set yet): run without it, retry later.
        logger.warning("Redis unavailable — caching disabled: %s", exc)
        _client = None
        last_error = type(exc).__name__
        _retry_at = time.monotonic() + _RETRY_SECONDS
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


_STALE_SAFETY_NET_TTL = 24 * 60 * 60  # 1 day — see get_with_staleness()'s docstring


def set_with_staleness(key: str, value: Any) -> None:
    """Writes {data, cached_at} with a long (24h) TTL, instead of the short
    freshness window a plain set() would use — see get_with_staleness()."""
    r = _redis()
    if r is None:
        return
    try:
        envelope = {"data": value, "cached_at": time.time()}
        r.setex(f"tf:{key}", _STALE_SAFETY_NET_TTL, json.dumps(envelope, default=str))
    except Exception:
        pass


def get_with_staleness(key: str, fresh_seconds: int) -> tuple[Any | None, bool]:
    """Returns (data, is_fresh). data is None if there's no cached value at
    all (nothing written in the last 24h) or up to `fresh_seconds` old
    ("here's current data") — or older than that but still present
    ("stale but better than an error", the whole point of this pair of
    functions: callers use this to serve a company's last-known dashboard/
    summary when a fresh recompute fails under load, instead of a hard
    error, for anyone who's loaded it at all in the last day."""
    r = _redis()
    if r is None:
        return None, False
    try:
        raw = r.get(f"tf:{key}")
        if raw is None:
            return None, False
        envelope = json.loads(raw)
        cached_at = envelope.get("cached_at", 0)
        is_fresh = (time.time() - cached_at) <= fresh_seconds
        return envelope.get("data"), is_fresh
    except Exception:
        return None, False


def available() -> bool:
    """True if Redis is actually connected — callers use this to distinguish
    "0 requests right now" from "no cluster-wide data, Redis is down"."""
    return _redis() is not None


def delete(key: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.delete(f"tf:{key}")
    except Exception:
        pass


# A group is a Redis set holding the names of cache keys that are dropped together
# (invalidate_company). It replaces deleting by key prefix: SCAN walks the whole keyspace
# 500 keys per call, so with thousands of companies every write paid hundreds of Redis
# round trips. Members may outlive their keys; deleting a missing key is harmless.
_GROUP_TTL = 3600  # longer than any member's TTL; refreshed on every add


def _group_key(group: str) -> str:
    return f"tf:grp:{group}"


def set_in_group(key: str, value: Any, ttl: int, group: str) -> None:
    """set(), and record the key in `group` so invalidate_company() can find it."""
    r = _redis()
    if r is None:
        return
    try:
        pipe = r.pipeline(transaction=False)
        pipe.setex(f"tf:{key}", ttl, json.dumps(value, default=str))
        pipe.sadd(_group_key(group), f"tf:{key}")
        pipe.expire(_group_key(group), _GROUP_TTL)
        pipe.execute()
    except Exception:
        pass


def report_group(company_id: str) -> str:
    """Group of a company's report caches cleared on every write (trial balance, VAT return)."""
    return f"reports:{company_id}"


class LocalTTLCache:
    """Per-process cache with a size limit: the fallback when Redis isn't there.
    Entries expire after their TTL and the least recently used go first once
    `max_entries` is reached, so memory stays bounded however many companies use it."""

    def __init__(self, max_entries: int):
        from collections import OrderedDict
        import threading

        self.max_entries = max_entries
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            if hit[0] <= time.monotonic():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return hit[1]

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


def remember(key: str, ttl: int, compute, local: LocalTTLCache) -> Any:
    """Cached value for `key`, computed on a miss: shared across servers through Redis
    when it's connected, otherwise kept in `local` (this process only, bounded)."""
    if available():
        hit = get(key)
        if hit is not None:
            return hit
        value = compute()
        set(key, value, ttl=ttl)
        return value
    hit = local.get(key)
    if hit is not None:
        return hit
    value = compute()
    local.set(key, value, ttl)
    return value


# Companies that just wrote something (app.read_replica keeps their reports on the primary
# for a short window); set by invalidate_company(), shared through Redis when connected.
_recent_writes = LocalTTLCache(max_entries=20000)


def recently_written(company_id: str) -> bool:
    return company_id in _recent_writes or get(f"recent_write:{company_id}") is not None


def invalidate_company(company_id: str) -> None:
    """Bust report caches for a company after a write operation.

    dashboard/summary are deliberately NOT invalidated here (TTL-only, 60s/
    120s respectively) — this fires on every single write anywhere in the
    company, so with instant invalidation a live company with any regular
    activity almost never got a cache hit on its two heaviest report
    endpoints, paying full recompute cost (dozens of sequential queries)
    on nearly every load. A dashboard/summary being up to 60-120s stale
    after a write is an accepted tradeoff for a large jump in cache-hit
    rate under real traffic."""
    # trial_balance (per branch) and vat_return (per period) are cached with
    # set_in_group(report_group(...)), so they're found without scanning keys.
    # One pipelined round trip, plus one more when the group has members.
    from app.config import get_settings

    ttl = max(1, get_settings().read_replica_write_window_seconds)
    _recent_writes.set(company_id, True, ttl)
    r = _redis()
    if r is None:
        return
    try:
        group = _group_key(report_group(company_id))
        pipe = r.pipeline(transaction=False)
        pipe.smembers(group)
        pipe.delete(f"tf:bootstrap:{company_id}")
        pipe.setex(f"tf:recent_write:{company_id}", ttl, json.dumps(1))
        members = pipe.execute()[0]
        if members:
            pipe = r.pipeline(transaction=False)
            pipe.delete(*members)
            # SREM rather than deleting the set: a key cached between the two calls stays listed.
            pipe.srem(group, *members)
            pipe.execute()
    except Exception:
        pass
