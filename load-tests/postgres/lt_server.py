"""Runs the backend with N uvicorn workers against the local load-test PostgreSQL.

    python load-tests/postgres/lt_server.py PORT WORKERS POOL_SIZE MAX_OVERFLOW

DATABASE_URL defaults to postgresql+psycopg2://postgres@127.0.0.1:55432/taxflow_lt.
REDIS_URL defaults to memory:// (no cache: dashboard/report figures are recomputed on
every request, so results are a worst case for production, which caches them).
"""
import os
import pathlib
import sys

port, workers, pool, overflow = (int(x) for x in sys.argv[1:5])
backend = pathlib.Path(__file__).resolve().parents[2] / "backend"
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://postgres@127.0.0.1:55432/taxflow_lt")
os.environ.setdefault("SECRET_KEY", "load-test-secret-local-only")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.update(APP_ENV="development", DB_POOL_SIZE=str(pool), DB_MAX_OVERFLOW=str(overflow), DB_POOL_TIMEOUT="30",
                  SLOW_REQUEST_MS="2000", SLOW_QUERY_MS="1000")
os.chdir(backend)
sys.path.insert(0, str(backend))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, workers=workers, log_level="warning")
