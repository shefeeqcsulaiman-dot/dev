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

Behind PgBouncer (transaction pooling) set DATABASE_DIRECT_URL to a direct,
non-pooled connection: the advisory lock is held per server session, which a
transaction pooler doesn't keep, and some DDL (CREATE INDEX CONCURRENTLY)
can't run inside the pooler's transactions either.

Adding a schema change:
    cd backend
    alembic revision -m "add invoices.foo"     # then fill in upgrade()/downgrade()
    alembic upgrade head
"""
from __future__ import annotations

import contextlib
import logging
import pathlib
from collections.abc import Iterator

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.database import Base, engine

log = logging.getLogger("taxflow")

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION = "0001_baseline"
# Arbitrary constants shared by every process: pg_advisory_lock keys for migrations,
# and for the whole startup sequence around them (see startup_lock()).
_ADVISORY_LOCK_KEY = 7_340_211_001
_STARTUP_LOCK_KEY = 7_340_211_002


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        # env.py runs on this connection instead of opening its own, so the
        # whole migration happens while we hold the advisory lock.
        cfg.attributes["connection"] = connection
    return cfg


def migration_engine() -> Engine:
    """The app engine, or a direct one-off connection when DATABASE_DIRECT_URL
    is set (needed behind PgBouncer -- see module docstring)."""
    direct = get_settings().database_direct_url
    if direct:
        return create_engine(direct, poolclass=NullPool, pool_pre_ping=True)
    return engine


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


# Revision ids that were renamed after release: old -> new. alembic_version.version_num is
# VARCHAR(32); 0012 was first named "0012_stock_movement_reference_index" (35 characters),
# which PostgreSQL refused to record (SQLite doesn't check lengths, so it can hold the old
# name). A database recorded under an old name is moved to the new one before upgrading,
# or Alembic would stop at a revision it no longer knows.
_RENAMED_REVISIONS = {"0012_stock_movement_reference_index": "0012_stock_movement_ref_index"}


def _apply_revision_renames(connection: Connection) -> None:
    for old, new in _RENAMED_REVISIONS.items():
        result = connection.execute(
            text("UPDATE alembic_version SET version_num = :new WHERE version_num = :old"), {"new": new, "old": old}
        )
        if result.rowcount:
            log.info("Renamed recorded revision %s -> %s", old, new)
    connection.commit()


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

    _apply_revision_renames(connection)
    before = current_revision(connection)
    command.upgrade(cfg, "head")
    connection.commit()
    after = current_revision(connection)
    if before != after:
        log.info("Database migrated %s -> %s", before, after)


def run_migrations(bind: Engine | None = None) -> None:
    """Bring the database schema up to the latest revision. Safe to call from
    many processes at once."""
    with (bind or migration_engine()).connect() as connection:
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


@contextlib.contextmanager
def startup_lock() -> Iterator[None]:
    """Lets one process at a time run the startup sequence (migrations, initial data,
    default-role/ledger backfills). Without it, several uvicorn workers starting together
    on PostgreSQL raced each other: the load test showed 3 of 4 failing with duplicate
    key errors on the initial company and on CREATE TABLE schema_flags. With it, later
    workers wait, then find the work done. Held on the direct connection (like
    migrations) so it still works behind a transaction-mode PgBouncer. No-op elsewhere."""
    with migration_engine().connect() as connection:
        if connection.dialect.name != "postgresql":
            yield
            return
        connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _STARTUP_LOCK_KEY})
        connection.commit()
        try:
            yield
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _STARTUP_LOCK_KEY})
            connection.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_migrations()
