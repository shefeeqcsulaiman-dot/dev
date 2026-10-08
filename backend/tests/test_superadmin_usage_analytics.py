"""Usage analytics: totals, daily trend, module breakdown and top companies from two grouped
queries, cached for 10 minutes (it counts every record of the period)."""
import json
from uuid import uuid4

from app.models import AppDataRecord
from tests.test_module_permissions import _make_restricted_company


def test_usage_analytics_counts_and_caches(client, db):
    from app.routers import superadmin

    cid, _, sa = _make_restricted_company(client, db, f"ua-{uuid4().hex[:4]}", ["sales"])
    url = f"/api/v1/superadmin/usage-analytics?days=14&company_id={cid}"
    superadmin._local_cache.clear()
    before = client.get(url, headers=sa).json()  # a new company already has its default records
    for i in range(3):
        db.add(AppDataRecord(company_id=cid, collection="salesInvoices", record_key=f"UA-{i}", payload=json.dumps({"n": i})))
    db.add(AppDataRecord(company_id=cid, collection="expenses", record_key="UA-E", payload="{}"))
    db.commit()

    superadmin._local_cache.clear()
    r = client.get(url, headers=sa)
    assert r.status_code == 200, r.text
    body = r.json()
    n = before["total_records"] + 4
    assert body["total_records"] == n
    assert sum(d["count"] for d in body["daily_trend"]) == n and len(body["daily_trend"]) == 14
    assert sum(m["count"] for m in body["module_breakdown"]) == n
    assert body["top_companies"][0]["company_id"] == cid and body["top_companies"][0]["count"] == n

    db.add(AppDataRecord(company_id=cid, collection="expenses", record_key="UA-E2", payload="{}"))
    db.commit()
    assert client.get(url, headers=sa).json()["total_records"] == n  # cached
    superadmin._local_cache.clear()
    assert client.get(url, headers=sa).json()["total_records"] == n + 1


def test_local_cache_is_bounded_and_expires(monkeypatch):
    from app import cache

    local = cache.LocalTTLCache(max_entries=5)
    for i in range(8):
        cache.remember(f"k{i}", 60, lambda i=i: i, local)
    assert len(local) == 5 and "k0" not in local and "k7" in local
    assert cache.remember("k7", 60, lambda: "recomputed", local) == 7  # hit
    local.set("short", 1, ttl=0)
    assert local.get("short") is None  # expired


def test_report_build_locks_are_a_fixed_set():
    from app.routers import reports

    locks = {id(reports._build_lock(f"summary:company-{i}")) for i in range(5000)}
    assert len(locks) <= reports._BUILD_LOCK_STRIPES
    assert reports._build_lock("same-key") is reports._build_lock("same-key")
