"""Request/database monitoring (app/monitoring.py): Server-Timing header,
per-endpoint stats keyed by route template, slow-request logs, and the
superadmin endpoint-stats view."""
import logging

from app import monitoring
from app.config import get_settings
from app.models import Company, User
from app.security import hash_password


def _superadmin_headers(client, db):
    admin = db.query(User).filter(User.email == "monitoring-superadmin@etaxflow.com").first()
    if not admin:
        company = Company(name="Monitoring Test Admin", trn="SUPERADMIN-MONITORING-TEST")
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, email="monitoring-superadmin@etaxflow.com",
                     full_name="Monitoring Super Admin", role="superadmin", password_hash=hash_password("test12345"))
        db.add(admin)
        db.commit()
    r = client.post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_response_carries_server_timing_with_db_queries(client, auth_headers):
    r = client.get("/api/v1/app-data", headers=auth_headers)
    assert r.status_code == 200
    timing = r.headers["Server-Timing"]
    assert timing.startswith("app;dur=")
    queries = int(timing.split('desc="')[1].split(" ")[0])
    assert queries > 0


def test_static_and_page_requests_are_not_timed(client):
    r = client.get("/health")
    assert "Server-Timing" not in r.headers


def test_stats_are_keyed_by_route_template_not_raw_path(client, auth_headers):
    for collection in ("salesInvoices", "bills"):
        client.get(f"/api/v1/app-data/registers/{collection}", headers=auth_headers)
    stats = monitoring.endpoint_stats(hours=1, limit=500)["endpoints"]
    keys = {row["endpoint"] for row in stats}
    assert "GET /api/v1/app-data/registers/{collection}" in keys
    assert not any(k.endswith("/registers/salesInvoices") for k in keys)
    row = next(r for r in stats if r["endpoint"] == "GET /api/v1/app-data/registers/{collection}")
    assert row["count"] >= 2
    assert row["p50_ms"] is not None and row["avg_queries"] > 0


def test_slow_request_is_logged(client, auth_headers, caplog, monkeypatch):
    monkeypatch.setattr(get_settings(), "slow_request_ms", 0.0001)
    with caplog.at_level(logging.WARNING, logger="taxflow"):
        client.get("/api/v1/app-data", headers=auth_headers)
    assert any("slow request GET /api/v1/app-data" in m for m in caplog.messages)


def test_percentile_uses_bucket_upper_edges():
    buckets = [0] * (len(monitoring.BUCKETS_MS) + 1)
    buckets[0] = 90   # <= 25ms
    buckets[4] = 10   # <= 500ms
    assert monitoring._percentile(buckets, 100, 0.50) == 25
    assert monitoring._percentile(buckets, 100, 0.95) == 500
    buckets[-1] = 1000  # mostly slower than the last edge
    assert monitoring._percentile(buckets, 1100, 0.95) is None


def test_endpoint_stats_is_superadmin_only(client, db, auth_headers):
    assert client.get("/api/v1/superadmin/endpoint-stats", headers=auth_headers).status_code in (401, 403)
    r = client.get("/api/v1/superadmin/endpoint-stats?hours=1", headers=_superadmin_headers(client, db))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hours"] == 1 and isinstance(body["endpoints"], list)


def test_sentry_stays_off_without_dsn():
    assert monitoring.init_sentry(None, "test", 0.0) is False


class _FakeRedis:
    """Just the hash commands monitoring.flush()/_from_redis() use."""

    def __init__(self):
        self.h: dict[str, dict[str, str]] = {}
        self._ops: list = []

    def pipeline(self, transaction=False):
        self._ops = []
        return self

    def hincrby(self, key, field, n):
        self._ops.append(lambda: self._incr(key, field, int(n)))

    def hincrbyfloat(self, key, field, n):
        self._ops.append(lambda: self._incr(key, field, float(n)))

    def eval(self, script, numkeys, key, field, value):
        def op():
            cur = float(self.h.get(key, {}).get(field, 0))
            if float(value) > cur:
                self.h.setdefault(key, {})[field] = str(value)
        self._ops.append(op)

    def expire(self, key, ttl):
        self._ops.append(lambda: None)

    def hgetall(self, key):
        self._ops.append(lambda: dict(self.h.get(key, {})))

    def execute(self):
        out = [op() for op in self._ops]
        self._ops = []
        return out

    def _incr(self, key, field, n):
        d = self.h.setdefault(key, {})
        d[field] = str(float(d.get(field, 0)) + n)


def test_flush_merges_every_process_into_one_cluster_view(monkeypatch):
    from app import cache

    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    monkeypatch.setattr(monitoring, "_unflushed", {})

    # Two "processes" worth of samples for the same endpoint.
    monitoring.record("GET /api/v1/x", 20, 200, db_ms=5, queries=2)
    monitoring.flush()
    monitoring.record("GET /api/v1/x", 700, 500, db_ms=300, queries=9)
    monitoring.flush()

    stats = monitoring.endpoint_stats(hours=1)
    assert stats["cluster_wide"] is True
    row = next(r for r in stats["endpoints"] if r["endpoint"] == "GET /api/v1/x")
    assert row["count"] == 2 and row["errors"] == 1
    assert row["max_ms"] == 700 and row["p95_ms"] == 1000
    assert row["avg_queries"] == 5.5
