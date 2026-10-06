"""Alembic adoption (app/migrate.py): fresh databases, pre-Alembic databases,
and repeat runs all end at the head revision with the full schema."""
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.migrate import BASELINE_REVISION, head_revision, run_migrations


@pytest.fixture()
def scratch_engine(tmp_path: pathlib.Path):
    eng = create_engine(f"sqlite:///{tmp_path / f'mig-{uuid.uuid4().hex}.db'}")
    yield eng
    eng.dispose()


def _revision(eng) -> str | None:
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_empty_database_is_created_and_stamped_at_head(scratch_engine):
    run_migrations(scratch_engine)
    tables = set(inspect(scratch_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert _revision(scratch_engine) == head_revision()


def test_pre_alembic_database_gets_legacy_patches_then_head(scratch_engine):
    # A database built the old way: create_all, no alembic_version table, and
    # missing a column that only the legacy startup patches add back.
    Base.metadata.create_all(bind=scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text("ALTER TABLE trial_requests DROP COLUMN employee_count"))
    assert "employee_count" not in {c["name"] for c in inspect(scratch_engine).get_columns("trial_requests")}

    run_migrations(scratch_engine)

    assert "employee_count" in {c["name"] for c in inspect(scratch_engine).get_columns("trial_requests")}
    assert "schema_flags" in inspect(scratch_engine).get_table_names()
    assert _revision(scratch_engine) == head_revision()


def test_rerun_is_a_no_op(scratch_engine):
    run_migrations(scratch_engine)
    run_migrations(scratch_engine)
    assert _revision(scratch_engine) == head_revision()


def test_baseline_is_the_root_revision():
    from alembic.script import ScriptDirectory
    from app.migrate import alembic_config

    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_base() == BASELINE_REVISION


def test_direct_url_is_used_for_migrations_when_set(tmp_path, monkeypatch):
    # Behind PgBouncer, migrations must bypass the pooler (DATABASE_DIRECT_URL).
    from app.config import get_settings

    direct = tmp_path / "direct.db"
    monkeypatch.setattr(get_settings(), "database_direct_url", f"sqlite:///{direct}")
    run_migrations()
    eng = create_engine(f"sqlite:///{direct}")
    try:
        assert _revision(eng) == head_revision()
    finally:
        eng.dispose()
