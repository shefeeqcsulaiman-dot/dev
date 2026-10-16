"""Super Admin overview panels (routers/superadmin.py):

- platform_health(): a live strip -- database reachable and how fast, connections in use,
  server errors / overloads in the last hour, requests in progress, people online, and
  which build every server copy runs (a deploy in progress shows two builds).
- insights(): slower-moving numbers, cached by the caller -- busiest hours (a week of
  hourly "online" peaks), companies needing attention, weekly growth, module adoption.

Everything here reads counts and timestamps the app already keeps; nothing is estimated.
"""
from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import active_users, request_metrics, server_errors
from app.models import AppDataRecord, AttendanceDetail, Branch, Company, Employee, PayrollRun, User

_INTERNAL_TRN = "SUPERADMIN-INTERNAL"

# Which collections show a module being used (records created in the window).
_MODULE_COLLECTIONS = {
    "sales": ("salesInvoices", "quotations", "customers"),
    "purchases": ("purchaseRecords", "bills", "purchaseDocuments", "vendors"),
    "expenses": ("expenses",),
    "inventory": ("products", "stockMovements", "warehouses"),
    "accounting": ("journalDrafts", "ledger", "payments", "receipts"),
    "rota": ("rotaAssignments",),
    "leave": ("leaveRequests",),
}


def _hm_status(level: str, reasons: list[str]) -> dict:
    return {"level": level, "reasons": reasons}


def platform_health(db: Session) -> dict:
    out: dict = {}
    reasons_red: list[str] = []
    reasons_amber: list[str] = []

    began = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        out["db_ok"] = True
    except Exception:
        out["db_ok"] = False
        reasons_red.append("Database not reachable")
    out["db_latency_ms"] = round((time.perf_counter() - began) * 1000, 1)
    if out["db_ok"] and out["db_latency_ms"] > 500:
        reasons_red.append(f"Database slow ({out['db_latency_ms']:.0f} ms)")
    elif out["db_ok"] and out["db_latency_ms"] > 150:
        reasons_amber.append(f"Database slower than usual ({out['db_latency_ms']:.0f} ms)")

    out["db_connections"] = None
    if db.get_bind().dialect.name == "postgresql":
        try:
            used = db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar()
            limit = int(db.execute(text("SHOW max_connections")).scalar())
            out["db_connections"] = {"used": int(used or 0), "max": limit}
            if limit and used / limit >= 0.9:
                reasons_red.append(f"Database connections nearly full ({used}/{limit})")
            elif limit and used / limit >= 0.75:
                reasons_amber.append(f"Database connections high ({used}/{limit})")
        except Exception:
            db.rollback()

    entries, _ = server_errors.recent(1000)
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    recent = []
    for e in entries:
        try:
            if datetime.fromisoformat(str(e.get("at"))) >= hour_ago:
                recent.append(e)
        except ValueError:
            continue
    overloads = sum(1 for e in recent if e.get("status") == 503)
    errors = len(recent) - overloads
    out["errors_1h"] = {"overloads": overloads, "errors": errors}
    if overloads:
        reasons_red.append(f"{overloads} overload (503) in the last hour")
    if errors:
        reasons_amber.append(f"{errors} server error(s) in the last hour")

    out["load"] = request_metrics.snapshot()
    out["online_now"] = active_users.online_now(db)
    out["deploy"] = request_metrics.builds()
    if len(out["deploy"]["builds"]) > 1:
        reasons_amber.append("Deploy in progress (two builds running)")

    level = "red" if reasons_red else "amber" if reasons_amber else "green"
    out["status"] = _hm_status(level, reasons_red + reasons_amber)
    out["checked_at"] = datetime.now(UTC).isoformat()
    return out


def _companies(db: Session) -> list[Company]:
    return db.query(Company).filter((Company.trn.is_(None)) | (Company.trn != _INTERNAL_TRN)).all()


def _last_activity_by_company(db: Session) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for model, extra in ((User, (User.role != "superadmin",)), (Employee, ()), (Branch, ())):
        for cid, last in (db.query(model.company_id, func.max(model.last_activity))
                          .filter(*extra).group_by(model.company_id).all()):
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if cid not in out or last > out[cid]:
                out[cid] = last
    return out


def _attention(db: Session, companies: list[Company], now: datetime) -> list[dict]:
    last_seen = _last_activity_by_company(db)
    today = now.date()
    items: list[dict] = []
    for c in companies:
        expiry = None
        try:
            expiry = date.fromisoformat(str(c.subscription_expires_at or "")[:10])
        except ValueError:
            pass
        if expiry is not None:
            days = (expiry - today).days
            if days < 0:
                items.append({"company_id": c.id, "company_name": c.name, "kind": "expired", "severity": "red",
                              "detail": f"Subscription expired {-days} day(s) ago"})
            elif days <= 14:
                items.append({"company_id": c.id, "company_name": c.name, "kind": "expiring",
                              "severity": "amber" if days > 3 else "red",
                              "detail": f"Subscription ends in {days} day(s) ({expiry.isoformat()})"})
        created = c.created_at.replace(tzinfo=UTC) if c.created_at and c.created_at.tzinfo is None else c.created_at
        if created and created < now - timedelta(days=7):
            seen = last_seen.get(c.id)
            if seen is None:
                items.append({"company_id": c.id, "company_name": c.name, "kind": "never_used", "severity": "amber",
                              "detail": f"No activity recorded since sign-up ({created.date().isoformat()})"})
            elif seen < now - timedelta(days=7):
                items.append({"company_id": c.id, "company_name": c.name, "kind": "inactive", "severity": "amber",
                              "detail": f"No activity for {(now - seen).days} days"})
    order = {"red": 0, "amber": 1}
    items.sort(key=lambda i: (order.get(i["severity"], 2), i["company_name"] or ""))
    return items


def _weeks(now: datetime, n: int) -> list[date]:
    """Mondays of the last n weeks, oldest first (this week last)."""
    monday = now.date() - timedelta(days=now.date().weekday())
    return [monday - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def _per_week(rows, weeks: list[date]) -> list[int]:
    counts = {w: 0 for w in weeks}
    for day, n in rows:
        if day is None:
            continue
        d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
        monday = d - timedelta(days=d.weekday())
        if monday in counts:
            counts[monday] += int(n or 0)
    return [counts[w] for w in weeks]


def _growth(db: Session, now: datetime, weeks_n: int = 8) -> dict:
    weeks = _weeks(now, weeks_n)
    since = datetime.combine(weeks[0], datetime.min.time()).replace(tzinfo=UTC)

    def daily(model, *extra):
        return (db.query(func.date(model.created_at), func.count(model.id))
                .filter(model.created_at >= since, *extra).group_by(func.date(model.created_at)).all())

    return {
        "weeks": [w.isoformat() for w in weeks],
        "companies": _per_week(daily(Company, (Company.trn.is_(None)) | (Company.trn != _INTERNAL_TRN)), weeks),
        "employees": _per_week(daily(Employee), weeks),
        "records": _per_week(daily(AppDataRecord), weeks),
    }


def _adoption(db: Session, companies: list[Company], now: datetime, limit: int = 25) -> dict:
    since = now - timedelta(days=30)
    collection_module = {col: mod for mod, cols in _MODULE_COLLECTIONS.items() for col in cols}
    usage: dict[str, dict[str, int]] = {}
    for cid, collection, n in (db.query(AppDataRecord.company_id, AppDataRecord.collection, func.count(AppDataRecord.id))
                               .filter(AppDataRecord.created_at >= since,
                                       AppDataRecord.collection.in_(list(collection_module)))
                               .group_by(AppDataRecord.company_id, AppDataRecord.collection).all()):
        mod = collection_module[collection]
        usage.setdefault(cid, {})[mod] = usage.setdefault(cid, {}).get(mod, 0) + int(n)
    for cid, n in (db.query(AttendanceDetail.company_id, func.count(AttendanceDetail.id))
                   .filter(AttendanceDetail.work_date >= since.date().isoformat(), AttendanceDetail.clock_in_1.isnot(None))
                   .group_by(AttendanceDetail.company_id).all()):
        usage.setdefault(cid, {})["attendance"] = int(n)
    for cid, n in (db.query(PayrollRun.company_id, func.count(PayrollRun.id))
                   .filter(PayrollRun.created_at >= since).group_by(PayrollRun.company_id).all()):
        usage.setdefault(cid, {})["payroll"] = int(n)

    modules = ["sales", "purchases", "expenses", "inventory", "accounting", "attendance", "rota", "leave", "payroll"]
    names = {c.id: c.name for c in companies}
    rows = [{"company_id": cid, "company_name": names[cid], "modules": {m: usage.get(cid, {}).get(m, 0) for m in modules},
             "total": sum(usage.get(cid, {}).values())}
            for cid in names]
    rows.sort(key=lambda r: (-r["total"], r["company_name"] or ""))
    using = {m: sum(1 for r in rows if r["modules"][m]) for m in modules}
    return {"modules": modules, "companies_using": using, "total_companies": len(rows), "rows": rows[:limit]}


def insights(db: Session) -> dict:
    now = datetime.now(UTC)
    companies = _companies(db)
    return {
        "busiest_hours": active_users.hourly_peaks(168),
        "attention": _attention(db, companies, now),
        "growth": _growth(db, now),
        "adoption": _adoption(db, companies, now),
        "generated_at": now.isoformat(),
    }
