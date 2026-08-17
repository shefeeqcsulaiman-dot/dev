"""Best-effort request-concurrency tracking for the superadmin "live load"
panel. Uses Redis (cluster-wide, shared across every instance/worker) via
app.cache when it's configured; falls back to this single process's own
in-memory counters when it isn't. Callers must treat the fallback as
"this worker only" — with multiple instances/workers, the real cluster
total is higher and unobservable without Redis.
"""
import threading
import time
from collections import deque

import app.cache as cache

_INFLIGHT_KEY = "load:inflight"
_RATE_WINDOW_SECONDS = 60

_local_in_flight = 0
_local_lock = threading.Lock()
_local_recent: deque = deque()  # start-timestamps within the last 60s


def _prune(now: float) -> None:
    cutoff = now - _RATE_WINDOW_SECONDS
    while _local_recent and _local_recent[0] < cutoff:
        _local_recent.popleft()


def request_started() -> None:
    global _local_in_flight
    now = time.time()
    with _local_lock:
        _local_in_flight += 1
        _local_recent.append(now)
        _prune(now)
    cache.incr_gauge(_INFLIGHT_KEY)
    cache.incr_window(f"load:rate:{int(now // _RATE_WINDOW_SECONDS)}", ttl=_RATE_WINDOW_SECONDS * 3)


def request_finished() -> None:
    global _local_in_flight
    with _local_lock:
        _local_in_flight = max(0, _local_in_flight - 1)
    cache.decr_gauge(_INFLIGHT_KEY)


def snapshot() -> dict:
    """Returns in-flight request count and requests/minute. cluster_wide=True
    means the numbers cover every instance/worker (via Redis); False means
    they only reflect whichever single process happened to answer this
    request — a real but partial signal, not the whole picture."""
    now = time.time()
    with _local_lock:
        _prune(now)
        local_in_flight = _local_in_flight
        local_last_minute = len(_local_recent)

    redis_in_flight = cache.get_gauge(_INFLIGHT_KEY)
    if redis_in_flight is not None:
        # Current fixed-minute bucket, not a true sliding window — resets on
        # the minute, so a value seen 2 seconds into a new minute is genuinely
        # "2 seconds' worth," not a bug. Good enough for a live-load glance.
        this_bucket = int(now // _RATE_WINDOW_SECONDS)
        redis_this_minute = cache.get_gauge(f"load:rate:{this_bucket}") or 0
        return {
            "cluster_wide": True,
            "in_flight": max(redis_in_flight, 0),
            "requests_this_minute": redis_this_minute,
        }

    return {
        "cluster_wide": False,
        "in_flight": local_in_flight,
        "requests_this_minute": local_last_minute,
    }
