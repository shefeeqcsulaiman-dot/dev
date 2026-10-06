"""A burst of concurrent requests on a cold report cache should rebuild it once, not once per request."""
import threading
import time

import app.cache as cache
from app.routers import reports


class _FakeRedis:
    def __init__(self):
        self.store, self.lock = {}, threading.Lock()

    def get(self, key):
        with self.lock:
            return self.store.get(key)

    def setex(self, key, ttl, value):
        with self.lock:
            self.store[key] = value


def test_concurrent_cold_cache_builds_once(monkeypatch):
    monkeypatch.setattr(cache, "_redis", lambda: _FakeRedis.instance)
    _FakeRedis.instance = _FakeRedis()
    builds = []

    def build():
        builds.append(1)
        time.sleep(0.2)
        return {"total": 42}

    results = []
    threads = [threading.Thread(target=lambda: results.append(reports._cached_or_build("stampede-test", 60, build)))
               for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(builds) == 1
    assert results == [{"total": 42}] * 12


def test_without_cache_every_call_builds(monkeypatch):
    monkeypatch.setattr(cache, "_redis", lambda: None)
    builds = []
    for _ in range(3):
        reports._cached_or_build("no-cache-test", 60, lambda: builds.append(1) or {"ok": True})
    assert len(builds) == 3
