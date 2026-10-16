"""How many people are using the app: Super Admin's "Active users" panel.

Every login records its last request time, at most once a minute: company users in
users.last_activity, employees (ESS and HRMS sub-users) in employees.last_activity, branch
logins in branches.last_activity (app/auth_principal.py). "Active in the last N days" is then
just last_activity >= now - N days: whoever was active at any point in the window has a
last_activity inside it.

"Online now" = active in the last 15 minutes. Its highest value per hour is kept (Redis, or
this process only without it) so the panel can show the peak of the last 24 hours: one
process samples it once a minute (a Redis key per minute decides which).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

import app.cache as cache
from app.models import Branch, Company, Employee, User

log = logging.getLogger("taxflow")

ONLINE_MINUTES = 15
_WINDOWS = (("online", timedelta(minutes=ONLINE_MINUTES)), ("last_24h", timedelta(hours=24)),
            ("last_7d", timedelta(days=7)), ("last_30d", timedelta(days=30)))
_PEAK_KEY = "tf:active:peak:{hour}"
ONLINE_MINUTE_KEY = "tf:active:min:{minute}"   # "online now" each minute, kept a day (timeline)
_SAMPLE_SECONDS = 60
_KEEP_HOURS = 8 * 24 + 2   # a week of hourly peaks for the busiest-hours chart

_local_peaks: dict[int, tuple[int, float]] = {}   # hour -> (count, unix time), without Redis
_sampler_started = False
_sampler_lock = threading.Lock()


def _groups():
    """(label, model, extra filters) for each kind of login."""
    return (
        ("users", User, (User.role != "superadmin",)),
        ("employees", Employee, ()),
        ("branches", Branch, ()),
    )


def _counts(db: Session, now: datetime) -> dict:
    out: dict = {}
    for label, model, extra in _groups():
        row = db.query(*[func.coalesce(func.sum(case((model.last_activity >= now - span, 1), else_=0)), 0)
                         for _, span in _WINDOWS]).filter(*extra).one()
        out[label] = {name: int(v or 0) for (name, _), v in zip(_WINDOWS, row)}
    out["total"] = {name: sum(out[label][name] for label, _, _ in _groups()) for name, _ in _WINDOWS}
    return out


def online_now(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=ONLINE_MINUTES)
    total = 0
    for _, model, extra in _groups():
        total += db.query(func.count(model.id)).filter(model.last_activity >= cutoff, *extra).scalar() or 0
    return int(total)


def _by_company(db: Session, now: datetime, limit: int = 20) -> list[dict]:
    online_cut, day_cut = now - timedelta(minutes=ONLINE_MINUTES), now - timedelta(hours=24)
    merged: dict[str, dict] = {}
    for label, model, extra in _groups():
        rows = (db.query(model.company_id,
                         func.sum(case((model.last_activity >= online_cut, 1), else_=0)),
                         func.count(model.id))
                .filter(model.last_activity >= day_cut, *extra)
                .group_by(model.company_id).all())
        for company_id, online, day in rows:
            entry = merged.setdefault(company_id, {"company_id": company_id, "online": 0, "last_24h": 0,
                                                   "users": 0, "employees": 0, "branches": 0})
            entry["online"] += int(online or 0)
            entry["last_24h"] += int(day or 0)
            entry[label] += int(day or 0)
    top = sorted(merged.values(), key=lambda e: (-e["online"], -e["last_24h"]))[:limit]
    names = {c.id: c.name for c in db.query(Company.id, Company.name)
             .filter(Company.id.in_([e["company_id"] for e in top])).all()} if top else {}
    for e in top:
        e["company_name"] = names.get(e["company_id"], e["company_id"])
    return top


def record_sample(count: int, at: float | None = None) -> None:
    """Keep the highest "online now" per hour."""
    at = at or time.time()
    hour = int(at // 3600)
    r = cache._redis()
    if r is not None:
        try:
            key = _PEAK_KEY.format(hour=hour)
            current = r.get(key)
            if current is None or int(str(current).split("|")[0]) < count:
                r.set(key, f"{count}|{at:.0f}", ex=_KEEP_HOURS * 3600)
            return
        except Exception:
            pass
    prev = _local_peaks.get(hour)
    if prev is None or prev[0] < count:
        _local_peaks[hour] = (count, at)
    for old in [h for h in _local_peaks if h < hour - _KEEP_HOURS]:
        _local_peaks.pop(old, None)


_local_minutes: dict[int, int] = {}


def _record_minute(count: int, at: float) -> None:
    minute = int(at // 60)
    r = cache._redis()
    if r is not None:
        try:
            r.set(ONLINE_MINUTE_KEY.format(minute=minute), count, ex=26 * 3600)
            return
        except Exception:
            pass
    _local_minutes[minute] = count
    for old in [m for m in _local_minutes if m < minute - 26 * 60]:
        _local_minutes.pop(old, None)


def online_by_minute(minutes: list[int]) -> dict[int, int]:
    r = cache._redis()
    if r is not None:
        try:
            values = r.mget([ONLINE_MINUTE_KEY.format(minute=m) for m in minutes])
            return {m: int(v) for m, v in zip(minutes, values) if v is not None}
        except Exception:
            pass
    return {m: _local_minutes[m] for m in minutes if m in _local_minutes}


def peak_last_24h(now: float | None = None) -> dict | None:
    now = now or time.time()
    hours = [int(now // 3600) - i for i in range(24)]
    samples: list[tuple[int, float]] = []
    r = cache._redis()
    if r is not None:
        try:
            for value in r.mget([_PEAK_KEY.format(hour=h) for h in hours]):
                if value:
                    count, at = str(value).split("|")
                    samples.append((int(count), float(at)))
        except Exception:
            samples = []
    if not samples:
        samples = [_local_peaks[h] for h in hours if h in _local_peaks]
    if not samples:
        return None
    count, at = max(samples)
    return {"count": count, "at": datetime.fromtimestamp(at, UTC).isoformat()}


def hourly_peaks(hours: int = 168, now: float | None = None) -> list[dict]:
    """The highest "online now" of each of the last `hours` hours, oldest first (None = no sample)."""
    now = now or time.time()
    hour_ids = [int(now // 3600) - i for i in range(hours - 1, -1, -1)]
    values: dict[int, int] = {}
    r = cache._redis()
    if r is not None:
        try:
            for h, value in zip(hour_ids, r.mget([_PEAK_KEY.format(hour=h) for h in hour_ids])):
                if value:
                    values[h] = int(str(value).split("|")[0])
        except Exception:
            values = {}
    if not values:
        values = {h: _local_peaks[h][0] for h in hour_ids if h in _local_peaks}
    return [{"hour": datetime.fromtimestamp(h * 3600, UTC).isoformat(), "peak": values.get(h)} for h in hour_ids]


def summary(db: Session) -> dict:
    now = datetime.now(UTC)
    counts = _counts(db, now)
    record_sample(counts["total"]["online"])
    return {
        "online_minutes": ONLINE_MINUTES,
        "counts": counts,
        "peak_24h": peak_last_24h(),
        "by_company": _by_company(db, now),
        "generated_at": now.isoformat(),
    }


# ── once-a-minute sample, so the peak is known even when nobody has Super Admin open ──

def ensure_sampler() -> None:
    """Start this process's sampling thread (cheap to call on every activity touch)."""
    global _sampler_started
    if _sampler_started:
        return
    with _sampler_lock:
        if _sampler_started:
            return
        _sampler_started = True
    threading.Thread(target=_sample_loop, name="active-users-sample", daemon=True).start()


def _claim_minute(now: float) -> bool:
    """Only one process per minute samples (with Redis); without Redis each process does."""
    r = cache._redis()
    if r is None:
        return True
    try:
        return bool(r.set(f"tf:active:sample:{int(now // 60)}", "1", nx=True, ex=120))
    except Exception:
        return False


def _sample_loop() -> None:
    from app.database import SessionLocal
    while True:
        time.sleep(_SAMPLE_SECONDS)
        try:
            now = time.time()
            if not _claim_minute(now):
                continue
            db = SessionLocal()
            try:
                count = online_now(db)
                record_sample(count, now)
                _record_minute(count, now)
                from app import observability
                observability.check_alerts(db)
            finally:
                db.close()
        except Exception as exc:  # never let the panel take a worker down
            log.debug("active users sample failed: %s", exc)
