"""`python -m app.migrate` refuses to run on the default SQLite file: as a deploy job
without DATABASE_URL it would "succeed" on a throwaway database (2026-10-09)."""
from app import migrate
from app.config import get_settings


def test_refuses_sqlite_unless_allowed(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "sqlite:///./taxflow.db")
    assert "DATABASE_URL is not set" in migrate._refuse_default_sqlite([])
    assert migrate._refuse_default_sqlite(["--allow-sqlite"]) is None


def test_runs_on_postgres_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql+psycopg2://u:p@h:25060/defaultdb")
    assert migrate._refuse_default_sqlite([]) is None
