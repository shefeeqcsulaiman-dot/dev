"""Report reads on a read replica (app/read_replica.py). The "replica" here is a
read-only connection to the test database, so a report that tried to write would fail."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import cache, database
from tests.conftest import TEST_DB

REPORTS = ("/api/v1/reports/dashboard", "/api/v1/reports/summary",
           "/api/v1/reports/trial-balance", "/api/v1/reports/branch-performance")


class _Counting:
    def __init__(self, factory):
        self.factory = factory
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return self.factory()


@pytest.fixture()
def replica(monkeypatch):
    eng = create_engine(f"sqlite:///file:{TEST_DB.as_posix()}?mode=ro&uri=true",
                        connect_args={"check_same_thread": False})
    factory = _Counting(sessionmaker(bind=eng, autoflush=False, autocommit=False))
    monkeypatch.setattr(database, "ReadSessionLocal", factory)
    cache._recent_writes.clear()
    yield factory
    eng.dispose()


def _strip(body):
    body = dict(body)
    body.pop("_version", None)
    return body


def test_reports_build_on_the_replica_with_identical_results(client, auth_headers, replica, monkeypatch):
    on_replica = {path: client.get(path, headers=auth_headers) for path in REPORTS}
    assert replica.opened >= len(REPORTS)
    monkeypatch.setattr(database, "ReadSessionLocal", None)
    for path, response in on_replica.items():
        assert response.status_code == 200, (path, response.text)
        assert _strip(response.json()) == _strip(client.get(path, headers=auth_headers).json()), path


def test_company_that_just_wrote_reads_reports_from_the_primary(client, auth_headers, replica):
    company_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    cache.invalidate_company(company_id)  # what every write triggers
    assert cache.recently_written(company_id)
    for path in REPORTS:
        assert client.get(path, headers=auth_headers).status_code == 200
    assert replica.opened == 0


def test_unreachable_replica_falls_back_to_the_primary(client, auth_headers, monkeypatch, tmp_path):
    broken = create_engine(f"sqlite:///file:{(tmp_path / 'missing' / 'x.db').as_posix()}?mode=ro&uri=true")
    monkeypatch.setattr(database, "ReadSessionLocal", sessionmaker(bind=broken))
    cache._recent_writes.clear()
    for path in REPORTS:
        assert client.get(path, headers=auth_headers).status_code == 200, path
