"""Super Admin: what happened when, and telling the Super Admin when something goes wrong.

timeline(): the last 24 h in 5-minute steps -- requests per minute (request_metrics keeps a
counter per minute for a day), people online (active_users samples it every minute), server
errors and overloads (server_errors), browser errors (client_errors table), and the moment
each build went live (request_metrics records deploys). Lining these up answers "did the
errors start with a deploy, or with the 9:00 rush?".

check_alerts(): run once a minute by one server copy (active_users' sampler). Raises an alert
when the database is unreachable or nearly out of connections, the server is overloaded
(503s), server errors pile up, or a deploy seems stuck (two builds for 15+ minutes). Each
kind of alert is sent at most once per 30 minutes. Alerts always show in Super Admin; they're
also emailed (SMTP_* + ALERT_EMAIL_TO) and/or posted to a chat webhook (ALERT_WEBHOOK_URL)
when those are configured.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.orm import Session

import app.cache as cache
from app import active_users, request_metrics, server_errors
from app.config import get_settings
from app.models import ClientError

log = logging.getLogger("taxflow")

_ALERTS_KEY = "tf:alerts"
_ALERT_COOLDOWN_SECONDS = 30 * 60
_MAX_ALERTS = 200
_local_alerts: list[dict] = []
_local_cooldown: dict[str, float] = {}


# ── timeline ────────────────────────────────────────────────────────────────

def _parse_at(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def timeline(db: Session, hours: int = 24, step_minutes: int = 5) -> dict:
    now = time.time()
    first_minute = int(now // 60) - hours * 60 + 1
    minutes = list(range(first_minute, int(now // 60) + 1))
    steps = list(range(first_minute - first_minute % step_minutes, int(now // 60) + 1, step_minutes))

    def step_of(minute: int) -> int:
        return minute - minute % step_minutes

    requests: dict[int, list[int]] = {}
    r = cache._redis()
    if r is not None:
        try:
            values = r.mget([request_metrics.RATE_KEY.format(minute=m) for m in minutes])
            for m, v in zip(minutes, values):
                if v is not None:
                    requests.setdefault(step_of(m), []).append(int(v))
        except Exception:
            requests = {}
    online: dict[int, int] = {}
    for m, v in active_users.online_by_minute(minutes).items():
        online[step_of(m)] = max(online.get(step_of(m), 0), v)

    errors: dict[int, int] = {}
    overloads: dict[int, int] = {}
    for e in server_errors.recent(1000)[0]:
        at = _parse_at(e.get("at"))
        if at is None or at < first_minute * 60:
            continue
        bucket = overloads if e.get("status") == 503 else errors
        s = step_of(int(at // 60))
        bucket[s] = bucket.get(s, 0) + 1

    browser: dict[int, int] = {}
    since = datetime.fromtimestamp(first_minute * 60, UTC)
    for (at,) in db.query(ClientError.occurred_at).filter(ClientError.occurred_at >= since).all():
        if at is None:
            continue
        ts = (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()
        s = step_of(int(ts // 60))
        browser[s] = browser.get(s, 0) + 1

    deploys = []
    if r is not None:
        try:
            for raw in r.lrange(request_metrics.DEPLOYS_KEY, 0, 49):
                d = json.loads(raw)
                if d.get("at", 0) >= first_minute * 60:
                    deploys.append({"build": d.get("build"), "at": datetime.fromtimestamp(d["at"], UTC).isoformat()})
        except Exception:
            deploys = []

    points = []
    for s in steps:
        reqs = requests.get(s)
        points.append({
            "at": datetime.fromtimestamp(s * 60, UTC).isoformat(),
            # average requests per minute over the step (None = not recorded: no Redis, or older)
            "requests_per_min": round(sum(reqs) / len(reqs), 1) if reqs else None,
            "online": online.get(s),
            "errors": errors.get(s, 0),
            "overloads": overloads.get(s, 0),
            "browser_errors": browser.get(s, 0),
        })
    return {"step_minutes": step_minutes, "points": points, "deploys": sorted(deploys, key=lambda d: d["at"]),
            "cluster_wide": r is not None}


# ── alerts ──────────────────────────────────────────────────────────────────

def _conditions(db: Session) -> list[tuple[str, str, str]]:
    """(kind, level, message) for everything wrong right now."""
    found: list[tuple[str, str, str]] = []
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db.rollback()
        return [("db_down", "red", "The database is not reachable.")]
    if db.get_bind().dialect.name == "postgresql":
        try:
            used = int(db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar() or 0)
            limit = int(db.execute(text("SHOW max_connections")).scalar() or 0)
            if limit and used / limit >= 0.9:
                found.append(("db_connections", "red", f"Database connections nearly full: {used} of {limit}."))
            elif limit and used / limit >= 0.8:
                found.append(("db_connections", "amber", f"Database connections high: {used} of {limit}."))
        except Exception:
            db.rollback()

    now = datetime.now(UTC)
    overloads_5m = errors_10m = 0
    for e in server_errors.recent(1000)[0]:
        at = _parse_at(e.get("at"))
        if at is None:
            continue
        age = now.timestamp() - at
        if e.get("status") == 503 and age <= 300:
            overloads_5m += 1
        elif e.get("status") != 503 and age <= 600:
            errors_10m += 1
    if overloads_5m >= 3:
        found.append(("overload", "red", f"Server overloaded: {overloads_5m} requests answered 503 \"busy\" in the last 5 minutes."))
    if errors_10m >= 5:
        found.append(("server_errors", "amber", f"{errors_10m} server errors (500) in the last 10 minutes."))

    builds = request_metrics.builds()
    if len(builds.get("builds", {})) > 1:
        r = cache._redis()
        if r is not None:
            try:
                started = r.set("tf:alert:mixed_builds_since", f"{time.time():.0f}", nx=True, ex=3600)
                since = float(r.get("tf:alert:mixed_builds_since") or time.time())
                if not started and time.time() - since >= 15 * 60:
                    names = ", ".join(f"{b} ×{n}" for b, n in builds["builds"].items())
                    found.append(("deploy_stuck", "amber", f"Two builds have been running for 15+ minutes ({names}): a deploy may be stuck."))
            except Exception:
                pass
    else:
        r = cache._redis()
        if r is not None:
            try:
                r.delete("tf:alert:mixed_builds_since")
            except Exception:
                pass
    return found


def _cool(kind: str) -> bool:
    """True if this kind of alert may be sent now (not sent in the last 30 minutes)."""
    r = cache._redis()
    if r is not None:
        try:
            return bool(r.set(f"tf:alert:cool:{kind}", "1", nx=True, ex=_ALERT_COOLDOWN_SECONDS))
        except Exception:
            pass
    last = _local_cooldown.get(kind, 0)
    if time.time() - last < _ALERT_COOLDOWN_SECONDS:
        return False
    _local_cooldown[kind] = time.time()
    return True


def _store(alert: dict) -> None:
    r = cache._redis()
    if r is not None:
        try:
            r.lpush(_ALERTS_KEY, json.dumps(alert))
            r.ltrim(_ALERTS_KEY, 0, _MAX_ALERTS - 1)
            return
        except Exception:
            pass
    _local_alerts.insert(0, alert)
    del _local_alerts[_MAX_ALERTS:]


def recent_alerts(limit: int = 50) -> list[dict]:
    r = cache._redis()
    if r is not None:
        try:
            return [json.loads(x) for x in r.lrange(_ALERTS_KEY, 0, limit - 1)]
        except Exception:
            pass
    return _local_alerts[:limit]


def channels() -> dict:
    s = get_settings()
    return {"email": bool(s.smtp_host and s.alert_email_to), "webhook": bool(s.alert_webhook_url)}


def _send(alert: dict) -> None:
    """Email and/or webhook, in the background: never slows or breaks the checker."""
    s = get_settings()
    subject = f"[eTaxFlow {alert['level'].upper()}] {alert['message']}"
    body = (f"{alert['message']}\n\nTime: {alert['at']}\nBuild: {alert.get('build') or '-'}\n\n"
            "Open Super Admin > System Health for details: https://app.etaxflow.com/superadmin")

    def work():
        if s.smtp_host and s.alert_email_to:
            try:
                import smtplib
                from email.message import EmailMessage
                msg = EmailMessage()
                msg["Subject"] = subject[:200]
                msg["From"] = s.smtp_from or s.smtp_user
                msg["To"] = s.alert_email_to
                msg.set_content(body)
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                    smtp.starttls()
                    if s.smtp_user:
                        smtp.login(s.smtp_user, s.smtp_password)
                    smtp.send_message(msg)
            except Exception as exc:
                log.warning("alert email failed: %s", exc)
        if s.alert_webhook_url:
            try:
                import urllib.request
                req = urllib.request.Request(s.alert_webhook_url, data=json.dumps({"text": f"{subject}\n{body}"}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).close()
            except Exception as exc:
                log.warning("alert webhook failed: %s", exc)

    threading.Thread(target=work, name="alert-send", daemon=True).start()


def check_alerts(db: Session) -> list[dict]:
    """Raise (store + send) an alert for each current problem not already alerted in the last
    30 minutes. Returns the alerts raised now."""
    raised = []
    build = request_metrics._build()
    for kind, level, message in _conditions(db):
        if not _cool(kind):
            continue
        alert = {"at": datetime.now(UTC).isoformat(), "kind": kind, "level": level, "message": message, "build": build}
        _store(alert)
        if any(channels().values()):
            _send(alert)
        raised.append(alert)
    return raised
