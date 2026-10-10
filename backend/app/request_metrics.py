"""Best-effort request-concurrency tracking for the superadmin "live load"
panel. Counted in this process's memory on every request; when Redis is
configured, a background thread adds them to cluster-wide figures every few
seconds (same pattern as app/monitoring.py). Without Redis the panel shows
"this worker only" — with multiple instances/workers, the real cluster total
is higher and unobservable.

Requests used to INCR/DECR Redis directly: three blocking round trips on the
event loop for every API request, stalling every other request on that worker
while they waited.
"""
import os
import socket
import threading
import time
import uuid
from collections import deque

import app.cache as cache

_RATE_WINDOW_SECONDS = 60
_FLUSH_SECONDS = 2
# Hash of process id -> "<in flight>|<unix time>", written by each process's flush.
_PROCS_KEY = "tf:load:procs"
_PROC_STALE_SECONDS = 15   # a process that hasn't reported for this long has gone
_PROC_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

_local_in_flight = 0
_local_lock = threading.Lock()
_local_recent: deque = deque()  # start-timestamps within the last 60s
_unflushed: dict[int, int] = {}  # minute bucket -> requests not yet added to Redis
_flusher_started = False
_next_flusher_check = 0.0


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
        bucket = int(now // _RATE_WINDOW_SECONDS)
        _unflushed[bucket] = _unflushed.get(bucket, 0) + 1
    _ensure_flusher(now)


def request_finished() -> None:
    global _local_in_flight
    with _local_lock:
        _local_in_flight = max(0, _local_in_flight - 1)


def _ensure_flusher(now: float) -> None:
    """Start the flush thread once Redis is reachable (checked at most every 30 s)."""
    global _flusher_started, _next_flusher_check
    if _flusher_started or now < _next_flusher_check:
        return
    with _local_lock:
        if _flusher_started or now < _next_flusher_check:
            return
        _next_flusher_check = now + 30
    if not cache.available():
        return
    with _local_lock:
        if _flusher_started:
            return
        _flusher_started = True
    threading.Thread(target=_flush_loop, name="load-metrics-flush", daemon=True).start()


def _flush_loop() -> None:
    while True:
        time.sleep(_FLUSH_SECONDS)
        try:
            flush()
        except Exception:  # never let the live-load panel take a worker down
            pass


def flush() -> None:
    """Add this process's request counts to Redis and report its in-flight count."""
    r = cache._redis()
    if r is None:
        return
    with _local_lock:
        pending = dict(_unflushed)
        _unflushed.clear()
        in_flight = _local_in_flight
    try:
        pipe = r.pipeline(transaction=False)
        for bucket, n in pending.items():
            pipe.incrby(f"tf:load:rate:{bucket}", n)
            pipe.expire(f"tf:load:rate:{bucket}", _RATE_WINDOW_SECONDS * 3)
        pipe.hset(_PROCS_KEY, _PROC_ID, f"{in_flight}|{time.time():.0f}")
        pipe.expire(_PROCS_KEY, 3600)
        pipe.execute()
    except Exception:
        with _local_lock:  # put the counts back so a Redis blip doesn't lose them
            for bucket, n in pending.items():
                _unflushed[bucket] = _unflushed.get(bucket, 0) + n
        raise


def _cluster_figures(r, now: float, local_in_flight: int) -> tuple[int, int]:
    """(in flight, requests this minute) across every process that reported recently."""
    in_flight = 0
    stale = []
    for proc, value in (r.hgetall(_PROCS_KEY) or {}).items():
        if proc == _PROC_ID:
            continue  # this process's own figure is counted live below
        try:
            count, reported_at = value.split("|")
            if now - float(reported_at) > _PROC_STALE_SECONDS:
                stale.append(proc)
                continue
            in_flight += int(count)
        except ValueError:
            stale.append(proc)
    if stale:
        r.hdel(_PROCS_KEY, *stale)
    bucket = int(now // _RATE_WINDOW_SECONDS)
    with _local_lock:
        own_unflushed = _unflushed.get(bucket, 0)
    this_minute = int(r.get(f"tf:load:rate:{bucket}") or 0) + own_unflushed
    return in_flight + local_in_flight, this_minute


def snapshot() -> dict:
    """Returns in-flight request count and requests/minute. cluster_wide=True
    means the numbers cover every instance/worker (via Redis, up to a few
    seconds old); False means they only reflect whichever single process
    happened to answer this request — a real but partial signal."""
    now = time.time()
    with _local_lock:
        _prune(now)
        local_in_flight = _local_in_flight
        local_last_minute = len(_local_recent)

    r = cache._redis()
    if r is not None:
        try:
            # Current fixed-minute bucket, not a true sliding window — resets on
            # the minute, so a value seen 2 seconds into a new minute is genuinely
            # "2 seconds' worth," not a bug. Good enough for a live-load glance.
            in_flight, this_minute = _cluster_figures(r, now, local_in_flight)
            return {
                "cluster_wide": True,
                "in_flight": max(in_flight, 0),
                "requests_this_minute": this_minute,
            }
        except Exception:
            pass

    return {
        "cluster_wide": False,
        "in_flight": local_in_flight,
        "requests_this_minute": local_last_minute,
    }
