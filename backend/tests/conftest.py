import contextlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


TEST_DB = Path(__file__).resolve().parent.parent / f"taxflow-pytest-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB.name}"
os.environ["SECRET_KEY"] = "taxflow-test-secret"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Company, User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    if TEST_DB.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
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
