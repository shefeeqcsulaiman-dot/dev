"""Redis down at start-up: /health says why (error type only, no host names), and the
app retries later instead of staying without Redis until the next restart."""
from app import cache


class _FakeRedis:
    def ping(self):
        return True


def test_failed_connect_is_reported_and_retried(client, monkeypatch):
    import redis
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", "rediss://default:pw@cache.example.invalid:25061")
    calls = []

    def fail(url, **kw):
        calls.append(url)
        raise redis.exceptions.ConnectionError("Error connecting to cache.example.invalid:25061")

    monkeypatch.setattr(redis, "from_url", fail)
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_tried", False)
    monkeypatch.setattr(cache, "_retry_at", 0.0)
    monkeypatch.setattr(cache, "last_error", None)

    body = client.get("/health").json()
    assert body["redis"] == "error: ConnectionError"
    assert "example" not in str(body) and "pw" not in str(body)
    assert cache._redis() is None and len(calls) == 1   # within the retry window: no new attempt

    monkeypatch.setattr(redis, "from_url", lambda url, **kw: _FakeRedis())
    monkeypatch.setattr(cache, "_retry_at", 0.0)        # retry window over
    assert isinstance(cache._redis(), _FakeRedis)
    assert client.get("/health").json()["redis"] == "ok"
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_tried", False)
