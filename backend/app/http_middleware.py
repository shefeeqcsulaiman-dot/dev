"""The app's own per-request work as one plain ASGI middleware.

It replaced five @app.middleware("http") functions (security headers, cache
invalidation, request timing, live-load counting, static cache headers). Each of
those is a Starlette BaseHTTPMiddleware, which runs the rest of the app in a new
task with its own memory stream; five of them cost ~1.7 ms per request before any
work was done (load-tests/postgres, 2026-10-10), a large share of a phone-app
request. Behaviour is the same: the same headers, timings and invalidation.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from starlette.datastructures import MutableHeaders

from app import monitoring
from app.config import get_settings
from app.request_metrics import request_finished, request_started

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_FOREVER = "public, max-age=31536000, immutable"


def _header(scope: dict, name: bytes) -> str:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return ""


def _static_cache_control(scope: dict) -> str | None:
    """Cache-Control for a successful GET that didn't set its own."""
    path = scope.get("path") or ""
    last_segment = path.rsplit("/", 1)[-1]
    ext = last_segment.rsplit(".", 1)[-1].lower() if "." in last_segment else ""
    if ext in ("js", "css") and "v=" in (scope.get("query_string") or b"").decode("latin-1"):
        # Cache-busted via ?v=... query string, so it's safe to cache "forever" —
        # any future edit ships under a new query string and misses this cache entirely.
        return _FOREVER
    if ext in ("woff2", "woff") and not path.startswith("/api/"):
        # Self-hosted fonts: a given file name never changes content, cache "forever".
        return _FOREVER
    if ext in ("png", "jpg", "jpeg", "gif", "svg", "ico", "webp") and not path.startswith("/api/"):
        # Static site images (logo etc.). Not versioned, so a day (not a year); never
        # applied to /api/ paths, which can return per-user images.
        return "public, max-age=86400"
    if ext == "html" or path in ("", "/"):
        # Never cache HTML itself — it's the only thing that references the current
        # ?v=... asset URLs above, so it must always be revalidated on load.
        return "no-cache"
    return None


class AppMiddleware:
    def __init__(self, app, *, csp: str, csp_mode: str, on_write: Callable[[str], Awaitable[None]]):
        self.app = app
        self.csp = csp
        self.csp_mode = csp_mode
        self.on_write = on_write

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = scope.get("method", "GET")
        # Feeds the superadmin "Live Load" panel — scoped to /api/v1/ only so static
        # asset traffic doesn't dilute how many actual app requests are in flight.
        is_api = "/api/v1/" in path
        # Per-endpoint timings, slow-request/slow-query logs, Server-Timing header
        # (app/monitoring.py).
        monitored = monitoring._is_monitored(path)
        acc = [0.0, 0]
        token = monitoring._request_db.set(acc) if monitored else None
        start = time.perf_counter()
        status = 500

        async def send_with_headers(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                if method == "GET" and status == 200 and "cache-control" not in headers:
                    cache_control = _static_cache_control(scope)
                    if cache_control:
                        headers["Cache-Control"] = cache_control
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(self), payment=(), usb=()"
                if self.csp_mode == "enforce":
                    headers["Content-Security-Policy"] = self.csp
                elif self.csp_mode == "report":
                    headers["Content-Security-Policy-Report-Only"] = self.csp
                # DO App Platform terminates TLS upstream and forwards plain HTTP to
                # the app, so the scheme is unreliable -- X-Forwarded-Proto is what
                # actually reflects what the browser used. Only send HSTS when the
                # browser reached us over HTTPS, so a plain-http local/scratch server
                # (no proxy in front) never gets it either.
                if (_header(scope, b"x-forwarded-proto") or scope.get("scheme")) == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                if monitored:
                    total = (time.perf_counter() - start) * 1000
                    headers["Server-Timing"] = f'app;dur={total:.1f}, db;dur={acc[0]:.1f};desc="{acc[1]} queries"'
            await send(message)

        if is_api:
            request_started()
        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            if is_api:
                request_finished()
            if monitored:
                total = (time.perf_counter() - start) * 1000
                monitoring._request_db.reset(token)
                monitoring.record(f"{method} {monitoring.route_template(scope)}", total, status, acc[0], acc[1])
                slow_request_ms = get_settings().slow_request_ms
                if slow_request_ms and total >= slow_request_ms:
                    monitoring.log.warning(
                        "slow request %s %s -> %s in %.0fms (db %.0fms, %d queries)",
                        method, path, status, total, acc[0], acc[1],
                    )
        # Report caches are dropped after every write. A rejected write (4xx) changed
        # nothing, so its caches stay valid; a 5xx may have failed after committing.
        if method in _WRITE_METHODS and is_api and not 400 <= status < 500:
            asyncio.create_task(self.on_write(_header(scope, b"authorization")))
