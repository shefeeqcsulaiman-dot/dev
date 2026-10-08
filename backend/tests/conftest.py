import contextlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


TEST_DB = Path(__file__).resolve().parent.parent / f"taxflow-pytest-{os.getpid()}.db"
# TEST_DATABASE_URL runs the suite against another database (CI runs it on PostgreSQL as
# well as SQLite). It must be a throwaway database: every table is dropped at the start.
PG_TEST_URL = os.environ.get("TEST_DATABASE_URL")
os.environ["DATABASE_URL"] = PG_TEST_URL or f"sqlite:///./{TEST_DB.name}"
# APP_ENV defaults to "production" (app/config.py), whose startup check refuses the test
# passwords. A developer's backend/.env sets it locally; CI has no .env, so set it here.
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "taxflow-test-secret"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["TESTING"] = "true"

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Company, User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    if PG_TEST_URL:
        # Start from an empty schema, including tables code creates outside the models
        # (e.g. schema_flags), so a re-run sees the same database a fresh CI job does.
        from sqlalchemy import text as _text

        with engine.begin() as conn:
            conn.execute(_text("DROP SCHEMA public CASCADE"))
            conn.execute(_text("CREATE SCHEMA public"))
    elif TEST_DB.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    yield
    # Every journal write path the suite exercised must have kept the pre-calculated
    # account totals (app/account_totals.py) equal to a live sum of journal lines.
    from sqlalchemy import text

    from app.account_totals import verify_company

    with SessionLocal() as check:
        companies = [c for (c,) in check.execute(text("SELECT id FROM companies"))]
        problems = [p for company_id in companies for p in verify_company(check, company_id)]
    engine.dispose()
    assert not problems, "account_period_totals out of step with journal lines:\n" + "\n".join(problems[:20])
    if TEST_DB.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB.unlink()


@pytest.fixture()
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(database):
    with TestClient(app) as test_client:
        yield test_client


def ensure_user(db: Session, email: str, trn: str, role: str = "admin") -> User:
    company = db.query(Company).filter(Company.trn == trn).first()
    if not company:
        company = Company(name=f"QA Tenant {trn[-3:]}", trn=trn, country="United Arab Emirates")
        db.add(company)
        db.flush()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(company_id=company.id, email=email, full_name=email.split("@")[0], role=role)
        db.add(user)
    user.company_id = company.id
    user.password_hash = hash_password("admin123")
    db.flush()
    seed_accounts(db, company.id)
    return user


def seed_accounts(db: Session, company_id: str) -> None:
    rows = [
        ("1000", "Cash and Bank", "asset"),
        ("1100", "Accounts Receivable", "asset"),
        ("1200", "Inventory", "asset"),
        ("2100", "Accounts Payable", "liability"),
        ("2200", "VAT Output Payable", "liability"),
        ("2210", "VAT Input Recoverable", "asset"),
        ("3000", "Sales Income", "sales"),
        ("4000", "Purchases", "purchase"),
        ("5000", "Cost of Goods Sold", "direct expense"),
    ]
    for code, name, account_type in rows:
        if not db.query(Account).filter(Account.company_id == company_id, Account.code == code).first():
            db.add(Account(company_id=company_id, code=code, name=name, type=account_type))
    db.commit()


@pytest.fixture()
def auth_headers(client, db):
    ensure_user(db, "qa-admin@taxflowqa.com", "900000000000001")
    db.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "qa-admin@taxflowqa.com", "password": "admin123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def second_tenant_headers(client, db):
    ensure_user(db, "qa-other@taxflowqa.com", "900000000000002")
    db.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "qa-other@taxflowqa.com", "password": "admin123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
