"""Request and database monitoring (docs/scaling-plan-10k.md, "Monitoring").

- Per-endpoint stats: count, 5xx errors, time histogram (for p50/p95) and
  database time, keyed by route template ("GET /api/v1/registers/{collection}"),
  in hourly buckets. Each request only updates in-process counters; a
  background thread adds them to Redis every FLUSH_SECONDS, so the superadmin
  System Health page sees every worker on every instance. Without Redis it
  shows this one process only (endpoint_stats() says which).
- Slow-request and slow-query log lines (SLOW_REQUEST_MS / SLOW_QUERY_MS).
  Query logs carry the SQL text only, never bound parameters.
- A Server-Timing header on API responses (app and db time, query count),
  readable in the browser's network panel.
- Sentry error tracking, only when SENTRY_DSN is set.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event

log = logging.getLogger("taxflow")

BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
FLUSH_SECONDS = 15
_KEEP_HOURS = 24
_REDIS_TTL = (_KEEP_HOURS + 2) * 3600


@dataclass
class _Stat:
    count: int = 0
    errors: int = 0
    total_ms: float = 0.0
    db_ms: float = 0.0
    queries: int = 0
    max_ms: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS_MS) + 1))

    def add(self, ms: float, error: bool, db_ms: float, queries: int) -> None:
        self.count += 1
        self.errors += int(error)
        self.total_ms += ms
        self.db_ms += db_ms
        self.queries += queries
        self.max_ms = max(self.max_ms, ms)
        self.buckets[_bucket(ms)] += 1

    def merge(self, other: "_Stat") -> None:
        self.count += other.count
        self.errors += other.errors
        self.total_ms += other.total_ms
        self.db_ms += other.db_ms
        self.queries += other.queries
        self.max_ms = max(self.max_ms, other.max_ms)
        self.buckets = [a + b for a, b in zip(self.buckets, other.buckets)]


def _bucket(ms: float) -> int:
    for i, upper in enumerate(BUCKETS_MS):
        if ms <= upper:
            return i
    return len(BUCKETS_MS)


def _hour(now: float | None = None) -> int:
    return int((now or time.time()) // 3600)


_lock = threading.Lock()
_local: dict[int, dict[str, _Stat]] = {}       # everything this process saw, last 24h
_unflushed: dict[int, dict[str, _Stat]] = {}   # not yet added to Redis
_flusher_started = False


def record(key: str, ms: float, status: int, db_ms: float = 0.0, queries: int = 0) -> None:
    hour = _hour()
    error = status >= 500
    with _lock:
        for store in (_local, _unflushed):
            store.setdefault(hour, {}).setdefault(key, _Stat()).add(ms, error, db_ms, queries)
        for old in [h for h in _local if h <= hour - _KEEP_HOURS]:
            del _local[old]
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    from app import cache

    with _lock:
        if _flusher_started:
            return
        _flusher_started = True
    if not cache.available():
        return
    threading.Thread(target=_flush_loop, name="monitoring-flush", daemon=True).start()


def _flush_loop() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        try:
            flush()
        except Exception as exc:  # never let monitoring take a worker down
            log.debug("monitoring flush failed: %s", exc)


# Redis has no "keep the larger value" hash command.
_HSET_MAX = (
    "local c = tonumber(redis.call('HGET', KEYS[1], ARGV[1]) or '0') "
    "if tonumber(ARGV[2]) > c then redis.call('HSET', KEYS[1], ARGV[1], ARGV[2]) end"
)


def flush() -> None:
    """Add this process's unflushed counters to Redis."""
    from app import cache

    r = cache._redis()
    if r is None:
        return
    with _lock:
        pending = dict(_unflushed)
        _unflushed.clear()
    if not pending:
        return
    try:
        pipe = r.pipeline(transaction=False)
        for hour, stats in pending.items():
            hkey = f"tf:mon:ep:{hour}"
            for key, s in stats.items():
                pipe.hincrby(hkey, f"{key}\tcount", s.count)
                pipe.hincrby(hkey, f"{key}\terrors", s.errors)
                pipe.hincrby(hkey, f"{key}\tqueries", s.queries)
                pipe.hincrbyfloat(hkey, f"{key}\ttotal_ms", round(s.total_ms, 1))
                pipe.hincrbyfloat(hkey, f"{key}\tdb_ms", round(s.db_ms, 1))
                for i, n in enumerate(s.buckets):
                    if n:
                        pipe.hincrby(hkey, f"{key}\tb{i}", n)
                pipe.eval(_HSET_MAX, 1, hkey, f"{key}\tmax_ms", round(s.max_ms, 1))
            pipe.expire(hkey, _REDIS_TTL)
        pipe.execute()
    except Exception:
        # Put the counters back so a Redis blip doesn't lose them.
        with _lock:
            for hour, stats in pending.items():
                for key, s in stats.items():
                    _unflushed.setdefault(hour, {}).setdefault(key, _Stat()).merge(s)
        raise


def _from_redis(hours: int) -> dict[str, _Stat] | None:
    from app import cache

    r = cache._redis()
    if r is None:
        return None
    now_hour = _hour()
    totals: dict[str, _Stat] = {}
    try:
        pipe = r.pipeline(transaction=False)
        for h in range(now_hour - hours + 1, now_hour + 1):
            pipe.hgetall(f"tf:mon:ep:{h}")
        for fields in pipe.execute():
            for name, value in (fields or {}).items():
                key, _, metric = name.rpartition("\t")
                s = totals.setdefault(key, _Stat())
                v = float(value)
                if metric == "max_ms":
                    s.max_ms = max(s.max_ms, v)
                elif metric.startswith("b") and metric[1:].isdigit():
                    s.buckets[int(metric[1:])] += int(v)
                elif metric in ("count", "errors", "queries"):
                    setattr(s, metric, getattr(s, metric) + int(v))
                elif metric in ("total_ms", "db_ms"):
                    setattr(s, metric, getattr(s, metric) + v)
    except Exception:
        return None
    return totals


def _percentile(buckets: list[int], count: int, p: float) -> float | None:
    """Upper edge of the histogram bucket holding the p-th percentile request.
    None for the open-ended last bucket (slower than the largest edge)."""
    if not count:
        return None
    target = p * count
    seen = 0
    for i, n in enumerate(buckets):
        seen += n
        if seen >= target:
            return float(BUCKETS_MS[i]) if i < len(BUCKETS_MS) else None
    return None


def endpoint_stats(hours: int = 1, limit: int = 20) -> dict[str, Any]:
    """Slowest endpoints over the last `hours`, ranked by total time spent
    (count x average), which is what most loads the servers."""
    hours = max(1, min(hours, _KEEP_HOURS))
    totals = _from_redis(hours)
    cluster_wide = totals is not None
    if totals is None:
        totals = {}
        now_hour = _hour()
        with _lock:
            for h, stats in _local.items():
                if h > now_hour - hours:
                    for key, s in stats.items():
                        totals.setdefault(key, _Stat()).merge(s)
    rows = []
    for key, s in totals.items():
        if not s.count:
            continue
        p95 = _percentile(s.buckets, s.count, 0.95)
        rows.append({
            "endpoint": key,
            "count": s.count,
            "errors": s.errors,
            "avg_ms": round(s.total_ms / s.count, 1),
            "p50_ms": _percentile(s.buckets, s.count, 0.50),
            "p95_ms": p95,
            "p95_over_ms": BUCKETS_MS[-1] if p95 is None else None,
            "max_ms": round(s.max_ms, 1),
            "avg_db_ms": round(s.db_ms / s.count, 1),
            "avg_queries": round(s.queries / s.count, 1),
            "total_s": round(s.total_ms / 1000, 1),
        })
    rows.sort(key=lambda r: r["total_s"], reverse=True)
    return {"cluster_wide": cluster_wide, "hours": hours, "endpoints": rows[:limit]}


# ── Per-request database time ───────────────────────────────────────────────

_request_db: contextvars.ContextVar[list | None] = contextvars.ContextVar("taxflow_request_db", default=None)
_timed_engines: set[int] = set()


def install_query_timing(engine, slow_query_ms: int) -> None:
    """Time every query on `engine`: add it to the current request's db total
    and log any slower than slow_query_ms. Installing twice is a no-op."""
    if id(engine) in _timed_engines:
        return
    _timed_engines.add(id(engine))

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_mon_t0", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        starts = conn.info.get("_mon_t0")
        if not starts:
            return
        ms = (time.perf_counter() - starts.pop()) * 1000
        acc = _request_db.get()
        if acc is not None:
            acc[0] += ms
            acc[1] += 1
        if slow_query_ms and ms >= slow_query_ms:
            sql = " ".join(statement.split())
            log.warning("slow query %.0fms: %s", ms, sql[:500])


# ── Middleware ───────────────────────────────────────────────────────────────

def route_template(scope: dict) -> str:
    """The matched route's full template, e.g. "/api/v1/app-data/registers/{collection}".

    FastAPI up to 0.11x copied included routes onto the app with the include_router()
    prefix already in route.path; newer versions (0.14x, what requirements.txt installs)
    keep routers nested, so route.path is relative to the prefix. The prefix is whatever
    part of the request path comes before the part the route's own pattern matches."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return "(unmatched)"
    path = scope.get("path") or ""
    regex = getattr(route, "path_regex", None)
    if regex is None or regex.match(path):
        return template
    for i, ch in enumerate(path):
        if ch == "/" and i and regex.match(path[i:]):
            return path[:i] + template
    return template


def _is_monitored(path: str) -> bool:
    """Requests timed by app/http_middleware.py (which records them here)."""
    return path.startswith("/api/") or path.startswith("/iclock/")


# ── Sentry ───────────────────────────────────────────────────────────────────

_sentry_on = False
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"})


def _scrub_event(event: dict, _hint: dict) -> dict:
    """Last check before an event leaves: no credentials, cookies, query strings or bodies."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: ("[Filtered]" if k.lower() in _SENSITIVE_HEADERS else v) for k, v in headers.items()}
        for key in ("cookies", "data", "query_string"):
            request.pop(key, None)
    return event


def init_sentry(dsn: str | None, environment: str, traces_sample_rate: float) -> bool:
    global _sentry_on
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed; error tracking is off")
        return False
    import os

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=os.environ.get("APP_VERSION") or None,
            traces_sample_rate=traces_sample_rate,
            send_default_pii=False,
            # Stack-frame local variables are on by default and held bearer tokens
            # (ASGI scope headers), request bodies with passwords, payroll figures...
            include_local_variables=False,
            before_send=_scrub_event,
        )
    except Exception as exc:
        # sentry-sdk 2.19 crashed at init on Starlette 1.x (it imported Jinja2Templates
        # without jinja2): error tracking must never stop the app from starting.
        log.warning("Sentry could not start (%s: %s); error tracking is off", type(exc).__name__, exc)
        return False
    _sentry_on = True
    log.info("Sentry error tracking enabled (%s)", environment)
    return True


def capture_exception(exc: BaseException) -> None:
    """Report an error that an exception handler turned into a response (and so
    Sentry's own integration might not see). No-op unless Sentry is on."""
    if not _sentry_on:
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:
        pass
