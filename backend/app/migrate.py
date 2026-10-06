"""Database schema migrations (Alembic).

Every schema change now lives in app/migrations/versions/. This module applies
them, safely when several server processes start at the same moment:

- On PostgreSQL a session-level advisory lock means only one process migrates.
  The others wait for it and then find nothing left to do. Before this, each
  uvicorn worker ran its own CREATE/ALTER at startup, and the load test showed
  those clashing.
- A database that predates Alembic (no alembic_version table) is adopted once:
  tables are created, the frozen legacy patches in main.ensure_schema_updates()
  are applied, and it is stamped at the baseline revision before upgrading.
- A brand-new empty database is created from the models and stamped at head.

Run it as a pre-deploy step with `python -m app.migrate`, or let app startup
call it (RUN_MIGRATIONS_ON_STARTUP, on by default).

Adding a schema change:
    cd backend
    alembic revision -m "add invoices.foo"     # then fill in upgrade()/downgrade()
    alembic upgrade head
"""
from __future__ import annotations

import logging
import pathlib

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.database import Base, engine

log = logging.getLogger("taxflow")

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION = "0001_baseline"
# Arbitrary constant shared by every process: pg_advisory_lock key for migrations.
_ADVISORY_LOCK_KEY = 7_340_211_001


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        # env.py runs on this connection instead of opening its own, so the
        # whole migration happens while we hold the advisory lock.
        cfg.attributes["connection"] = connection
    return cfg


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _migrate(connection: Connection) -> None:
    import app.models  # noqa: F401  -- registers every table on Base.metadata

    cfg = alembic_config(connection)
    tables = set(inspect(connection).get_table_names())

    if "alembic_version" not in tables:
        if "companies" not in tables:
            log.info("Empty database: creating schema from models, stamping head")
            Base.metadata.create_all(bind=connection)
            connection.commit()
            command.stamp(cfg, "head")
            connection.commit()
            return
        log.info("Pre-Alembic database: applying legacy schema patches, stamping %s", BASELINE_REVISION)
        from app.main import ensure_schema_updates

        Base.metadata.create_all(bind=connection)
        ensure_schema_updates(connection)
        connection.commit()
        command.stamp(cfg, BASELINE_REVISION)
        connection.commit()

    before = current_revision(connection)
    command.upgrade(cfg, "head")
    connection.commit()
    after = current_revision(connection)
    if before != after:
        log.info("Database migrated %s -> %s", before, after)


def run_migrations(bind: Engine | None = None) -> None:
    """Bring the database schema up to the latest revision. Safe to call from
    many processes at once."""
    with (bind or engine).connect() as connection:
        is_pg = connection.dialect.name == "postgresql"
        if is_pg:
            connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
            connection.commit()
        try:
            _migrate(connection)
        finally:
            if is_pg:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
                connection.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_migrations()
