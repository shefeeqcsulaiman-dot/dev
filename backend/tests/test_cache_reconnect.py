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


def test_url_problem_names_the_mistake_without_the_value():
    h = "valkeyetax-x.k.db.ondigitalocean.com"
    assert cache.url_problem(f"rediss://default:AVNS_x@{h}:25061") is None
    assert "rediss://" in cache.url_problem(f"redis://default:AVNS_x@{h}:25061")
    assert "Public" in cache.url_problem(f"rediss://default:AVNS_x@private-{h}:25061")
    assert "spaces" in cache.url_problem(f" rediss://default:AVNS_x@{h}:25061")
    assert "spaces" in cache.url_problem(f'"rediss://default:AVNS_x@{h}:25061"')
    assert "placeholder" in cache.url_problem(f"rediss://default:PASSWORD@{h}:25061")
    assert "25061" in cache.url_problem(f"rediss://default:AVNS_x@{h}:25060")
    assert "no password" in cache.url_problem(f"rediss://{h}:25061")
    for url in (f"redis://default:AVNS_x@{h}:25061", f"rediss://default:AVNS_x@private-{h}:25061"):
        assert "AVNS_x" not in cache.url_problem(url) and "valkeyetax" not in cache.url_problem(url)
    assert cache.url_problem("memory://") is None and cache.url_problem("redis://localhost:6379/0") is None


def test_health_names_database_failure_without_host(client, monkeypatch):
    import app.main as main

    class _Broken:
        def __init__(self, *a, **k):
            raise Exception('connection to server at "db-secret-host.example" failed: '
                            'FATAL:  password authentication failed for user "doadmin"')

    monkeypatch.setattr(main, "SessionLocal", _Broken)
    body = client.get("/health").json()
    assert body["db"] == "error: wrong password in DATABASE_URL"
    assert body["status"] == "degraded"
    assert "secret-host" not in str(body) and "doadmin" not in str(body)


def test_health_says_whether_sentry_is_on(client, monkeypatch):
    from app import monitoring
    assert client.get("/health").json()["sentry"] == "off"
    monkeypatch.setattr(monitoring, "_sentry_on", True)
    assert client.get("/health").json()["sentry"] == "on"
