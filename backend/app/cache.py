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


def incr_gauge(key: str) -> None:
    """Increment a live counter with no expiry (e.g. in-flight request count) —
    paired incr_gauge()/decr_gauge() calls around a unit of work. No-ops
    without Redis; see request_metrics.py for the in-memory fallback."""
    r = _redis()
    if r is None:
        return
    try:
        r.incr(f"tf:{key}")
    except Exception:
        pass


def decr_gauge(key: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.decr(f"tf:{key}")
    except Exception:
        pass


def get_gauge(key: str) -> int | None:
    """None means Redis is unavailable (caller should fall back), not that
    the count is unknown/zero — an untouched gauge reads back as 0."""
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(f"tf:{key}")
        return int(raw) if raw is not None else 0
    except Exception:
        return None


def incr_window(key: str, ttl: int) -> None:
    """Increment a counter that expires after `ttl` seconds — used for
    fixed-window rate counters (e.g. "requests this minute")."""
    r = _redis()
    if r is None:
        return
    try:
        pipe = r.pipeline()
        pipe.incr(f"tf:{key}")
        pipe.expire(f"tf:{key}", ttl)
        pipe.execute()
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
    """Delete all keys matching tf:<prefix>*.

    SCAN, not KEYS: KEYS walks the whole keyspace in one blocking call, and this runs
    on every company write (invalidate_company), so with thousands of companies'
    keys it would stall Redis for everyone."""
    r = _redis()
    if r is None:
        return
    try:
        batch = []
        for key in r.scan_iter(match=f"tf:{prefix}*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                r.delete(*batch)
                batch = []
        if batch:
            r.delete(*batch)
    except Exception:
        pass


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
    # trial_balance is written per-branch (reports.py: f"trial_balance:{company_id}:{branch_id or 'all'}"),
    # so a plain delete() here never matched any actual key — the cache only
    # ever cleared itself via its own TTL, not on writes.
    delete_prefix(f"trial_balance:{company_id}:")
    delete_prefix(f"vat_return:{company_id}:")
    delete(f"bootstrap:{company_id}")
