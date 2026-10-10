"""Live-load counting (app/request_metrics.py): requests only touch memory; a flush adds
them to Redis, and the snapshot adds up every process that reported recently."""
import time

import app.cache as cache
from app import request_metrics as rm


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
        self.store, self.hashes, self.calls = {}, {}, 0

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    def incrby(self, key, n):
        self.calls += 1
        self.store[key] = int(self.store.get(key, 0)) + n

    def expire(self, key, ttl):
        return True

    def get(self, key):
        return self.store.get(key)

    def hset(self, key, field, value):
        self.calls += 1
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        for f in fields:
            self.hashes.get(key, {}).pop(f, None)


def test_requests_touch_only_memory_until_flushed(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    monkeypatch.setattr(rm, "_flusher_started", True)   # no background thread in the test
    monkeypatch.setattr(rm, "_unflushed", {})
    for _ in range(5):
        rm.request_started()
    rm.request_finished()
    assert fake.calls == 0

    # Another worker reported 3 in flight a moment ago; a third one went quiet long ago.
    now = time.time()
    fake.hashes[rm._PROCS_KEY] = {"other:1": f"3|{now:.0f}", "gone:2": f"9|{now - 600:.0f}"}
    snap = rm.snapshot()
    assert snap["cluster_wide"] is True
    assert snap["in_flight"] == 3 + rm._local_in_flight
    assert snap["requests_this_minute"] >= 5            # this process's unflushed starts count too
    assert "gone:2" not in fake.hashes[rm._PROCS_KEY]   # stale process dropped

    rm.flush()
    bucket = int(time.time() // 60)
    assert fake.store[f"tf:load:rate:{bucket}"] >= 5
    assert rm._PROC_ID in fake.hashes[rm._PROCS_KEY]
    assert rm._unflushed == {}
    for _ in range(4):
        rm.request_finished()


def test_no_base_http_middleware():
    """Each @app.middleware("http") wraps the app in a BaseHTTPMiddleware (its own task and
    stream per request, ~0.35 ms each); per-request work belongs in app/http_middleware.py."""
    from app.main import app

    assert [m.cls.__name__ for m in app.user_middleware if m.cls.__name__ == "BaseHTTPMiddleware"] == []


def test_without_redis_the_snapshot_is_this_process_only(monkeypatch):
    monkeypatch.setattr(cache, "_redis", lambda: None)
    rm.request_started()
    try:
        snap = rm.snapshot()
        assert snap["cluster_wide"] is False
        assert snap["in_flight"] >= 1
    finally:
        rm.request_finished()
