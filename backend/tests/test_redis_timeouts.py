"""A Redis connection that stops answering (dropped by the server or the network after an
idle spell) must fail fast instead of hanging the request: on e4cs.com (2026-10-10) about
1 in 7 /health calls hung 15-20 s because the client had no read timeout."""
import socket
import threading
import time

import redis

from app import cache


def _silent_server():
    """Accepts connections and never replies."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    held = []

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    return srv, held


def test_client_options_have_read_timeout():
    assert cache.CONNECTION_OPTIONS["socket_timeout"] <= 5
    assert cache.CONNECTION_OPTIONS["health_check_interval"] > 0


def test_unanswered_command_fails_fast_not_forever():
    srv, held = _silent_server()
    try:
        port = srv.getsockname()[1]
        client = redis.from_url(f"redis://127.0.0.1:{port}/0", decode_responses=True, **cache.CONNECTION_OPTIONS)
        start = time.monotonic()
        try:
            client.ping()
            raise AssertionError("ping should not succeed against a silent server")
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError):
            pass
        # socket_timeout 2 s, one retry on timeout: well under the 15-20 s hangs seen live.
        assert time.monotonic() - start < 8
    finally:
        srv.close()
        for c in held:
            c.close()


def test_limiter_uses_the_same_timeouts(monkeypatch):
    from app import limiter
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", "rediss://default:x@cache.example.com:25061")
    opts = limiter._storage_options()
    assert opts["socket_timeout"] == cache.CONNECTION_OPTIONS["socket_timeout"]
    monkeypatch.setattr(get_settings(), "redis_url", "memory://")
    assert limiter._storage_options() == {}


def test_rate_limiter_fails_open_when_redis_misbehaves():
    # A Redis timeout inside the limiter must not become a 500 for the user.
    from app.limiter import limiter
    assert limiter._swallow_errors is True
    assert limiter._in_memory_fallback_enabled is True
