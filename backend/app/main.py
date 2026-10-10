import hashlib
import json
import logging
import os
import re
import pathlib
from typing import Any

# Load .env into os.environ so os.environ.get() works for AI keys
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=pathlib.Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass

from fastapi import FastAPI, Request

_log = logging.getLogger("taxflow")
_env_static = os.environ.get("STATIC_DIR", "")
_default_static = pathlib.Path(__file__).parent.parent / "frontend" / "public"
_repo_static = pathlib.Path(__file__).parent.parent.parent / "frontend" / "public"
static_dir: pathlib.Path = (
    pathlib.Path(_env_static)
    if _env_static
    else (_default_static if _default_static.exists() else _repo_static)
)
from fastapi.middleware.cors import CORSMiddleware
from brotli_asgi import BrotliMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.orm import Session

from app.company_defaults import seed_accounts, seed_tax_codes, seed_voucher_types
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Company, User
from app.routers import accounting, ai, ai_voice, app_data, attendance, audit, auth, branches, companies, corporate_accounting, documents, ess, ess_voice, events, exception_center, hr_access, hr_ai, inventory, invoice_share, invoices, jobs, leave, module_records, payroll, registers, reports, source_transactions, stock_feed, superadmin, tax
from app.security import hash_password


settings = get_settings()

# Cache for _resolve_minified_js(), keyed per source filename by
# (src mtime, min mtime) so we only re-read/re-hash each pair of files when
# either one actually changes on disk.
_minified_js_cache: dict[str, dict[str, Any]] = {}


def _resolve_minified_js(name: str) -> tuple[bytes, bool]:
    """Returns (content, is_minified) for static/taxflow/src/<name>.js.
    Serves <name>.min.js only when its embedded //SOURCE_SHA256:<hash>
    header matches the current <name>.js — see frontend/scripts/
    build-min.mjs for how these files are generated. Shared by app.js and
    ess.js (see _resolve_app_js()/_resolve_ess_js() below); correctness
    always wins over the size/speed win, never the other way around."""
    src_path = static_dir / "taxflow" / "src" / f"{name}.js"
    min_path = static_dir / "taxflow" / "src" / f"{name}.min.js"
    try:
        src_mtime = src_path.stat().st_mtime
        min_mtime = min_path.stat().st_mtime if min_path.exists() else None
    except OSError:
        return b"", False

    cache_key = (src_mtime, min_mtime)
    cache_entry = _minified_js_cache.get(name)
    if cache_entry is not None and cache_entry.get("key") == cache_key:
        return cache_entry["content"], cache_entry["is_min"]

    src_bytes = src_path.read_bytes()
    content, is_min = src_bytes, False
    if min_mtime is not None:
        try:
            min_bytes = min_path.read_bytes()
            first_line_end = min_bytes.index(b"\n")
            header = min_bytes[:first_line_end].decode("ascii", errors="ignore")
            if header.startswith("//SOURCE_SHA256:"):
                expected_hash = header.split(":", 1)[1].strip()
                actual_hash = hashlib.sha256(src_bytes).hexdigest()
                if expected_hash == actual_hash:
                    content, is_min = min_bytes, True
        except (OSError, ValueError):
            pass

    _minified_js_cache[name] = {"key": cache_key, "content": content, "is_min": is_min}
    return content, is_min


def _resolve_app_js() -> tuple[bytes, bool]:
    return _resolve_minified_js("app")


def _resolve_ess_js() -> tuple[bytes, bool]:
    return _resolve_minified_js("ess")


async def _invalidate_cache_bg(auth_header: str) -> None:
    import asyncio
    try:
        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            return
        from app.security import user_id_from_token
        user_id = await asyncio.get_event_loop().run_in_executor(None, user_id_from_token, token)
        if not user_id:
            return
        def _sync():
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    import app.cache as _cache
                    _cache.invalidate_company(user.company_id)
            finally:
                db.close()
        await asyncio.get_event_loop().run_in_executor(None, _sync)
    except Exception:
        pass


def create_app() -> FastAPI:
    from app.limiter import limiter

    from app import monitoring
    monitoring.init_sentry(settings.sentry_dsn, settings.sentry_environment, settings.sentry_traces_sample_rate)
    monitoring.install_query_timing(engine, settings.slow_query_ms)
    from app import database as _database
    if _database.read_engine is not None:
        monitoring.install_query_timing(_database.read_engine, settings.slow_query_ms)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # Brotli compresses ~15-20% smaller than gzip for text/JS/CSS at the same
    # quality; falls back to gzip automatically for clients that don't send
    # "br" in Accept-Encoding, so this is a drop-in replacement for GZipMiddleware.
    # quality=6 (default is 4): ~10% smaller output for a few extra ms per
    # request — measured against this app's actual app.js/hrms.html: quality 4
    # -> 244KB/45KB in ~19/4ms, quality 6 -> 219KB/40KB in ~36/6ms. Worth it
    # since app.js is now cached for a year (this cost is paid once per
    # browser) and even HTML's few extra ms are imperceptible.
    app.add_middleware(BrotliMiddleware, minimum_size=1000, quality=6)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # Content Security Policy. 'unsafe-inline' is needed for the pages' inline scripts and
    # onclick handlers; the policy still blocks scripts from other sites and data being sent
    # to them. CSP_MODE=report (or off) is a no-deploy escape hatch if something breaks.
    _api_origin = re.match(r"https?://[^/]+", os.environ.get("API_BASE_URL", "") or "")
    _csp = "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://api.qrserver.com https://flagcdn.com",
        "connect-src 'self'" + (f" {_api_origin.group(0)}" if _api_origin else ""),
        "media-src 'self' blob: data:",
        "frame-src 'self' blob: data:",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])
    _csp_mode = os.environ.get("CSP_MODE", "enforce").strip().lower()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(self), payment=(), usb=()"
        if _csp_mode == "enforce":
            response.headers["Content-Security-Policy"] = _csp
        elif _csp_mode == "report":
            response.headers["Content-Security-Policy-Report-Only"] = _csp
        # DO App Platform terminates TLS upstream and forwards plain HTTP to
        # the app, so request.url.scheme is unreliable -- X-Forwarded-Proto
        # is what actually reflects what the browser used. Only send HSTS
        # when the browser reached us over HTTPS, so a plain-http local/
        # scratch server (no proxy in front) never gets it either.
        if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.middleware("http")
    async def cache_invalidation(request: Request, call_next):
        response = await call_next(request)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/v1/" in request.url.path:
            import asyncio
            auth_header = request.headers.get("authorization", "")
            asyncio.create_task(_invalidate_cache_bg(auth_header))
        return response

    @app.middleware("http")
    async def request_timing(request: Request, call_next):
        # Per-endpoint timings, slow-request/slow-query logs, Server-Timing
        # header -- see app/monitoring.py.
        return await monitoring.time_request(request, call_next, settings.slow_request_ms)

    @app.middleware("http")
    async def request_load_tracking(request: Request, call_next):
        # Feeds the superadmin "Live Load" panel (system-health) — scoped to
        # /api/v1/ only so static asset traffic doesn't dilute the signal of
        # how many actual app requests are in flight right now.
        from app.request_metrics import request_finished, request_started
        is_api = "/api/v1/" in request.url.path
        if is_api:
            request_started()
        try:
            return await call_next(request)
        finally:
            if is_api:
                request_finished()

    @app.middleware("http")
    async def static_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and response.status_code == 200 and "cache-control" not in response.headers:
            path = request.url.path
            last_segment = path.rsplit("/", 1)[-1]
            ext = last_segment.rsplit(".", 1)[-1].lower() if "." in last_segment else ""
            if ext in ("js", "css") and "v=" in request.url.query:
                # Cache-busted via ?v=... query string, so it's safe to cache "forever" —
                # any future edit ships under a new query string and misses this cache entirely.
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif ext in ("woff2", "woff") and not path.startswith("/api/"):
                # Self-hosted fonts: a given file name never changes content, cache "forever".
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif ext in ("png", "jpg", "jpeg", "gif", "svg", "ico", "webp") and not path.startswith("/api/"):
                # Static site images (logo etc.). Not versioned, so a day (not a year); never
                # applied to /api/ paths, which can return per-user images.
                response.headers["Cache-Control"] = "public, max-age=86400"
            elif ext == "html" or path in ("", "/"):
                # Never cache HTML itself — it's the only thing that references the current
                # ?v=... asset URLs above, so it must always be revalidated on load.
                response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        from fastapi import HTTPException as _HTTPEx
        if isinstance(exc, _HTTPEx):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        import logging
        from sqlalchemy.exc import OperationalError as _SQLAOperationalError
        from sqlalchemy.exc import TimeoutError as _SQLATimeoutError
        if isinstance(exc, (_SQLATimeoutError, _SQLAOperationalError)):
            # TimeoutError: the local connection pool couldn't hand out a
            # connection within pool_timeout. OperationalError: the DB
            # SERVER itself refused or dropped the connection (e.g. its own
            # max_connections ceiling), a DBAPI-level failure distinct from
            # the pool-side one above -- live load testing found this is at
            # least as common as TimeoutError in practice, so both get the
            # same treatment. Either way the server is momentarily
            # overloaded, not broken: 503+Retry-After is the correct signal
            # (distinct from a real bug's 500) and is what the frontend's
            # retry logic keys off of. Most callers of dashboard()/
            # report_summary() never reach here at all -- reports.py's
            # _cached_or_build() already falls back to stale cached data
            # first for both exception types; this only fires when there's
            # truly nothing cached yet.
            logging.getLogger("taxflow").warning("DB overload (%s) on %s %s", type(exc).__name__, request.method, request.url.path)
            return JSONResponse(status_code=503, content={"detail": "Service temporarily busy, please retry."}, headers={"Retry-After": "3"})
        logging.getLogger("taxflow").error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
        monitoring.capture_exception(exc)
        return JSONResponse(status_code=500, content={"detail": "An internal error occurred."})

    def _run_startup_tasks(log) -> None:
        """Migrations, initial data and idempotent backfills; startup() runs this under
        app.migrate.startup_lock() so concurrent workers take turns."""
        try:
            if settings.run_migrations_on_startup:
                from app.migrate import run_migrations
                run_migrations()
            seed_initial_data()
        except Exception as exc:
            log.error("Startup DB init failed (app will still serve traffic): %s", exc)
        try:
            from app.routers.hr_access import backfill_default_role_permissions, backfill_main_module_edit
            with SessionLocal() as db:
                backfill_default_role_permissions(db)
                backfill_main_module_edit(db)
        except Exception as exc:
            log.error("Default role permission backfill failed: %s", exc)
        try:
            from app.company_defaults import backfill_default_ledgers
            with SessionLocal() as db:
                backfill_default_ledgers(db)
        except Exception as exc:
            log.error("Default ledger backfill failed: %s", exc)
        try:
            from app.company_defaults import backfill_user_roles_from_ui
            with SessionLocal() as db:
                backfill_user_roles_from_ui(db)
        except Exception as exc:
            log.error("User role backfill failed: %s", exc)

    @app.on_event("startup")
    def startup() -> None:
        import logging
        log = logging.getLogger("taxflow")
        settings.assert_production_secrets()
        from app.migrate import startup_lock
        try:
            with startup_lock():
                _run_startup_tasks(log)  # each task logs and swallows its own failure
        except Exception as exc:
            log.error("Startup DB init failed (app will still serve traffic): %s", exc)
        # For SQLite: flush the WAL to the main database file on every startup so
        # the WAL never grows unbounded between sessions.
        if settings.database_url.startswith("sqlite"):
            try:
                from app.database import engine as _eng
                with _eng.connect() as conn:
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                log.info("SQLite WAL checkpoint completed")
            except Exception as exc:
                log.warning("SQLite WAL checkpoint failed: %s", exc)

    site_dir = static_dir / "site"

    @app.get("/", response_model=None)
    def root():
        from fastapi.responses import RedirectResponse
        index = static_dir / "taxflow" / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return RedirectResponse(url="/login", status_code=302)

    def _db_error_kind(exc: Exception) -> str:
        """What kind of database failure, for the public /health: a fixed phrase, never
        the driver's message (it can contain host names)."""
        msg = str(exc).lower()
        for needle, kind in (
            ("password authentication failed", "wrong password in DATABASE_URL"),
            ("no pg_hba.conf entry", "connection not allowed (trusted sources)"),
            ("does not exist", "database, pool or user name not found"),
            ("timeout", "timed out (trusted sources or wrong host/port)"),
            ("timed out", "timed out (trusted sources or wrong host/port)"),
            ("could not translate host name", "host name not found"),
            ("connection refused", "connection refused (wrong host/port)"),
            ("too many", "too many connections"),
            ("ssl", "SSL problem (sslmode=require?)"),
            ("server closed the connection", "server closed the connection"),
        ):
            if needle in msg:
                return kind
        return type(exc).__name__

    # Bumped with each deploy-relevant change, so /health shows which code is live (the image
    # has no git metadata). Format: date.sequence.
    BUILD = "2026-10-10.5"

    def monitoring_sentry_on() -> bool:
        from app import monitoring as _monitoring
        return _monitoring._sentry_on

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            db.close()
            db_status = "ok"
        except Exception as exc:
            db_status = "error: " + _db_error_kind(exc)
        # Diagnostic only — app.cache already degrades gracefully if Redis is
        # unreachable (returns None / no-ops instead of raising), which means
        # a broken connection is otherwise invisible: nothing errors, caching
        # just silently never helps. Surfacing the real state here so that's
        # checkable without DB/log access.
        from app import cache as _cache
        redis_client = _cache._redis()
        if redis_client is not None:
            redis_status = "ok"
        elif _cache.last_error:
            redis_status = f"error: {_cache.last_error}"
            from app.config import get_settings as _gs
            hint = _cache.url_problem(_gs().redis_url)
            if hint:
                redis_status += f" (REDIS_URL: {hint})"
        else:
            redis_status = "disabled (no REDIS_URL)"
        if redis_client is not None:
            try:
                redis_client.ping()
            except Exception as exc:
                redis_status = f"error: {type(exc).__name__}"  # public endpoint: no host names
        return {
            "status": "ok" if db_status == "ok" else "degraded",
            "db": db_status,
            "redis": redis_status,
            "sentry": "on" if monitoring_sentry_on() else "off",
            "build": BUILD,
            "service": settings.app_name,
        }

    @app.get("/landing.html", include_in_schema=False)
    def landing() -> FileResponse:
        return FileResponse(str(site_dir / "landing.html"))

    @app.get("/signup.html", include_in_schema=False)
    def signup() -> FileResponse:
        return FileResponse(str(site_dir / "signup.html"))

    @app.get("/contact.html", include_in_schema=False)
    def contact() -> FileResponse:
        return FileResponse(str(site_dir / "contact.html"))

    # Primary routes at root path
    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "login.html"))

    @app.get("/signup", include_in_schema=False)
    def signup_page() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "signup.html"))

    @app.get("/superadmin", include_in_schema=False)
    def superadmin_page() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "superadmin.html"))

    @app.get("/digital-invoice.html", include_in_schema=False)
    def digital_invoice() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "digital-invoice.html"))

    @app.get("/i/{code}", include_in_schema=False)
    def short_invoice_link(code: str) -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "digital-invoice.html"))

    @app.get("/pos", include_in_schema=False)
    def pos_terminal() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "pos.html"))

    @app.get("/taxflow/pos.html", include_in_schema=False)
    def pos_terminal_html() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "pos.html"))

    @app.get("/hrms", include_in_schema=False)
    def hrms_portal() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "hrms.html"))  # HRMS portal

    @app.get("/ess", include_in_schema=False)
    def ess_portal() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "ess.html"))  # Employee Self-Service

    @app.get("/handbook", include_in_schema=False)
    def user_handbook() -> FileResponse:
        # Previously an external claude.ai artifact link (Quick Actions'
        # "Handbook" button, index.html/hrms.html) — served locally now so
        # it works without a separate claude.ai session/login and stays in
        # the app's own domain like every other standalone page here.
        return FileResponse(str(static_dir / "taxflow" / "handbook.html"))

    @app.get("/config.js", include_in_schema=False)
    def config_js() -> Response:
        api_base = os.environ.get("API_BASE_URL", "")
        content = (
            f'window.TAXFLOW_API_BASE_URL = "{api_base}";\n' if api_base
            else "// local dev — app.js falls back to localhost:8000\n"
        )
        return Response(content=content, media_type="application/javascript")

    # Serve the minified app.js build (frontend/scripts/build-min.mjs) instead
    # of the source file, but ONLY when its embedded source hash still matches
    # the current app.js on disk. If someone edits app.js and forgets to
    # regenerate the minified twin, this falls back to serving the original
    # source — correctness always wins over the size/speed win, never the
    # other way around.
    @app.get("/src/app.js", include_in_schema=False)
    @app.get("/taxflow/src/app.js", include_in_schema=False)
    def app_js() -> Response:
        content, is_min = _resolve_app_js()
        response = Response(content=content, media_type="text/javascript")
        if is_min:
            response.headers["X-Served-Variant"] = "minified"
        return response

    # Same minified-with-hash-fallback serving as app.js above, extended to
    # ess.js (the ESS portal's own dedicated bundle) -- these explicit routes
    # take priority over the generic StaticFiles mount below, which is what
    # served ess.js/ess.css unminified before this existed.
    @app.get("/src/ess.js", include_in_schema=False)
    @app.get("/taxflow/src/ess.js", include_in_schema=False)
    def ess_js() -> Response:
        content, is_min = _resolve_ess_js()
        response = Response(content=content, media_type="text/javascript")
        if is_min:
            response.headers["X-Served-Variant"] = "minified"
        return response

    # Legacy /taxflow/* redirects for backward compatibility
    @app.get("/taxflow", include_in_schema=False)
    def taxflow_root_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=301)

    @app.get("/taxflow/", include_in_schema=False)
    def taxflow_slash_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=301)

    @app.get("/taxflow/login", include_in_schema=False)
    def taxflow_login_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=301)

    @app.get("/taxflow/superadmin", include_in_schema=False)
    def taxflow_superadmin_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/superadmin", status_code=301)

    @app.get("/taxflow/config.js", include_in_schema=False)
    def taxflow_config_js_redirect() -> Response:
        api_base = os.environ.get("API_BASE_URL", "")
        content = (
            f'window.TAXFLOW_API_BASE_URL = "{api_base}";\n' if api_base
            else "// local dev — app.js falls back to localhost:8000\n"
        )
        return Response(content=content, media_type="application/javascript")

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(attendance.router, prefix="/api/v1")
    app.include_router(attendance.gated_router, prefix="/api/v1")
    app.include_router(attendance.short_router, prefix="/api/v1")
    # No /api/v1 prefix, deliberately — a real ZKTeco ADMS Cloud Server Mode
    # device hardcodes /iclock/cdata and /iclock/getrequest at the server
    # root (only Server IP + Port are configurable on the device itself, no
    # custom path), so these routes must live exactly there.
    app.include_router(attendance.iclock_router)
    app.include_router(ai.router, prefix="/api/v1")
    app.include_router(ai_voice.router, prefix="/api/v1")
    app.include_router(hr_ai.router, prefix="/api/v1")
    app.include_router(invoice_share.router, prefix="/api/v1")
    app.include_router(companies.router, prefix="/api/v1")
    app.include_router(branches.router, prefix="/api/v1")
    app.include_router(invoices.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(source_transactions.router, prefix="/api/v1")
    app.include_router(accounting.router, prefix="/api/v1")
    app.include_router(corporate_accounting.router, prefix="/api/v1")
    app.include_router(tax.router, prefix="/api/v1")
    app.include_router(inventory.router, prefix="/api/v1")
    app.include_router(payroll.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(exception_center.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(module_records.router, prefix="/api/v1")
    app.include_router(app_data.router, prefix="/api/v1")
    app.include_router(registers.router, prefix="/api/v1")
    app.include_router(stock_feed.router, prefix="/api/v1")
    app.include_router(superadmin.router, prefix="/api/v1")
    app.include_router(ess.router, prefix="/api/v1")
    app.include_router(ess_voice.router, prefix="/api/v1")
    app.include_router(hr_access.router, prefix="/api/v1")
    app.include_router(hr_access.gated_router, prefix="/api/v1")
    app.include_router(leave.router, prefix="/api/v1")

    # Serve frontend static files
    if static_dir.exists():
        # Legacy mount — keeps /taxflow/* working for old bookmarks
        app.mount("/taxflow", StaticFiles(directory=str(static_dir / "taxflow"), html=True), name="taxflow")
        clients_dir = static_dir / "clients"
        if clients_dir.exists():
            app.mount("/clients", StaticFiles(directory=str(clients_dir)), name="clients")
        app.mount("/static-assets", StaticFiles(directory=str(static_dir)), name="assets")
        # Root asset mount — allows index.html at "/" to load src/app.js, src/styles.css etc.
        # html=False so it never serves index.html as SPA fallback (avoids masking API 404s)
        app.mount("/", StaticFiles(directory=str(static_dir / "taxflow")), name="taxflow-root")

    return app


def ensure_schema_updates(connection=None) -> None:
    """Legacy startup schema patches, kept for databases created before Alembic.

    FROZEN: new schema changes go in app/migrations/versions/, not here. This
    now runs only once per database -- from app.migrate.run_migrations(), when
    it adopts a pre-Alembic database -- and is still idempotent, so tests and
    that adoption step can call it safely.
    """
    if connection is None:
        with engine.begin() as connection:
            _apply_legacy_schema_updates(connection)
        return
    _apply_legacy_schema_updates(connection)


def _apply_legacy_schema_updates(connection) -> None:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if "companies" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("companies")}
        if "subscription_expires_at" not in existing_columns:
            connection.execute(text("ALTER TABLE companies ADD COLUMN subscription_expires_at VARCHAR(20)"))
        if "logo" not in existing_columns:
            connection.execute(text("ALTER TABLE companies ADD COLUMN logo TEXT"))
        if "fta_username" not in existing_columns:
            connection.execute(text("ALTER TABLE companies ADD COLUMN fta_username VARCHAR(255)"))
        for col, typedef in [
            ("trade_name", "VARCHAR(160)"),
            ("emirate", "VARCHAR(80)"),
            ("business_type", "VARCHAR(80)"),
            ("business_activity", "VARCHAR(160)"),
            ("legal_structure", "VARCHAR(80)"),
            ("trade_license_no", "VARCHAR(80)"),
            ("trade_license_issue_date", "VARCHAR(20)"),
            ("trade_license_expiry", "VARCHAR(20)"),
            ("free_zone", "VARCHAR(120)"),
            ("address", "VARCHAR(400)"),
            ("po_box", "VARCHAR(20)"),
            ("phone", "VARCHAR(40)"),
            ("website", "VARCHAR(160)"),
            ("departments", "TEXT"),
            ("branches", "TEXT"),
            ("modules_enabled", "TEXT"),
            ("currency", "VARCHAR(3) DEFAULT 'AED'"),
            ("vat_rate", "NUMERIC(5,2) DEFAULT 5.00"),
            ("stock_mode", "VARCHAR(20) DEFAULT 'with_stock'"),
            # Existing companies keep periodic: their purchases already went to 4000, so
            # perpetual COGS would drive 1200 Inventory negative. New companies: perpetual.
            ("inventory_accounting", "VARCHAR(20) DEFAULT 'periodic'"),
        ]:
            if col not in existing_columns:
                connection.execute(text(f"ALTER TABLE companies ADD COLUMN {col} {typedef}"))
    if "leave_requests" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("leave_requests")}
        for col, typedef in [
            ("cancelled_at", "TIMESTAMP WITH TIME ZONE"),
            ("cancelled_by", "VARCHAR(160)"),
            ("cancel_reason", "VARCHAR(300)"),
        ]:
            if col not in existing_columns:
                connection.execute(text(f"ALTER TABLE leave_requests ADD COLUMN {col} {typedef}"))
    if "users" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("users")}
        # Legacy column that held plain-text passwords; wipe it wherever it still exists.
        if "password_plain" in existing_columns:
            connection.execute(text("UPDATE users SET password_plain = NULL WHERE password_plain IS NOT NULL"))
        if "is_active" not in existing_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL"))
        if "last_login" not in existing_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN last_login TIMESTAMP WITH TIME ZONE"))
    if "employees" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("employees")}
        if "password_hash" not in existing_columns:
            connection.execute(text("ALTER TABLE employees ADD COLUMN password_hash VARCHAR(255)"))
        required_columns = {
            "username": "VARCHAR(80)",
            "role_id": "VARCHAR(36)",
            "work_location_id": "VARCHAR(36)",
            "branch_id": "VARCHAR(36)",
            "is_active": "BOOLEAN DEFAULT TRUE",
            "last_login": "TIMESTAMP WITH TIME ZONE",
            "last_activity": "TIMESTAMP WITH TIME ZONE",
            "password_changed_at": "TIMESTAMP WITH TIME ZONE",
            # Payroll's allowances field was hardcoded to 0.00 with no
            # column anywhere to source a real figure from — see
            # payroll.py's generate_payroll().
            "housing_allowance": "NUMERIC(12,2) DEFAULT 0",
            "transport_allowance": "NUMERIC(12,2) DEFAULT 0",
            "other_allowance": "NUMERIC(12,2) DEFAULT 0",
            # Mirrors the "employees" AppDataRecord's photo field (a
            # compressed base64 data URL) so ESS -- which reads this
            # SQL table, not the AppDataRecord JSON blob -- can show it.
            "photo": "TEXT",
        }
        photo_column_is_new = "photo" not in existing_columns
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE employees ADD COLUMN {column_name} {column_type}"))
        if photo_column_is_new:
            # One-time backfill, guarded on the column having just been
            # created (so this doesn't re-scan app_data_records on every
            # future startup): a photo saved from HRMS Edit Employee
            # before this SQL column existed only ever landed in the
            # "employees"/"staff" AppDataRecord JSON blob. Mirror it in
            # now so those employees show up correctly in ESS without
            # needing to be re-saved.
            photo_rows = connection.execute(text(
                "SELECT company_id, payload FROM app_data_records WHERE collection IN ('employees', 'staff')"
            )).fetchall()
            for company_id, payload_raw in photo_rows:
                try:
                    payload = json.loads(payload_raw or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                photo = str(payload.get("photo") or "").strip()
                emp_no = str(payload.get("id") or "").strip()
                if not photo or not emp_no:
                    continue
                connection.execute(
                    text(
                        "UPDATE employees SET photo=:photo WHERE company_id=:cid AND employee_no=:eno "
                        "AND (photo IS NULL OR photo='')"
                    ),
                    {"photo": photo, "cid": company_id, "eno": emp_no},
                )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_employees_branch_id ON employees (branch_id)"))
        # Portal usernames are unique platform-wide (not just per-company) so
        # /ess and /hr/login can look an employee up by username alone, with
        # no ?c=<company_id> link required. Partial index (WHERE username IS
        # NOT NULL) since most employees have no portal access at all.
        try:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_username "
                "ON employees (username) WHERE username IS NOT NULL"
            ))
        except Exception as idx_exc:
            # Pre-existing duplicate usernames (extremely unlikely — this
            # column is new) would block index creation; don't let that
            # abort the rest of the schema migration on startup.
            logging.getLogger("taxflow").error(
                "Could not create uq_employees_username (likely duplicate usernames already exist): %s", idx_exc
            )
    if "roles" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("roles")}
        if "department_scope" not in existing_columns:
            # JSON-encoded list of department names. A role scoped to one
            # or more departments grants its holder (once given ESS Portal
            # Access) visibility of every employee in those departments —
            # see GET /ess/team. Empty/NULL means "no department scoping"
            # (the pre-existing behavior for every role created before
            # this column existed).
            connection.execute(text("ALTER TABLE roles ADD COLUMN department_scope TEXT"))
    if "company_locations" in table_names:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_company_locations_branch_id ON company_locations (branch_id)"))
    if "attendance_sessions" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("attendance_sessions")}
        if "branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE attendance_sessions ADD COLUMN branch_id VARCHAR(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_attendance_sessions_branch_id ON attendance_sessions (branch_id)"))
        # check_in() had a check-then-insert race (no lock, no unique
        # constraint) — two near-simultaneous check-ins for the same
        # employee could both pass the "already open?" query and both
        # insert an "open" session. De-duplicate any that already exist
        # (keep the most recent, force-close the rest the same way
        # _maybe_auto_checkout() already does) before adding the unique
        # index that stops it happening again — the index creation
        # itself would otherwise fail outright if duplicates were present.
        connection.execute(text("""
            UPDATE attendance_sessions SET status='closed', auto_checkout=TRUE
            WHERE status='open' AND id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY company_id, employee_id ORDER BY check_in DESC
                    ) AS rn
                    FROM attendance_sessions WHERE status='open'
                ) ranked WHERE rn = 1
            )
        """))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_sessions_open_per_employee "
            "ON attendance_sessions (company_id, employee_id) WHERE status='open'"
        ))
    if "biometric_devices" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("biometric_devices")}
        required_columns = {
            "biotime_base_url": "VARCHAR(255)",
            "biotime_username": "VARCHAR(120)",
            "biotime_password_enc": "TEXT",
            "biotime_token": "TEXT",
            "biotime_token_expires_at": "TIMESTAMP WITH TIME ZONE",
            # ZKTeco ADMS Classic (real iClock wire protocol) identifies a
            # device by its own hardware serial number, not a bearer key.
            "serial_number": "VARCHAR(40)",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE biometric_devices ADD COLUMN {column_name} {column_type}"))
        # Unconditional CREATE INDEX IF NOT EXISTS (not gated on the
        # column having just been added) — same idempotent-every-startup
        # pattern as uq_attendance_punch_dedup above; gating it on
        # existing_columns would silently skip creating the index forever
        # on any deploy where the column already existed from a prior run.
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_biometric_devices_serial_number "
            "ON biometric_devices (serial_number)"
        ))
    if "impersonation_sessions" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("impersonation_sessions")}
        if "target_branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE impersonation_sessions ADD COLUMN target_branch_id VARCHAR(36)"))
        # target_user_id was NOT NULL from this table's original creation
        # (impersonation only ever targeted a company admin user) — a new
        # "impersonate as branch" session (superadmin.py) has no target
        # user at all. Postgres needs an explicit ALTER to relax this;
        # harmless/idempotent to re-run, and a no-op once already
        # nullable. SQLite (local dev/tests) recreates this table fresh
        # via Base.metadata.create_all() before this ever runs, so it
        # picks up the model's nullable=True directly and doesn't need
        # ALTER COLUMN at all (which SQLite's ALTER TABLE can't do anyway).
        if not settings.database_url.startswith("sqlite"):
            try:
                connection.execute(text(
                    "ALTER TABLE impersonation_sessions ALTER COLUMN target_user_id DROP NOT NULL"
                ))
            except Exception as alter_exc:
                logging.getLogger("taxflow").error(
                    "Could not relax impersonation_sessions.target_user_id NOT NULL: %s", alter_exc
                )
    if "stock_product_mappings" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("stock_product_mappings")}
        required_columns = {
            "supplier_name": "VARCHAR(160)",
            "taxflow_name": "VARCHAR(160)",
            "units_per_outer": "NUMERIC(12, 4) DEFAULT 1",
            "cost": "NUMERIC(12, 2) DEFAULT 0",
            "markup_percent": "NUMERIC(8, 2) DEFAULT 0",
            "tax_rate": "NUMERIC(5, 2) DEFAULT 5",
            "vat_amount": "NUMERIC(12, 2) DEFAULT 0",
            "inc_vat": "NUMERIC(12, 2) DEFAULT 0",
            "price_outer": "NUMERIC(12, 2) DEFAULT 0",
            # Existing rows predate this column and were created under
            # the old all-implicit system — default them to TRUE so
            # this migration doesn't retroactively relabel already-
            # curated mappings as "Needs Review". Going forward, the
            # application code explicitly sets this False on auto-create
            # and True only on an explicit user save (see models.py).
            "mapping_confirmed": "BOOLEAN DEFAULT TRUE",
            "tracking": "VARCHAR(20) DEFAULT 'Yes'",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE stock_product_mappings ADD COLUMN {column_name} {column_type}"))
    if "item_units" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("item_units")}
        required_columns = {
            "purchase_default": "BOOLEAN DEFAULT FALSE",
            "sales_default": "BOOLEAN DEFAULT FALSE",
            "status": "VARCHAR(30) DEFAULT 'active'",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE item_units ADD COLUMN {column_name} {column_type}"))
    if "invoices" in table_names:
        index_names = {index["name"] for index in inspector.get_indexes("invoices")}
        if "uq_invoice_company_number" not in index_names:
            connection.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_company_number ON invoices (company_id, invoice_number)")
            )
        existing_columns = {column["name"] for column in inspector.get_columns("invoices")}
        if "branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE invoices ADD COLUMN branch_id VARCHAR(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_invoices_branch_id ON invoices (branch_id)"))
    if "tax_lines" in table_names:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tax_lines_company_direction ON tax_lines (company_id, direction)"))
    if "general_ledger_entries" in table_names:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_gl_entries_company_account_date ON general_ledger_entries (company_id, account_id, entry_date)"))
    if "invoice_lines" in table_names:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_invoice_lines_invoice_id ON invoice_lines (invoice_id)"))
    for branch_scoped_table in ("journal_entries", "general_ledger_entries", "source_transactions"):
        if branch_scoped_table in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns(branch_scoped_table)}
            if "branch_id" not in existing_columns:
                connection.execute(text(f"ALTER TABLE {branch_scoped_table} ADD COLUMN branch_id VARCHAR(36)"))
            connection.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{branch_scoped_table}_branch_id ON {branch_scoped_table} (branch_id)"
            ))
    if "audit_logs" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("audit_logs")}
        if "employee_id" not in existing_columns:
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN employee_id VARCHAR(36)"))
        if "branch_actor_id" not in existing_columns:
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN branch_actor_id VARCHAR(36)"))
    if "branches" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("branches")}
        if "modules_enabled" not in existing_columns:
            connection.execute(text("ALTER TABLE branches ADD COLUMN modules_enabled TEXT"))
        # Branch Login Phase 2 — the branch entity's own shared login.
        required_columns = {
            "username": "VARCHAR(80)",
            "password_hash": "VARCHAR(255)",
            "password_changed_at": "TIMESTAMP WITH TIME ZONE",
            "last_login": "TIMESTAMP WITH TIME ZONE",
            "last_activity": "TIMESTAMP WITH TIME ZONE",
            "country": "VARCHAR(60)",
            "currency": "VARCHAR(10)",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE branches ADD COLUMN {column_name} {column_type}"))
        # Global uniqueness (not per-company), mirroring uq_employees_username
        # — the shared /login page resolves a branch by username alone, no
        # company selector required.
        try:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_branches_username "
                "ON branches (username) WHERE username IS NOT NULL"
            ))
        except Exception as idx_exc:
            logging.getLogger("taxflow").error(
                "Could not create uq_branches_username (likely duplicate usernames already exist): %s", idx_exc
            )
    if "app_data_records" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("app_data_records")}
        if "branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE app_data_records ADD COLUMN branch_id VARCHAR(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_app_data_records_branch_id ON app_data_records (branch_id)"))
        if "record_date" not in existing_columns:
            connection.execute(text("ALTER TABLE app_data_records ADD COLUMN record_date VARCHAR(10)"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_app_data_company_collection_date "
            "ON app_data_records (company_id, collection, record_date)"
        ))
        # Once: stamp record_date on rows saved before the column existed (new saves stamp themselves).
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
        if not connection.execute(text("SELECT 1 FROM schema_flags WHERE name = 'app_data_record_date_v1'")).first():
            from app.models import DATED_COLLECTIONS, payload_record_date
            rows = connection.execute(
                text("SELECT id, payload FROM app_data_records WHERE collection IN :c AND record_date IS NULL")
                .bindparams(bindparam("c", expanding=True)),
                {"c": sorted(DATED_COLLECTIONS)},
            ).all()
            updates = [{"i": rid, "d": day} for rid, payload in rows if (day := payload_record_date(payload))]
            if updates:
                connection.execute(text("UPDATE app_data_records SET record_date = :d WHERE id = :i"), updates)
            connection.execute(text("INSERT INTO schema_flags (name) VALUES ('app_data_record_date_v1') ON CONFLICT (name) DO NOTHING"))
    if "stock_movements" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("stock_movements")}
        if "branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE stock_movements ADD COLUMN branch_id VARCHAR(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_stock_movements_branch_id ON stock_movements (branch_id)"))
    if "payroll_runs" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("payroll_runs")}
        if "branch_id" not in existing_columns:
            connection.execute(text("ALTER TABLE payroll_runs ADD COLUMN branch_id VARCHAR(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payroll_runs_branch_id ON payroll_runs (branch_id)"))
        # generate_payroll() (payroll.py) only guarded against a
        # duplicate run with a check-then-insert SELECT — two
        # near-simultaneous POST /payroll/generate calls for the same
        # period/branch could both pass the check before either
        # committed, each independently decrementing loan balances and
        # marking advances "Repaid" a second time. Two unique indexes,
        # same idempotent-every-startup pattern as
        # uq_attendance_sessions_open_per_employee above: one for a
        # specific branch's run, one (partial, branch_id IS NULL) for a
        # company-wide run. Wrapped in try/except rather than
        # de-duplicating first — unlike attendance sessions, merging or
        # deleting an existing duplicate PAYROLL run could destroy real,
        # already-paid payslip data or double-undo a loan/advance
        # balance; a DB that already has duplicates keeps them and just
        # doesn't get the new protection until someone resolves it by hand.
        try:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_runs_company_period_branch "
                "ON payroll_runs (company_id, period, branch_id) WHERE branch_id IS NOT NULL"
            ))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_runs_company_period_companywide "
                "ON payroll_runs (company_id, period) WHERE branch_id IS NULL"
            ))
        except Exception as payroll_idx_exc:
            logging.getLogger("taxflow").error(
                "Could not create payroll_runs duplicate-run unique indexes (existing duplicate rows?): %s",
                payroll_idx_exc,
            )
    if "accounts" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("accounts")}
        required_columns = {
            "parent_account_id": "VARCHAR(36)",
            "opening_balance": "NUMERIC(18, 2) DEFAULT 0",
            "opening_balance_type": "VARCHAR(2) DEFAULT 'DR'",
            "currency": "VARCHAR(10) DEFAULT 'AED'",
            "tax_applicable": "BOOLEAN DEFAULT FALSE",
            "is_bank_cash": "BOOLEAN DEFAULT FALSE",
            "is_control_account": "BOOLEAN DEFAULT FALSE",
            "level": "INTEGER DEFAULT 5",
            "is_group": "BOOLEAN DEFAULT FALSE",
            "node_type": "VARCHAR(30) DEFAULT 'POSTING_LEDGER'",
            "normal_balance": "VARCHAR(2) DEFAULT 'DR'",
            "created_mode": "VARCHAR(20) DEFAULT 'manual'",
            "status": "VARCHAR(20) DEFAULT 'active'",
            "ai_confidence": "NUMERIC(5,2)",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type}"))
        # Backfill node_type for existing accounts
        connection.execute(text(
            "UPDATE accounts SET node_type='MAIN_LEDGER' WHERE is_group=TRUE AND level=1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
        ))
        connection.execute(text(
            "UPDATE accounts SET node_type='SUB_LEDGER' WHERE is_group=TRUE AND level>1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
        ))
    if "client_errors" not in table_names:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS client_errors (
                id VARCHAR(36) PRIMARY KEY,
                company_id VARCHAR(36),
                user_id VARCHAR(36),
                message TEXT NOT NULL,
                stack TEXT,
                url VARCHAR(500),
                context VARCHAR(120),
                user_agent VARCHAR(500),
                page VARCHAR(80),
                viewport VARCHAR(20),
                occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_client_errors_company ON client_errors (company_id)"))
    else:
        existing_columns = {column["name"] for column in inspector.get_columns("client_errors")}
        if "page" not in existing_columns:
            connection.execute(text("ALTER TABLE client_errors ADD COLUMN page VARCHAR(80)"))
        if "viewport" not in existing_columns:
            connection.execute(text("ALTER TABLE client_errors ADD COLUMN viewport VARCHAR(20)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_client_errors_occurred ON client_errors (occurred_at DESC)"))
    # Composite indexes on app_data_records for scale
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_app_data_company_collection "
        "ON app_data_records (company_id, collection)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_app_data_company_collection_created "
        "ON app_data_records (company_id, collection, created_at)"
    ))
    # Join columns behind trial balance / balance sheet / ledger drill-down /
    # payroll queries — these tables predate their FK columns having an
    # index, so every one of those reports did a full scan on them. The
    # model-level index=True only takes effect for a brand-new table;
    # these CREATE INDEX IF NOT EXISTS calls are what actually backfills
    # it onto the already-running production database.
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_journal_lines_journal_id ON journal_lines (journal_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_journal_lines_account_id ON journal_lines (account_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_general_ledger_entries_account_id ON general_ledger_entries (account_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payroll_items_run_id ON payroll_items (run_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payroll_items_employee_id ON payroll_items (employee_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_voucher_lines_voucher_id ON voucher_lines (voucher_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_voucher_lines_account_id ON voucher_lines (account_id)"))
    # Composite indexes backing reports.py's repeated status/module/date
    # filters (dashboard ~30 queries, summary ~40-50 queries per call) —
    # same backfill reasoning as the block above: the model-level
    # Index()s in models.py only take effect for a brand-new table.
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_invoices_company_status ON invoices (company_id, status)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_tx_company_module ON source_transactions (company_id, module)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_tx_company_status ON source_transactions (company_id, status)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_journal_entries_company_status ON journal_entries (company_id, status)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_journal_entries_company_date ON journal_entries (company_id, entry_date)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_company_created ON audit_logs (company_id, created_at)"))
    if "trial_requests" in table_names:
        existing_columns = {c["name"] for c in inspector.get_columns("trial_requests")}
        if "employee_count" not in existing_columns:
            connection.execute(text("ALTER TABLE trial_requests ADD COLUMN employee_count VARCHAR(40)"))
    if "trial_requests" not in table_names:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS trial_requests (
                id VARCHAR(36) PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                company_name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                phone VARCHAR(60),
                interest VARCHAR(120),
                notes TEXT,
                status VARCHAR(30) DEFAULT 'new',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_trial_requests_status ON trial_requests (status)"))

    # "backup" module (Superadmin > Module Permissions) introduced after most
    # companies already had an explicit modules_enabled list. Those lists don't
    # contain "backup", which would now read as "switched off" and take away a
    # Download Backup they always had. Add it ONCE to every existing explicit
    # list; the marker keeps this from re-adding it after a super admin later
    # turns it off on purpose. NULL/empty lists (= everything on) need nothing.
    if "companies" in table_names:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"
        ))
        done = connection.execute(text(
            "SELECT 1 FROM schema_flags WHERE name = 'backup_module_backfill'"
        )).first()
        if not done:
            rows = connection.execute(text(
                "SELECT id, modules_enabled FROM companies WHERE modules_enabled IS NOT NULL AND modules_enabled <> ''"
            )).fetchall()
            for company_id, raw in rows:
                try:
                    mods = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(mods, list) and mods and "backup" not in mods:
                    connection.execute(
                        text("UPDATE companies SET modules_enabled = :m WHERE id = :i"),
                        {"m": json.dumps(mods + ["backup"]), "i": company_id},
                    )
            connection.execute(text("INSERT INTO schema_flags (name) VALUES ('backup_module_backfill') ON CONFLICT (name) DO NOTHING"))

    # The built-in "Manager" role (_ensure_default_roles, hr_access.py) was never
    # department-scoped until now -- every company's existing "Manager" role has
    # department_scope NULL, so its holders see every department's employees,
    # leave, attendance, rota etc. company-wide. Scope every existing one to "@own"
    # (the marker meaning "whatever department the login's own employee record is
    # in" -- same as the manual "Only the employee's own department" role option),
    # ONCE. Only touches rows this function itself created (is_system_role = true,
    # role_name = 'Manager', still at its untouched NULL default) -- a company that
    # renamed/repurposed that role, or deliberately opened it back up to every
    # department after this ran once, is never overwritten again. A custom role
    # some company separately named "Manager" (is_system_role = false) is untouched.
    if "companies" in table_names and "roles" in table_names:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"
        ))
        done = connection.execute(text(
            "SELECT 1 FROM schema_flags WHERE name = 'manager_role_own_department_backfill'"
        )).first()
        if not done:
            connection.execute(text(
                "UPDATE roles SET department_scope = :own "
                "WHERE role_name = 'Manager' AND is_system_role = true "
                "AND (department_scope IS NULL OR department_scope = '')"
            ), {"own": json.dumps(["@own"])})
            connection.execute(text(
                "INSERT INTO schema_flags (name) VALUES ('manager_role_own_department_backfill') ON CONFLICT (name) DO NOTHING"
            ))


def seed_initial_data() -> None:
    db: Session = SessionLocal()
    try:
        company = db.query(Company).filter(Company.trn == "100000000000003").first()
        if not company:
            company = Company(name="Company", trn="100000000000003")
            db.add(company)
            db.flush()
        else:
            company.name = "Company"

        user = db.query(User).filter(User.email == "admin@taxflowapp.com").first()
        legacy_user = db.query(User).filter(User.email == "admin@taxflow.local").first()
        if not user:
            if legacy_user:
                user = legacy_user
                user.email = "admin@taxflowapp.com"
            else:
                user = User(
                    company_id=company.id,
                    email="admin@taxflowapp.com",
                    full_name="Administrator",
                    role="admin",
                )
                db.add(user)

        admin_pwd = settings.admin_password
        superadmin_pwd = settings.superadmin_password

        user.full_name = "Administrator"
        user.role = "admin"
        user.password_hash = hash_password(admin_pwd)
        user.company_id = company.id

        seed_accounts(db, company.id)
        seed_voucher_types(db, company.id)
        seed_tax_codes(db, company.id)

        # Seed super admin (isolated company so it never appears in regular app)
        sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
        if not sa_company:
            sa_company = Company(name="ETaxFlow Admin", trn="SUPERADMIN-INTERNAL")
            db.add(sa_company)
            db.flush()
        sa_user = db.query(User).filter(User.email == "superadmin@etaxflow.com").first()
        if not sa_user:
            sa_user = User(
                company_id=sa_company.id,
                email="superadmin@etaxflow.com",
                full_name="Super Administrator",
                password_hash=hash_password(superadmin_pwd),
                role="superadmin",
            )
            db.add(sa_user)
        else:
            sa_user.role = "superadmin"
            sa_user.password_hash = hash_password(superadmin_pwd)
            sa_user.company_id = sa_company.id

        db.commit()
    finally:
        db.close()


app = create_app()
