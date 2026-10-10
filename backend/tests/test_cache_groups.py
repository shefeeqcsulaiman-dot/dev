"""Write-time cache invalidation: a company's report caches are found through a group set,
never by scanning the keyspace, and the General Ledger listing has its own endpoint."""
import threading

import app.cache as cache


class _Pipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    def __getattr__(self, name):
        def queue(*args):
            self.ops.append((name, args))
            return self
        return queue

    def execute(self):
        return [getattr(self.redis, name)(*args) for name, args in self.ops]


class _FakeRedis:
    def __init__(self):
        self.store, self.sets, self.lock, self.scans = {}, {}, threading.Lock(), 0

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def expire(self, key, ttl):
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.sets.pop(key, None)

    def scan_iter(self, *a, **k):
        self.scans += 1
        return iter(self.store)


def test_invalidate_company_drops_only_that_companys_report_caches(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    for cid in ("c1", "c2"):
        cache.set_in_group(f"trial_balance:{cid}:all", {"rows": [1]}, 120, cache.report_group(cid))
        cache.set_in_group(f"trial_balance:{cid}:branch-a", {"rows": [2]}, 120, cache.report_group(cid))
        cache.set_in_group(f"vat_return:{cid}:2026-10", {"net": "1"}, 300, cache.report_group(cid))
        cache.set(f"bootstrap:{cid}", {"x": 1})
        cache.set(f"summary:{cid}:all", {"y": 1})

    cache.invalidate_company("c1")

    assert cache.get("trial_balance:c1:all") is None
    assert cache.get("trial_balance:c1:branch-a") is None
    assert cache.get("vat_return:c1:2026-10") is None
    assert cache.get("bootstrap:c1") is None
    assert cache.get("summary:c1:all") == {"y": 1}          # TTL-only by design
    assert cache.get("trial_balance:c2:all") == {"rows": [1]}
    assert cache.get("vat_return:c2:2026-10") == {"net": "1"}
    assert cache.recently_written("c1")
    assert fake.scans == 0


def test_trial_balance_cache_is_cleared_by_a_write(client, auth_headers, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    company_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    assert client.get("/api/v1/reports/trial-balance", headers=auth_headers).status_code == 200
    assert cache.get(f"trial_balance:{company_id}:all") is not None
    cache.invalidate_company(company_id)
    assert cache.get(f"trial_balance:{company_id}:all") is None


def test_general_ledger_has_its_own_endpoint(client, auth_headers):
    summary = client.get("/api/v1/reports/summary", headers=auth_headers)
    assert summary.status_code == 200
    assert "general_ledger" not in summary.json()
    gl = client.get("/api/v1/reports/general-ledger", headers=auth_headers)
    assert gl.status_code == 200
    rows = gl.json()["rows"]
    assert isinstance(rows, list)
    for row in rows:
        assert {"account_code", "date", "debit", "credit", "balance", "row_type"} <= set(row)


def test_general_ledger_needs_a_signed_in_user(client):
    assert client.get("/api/v1/reports/general-ledger").status_code == 401
