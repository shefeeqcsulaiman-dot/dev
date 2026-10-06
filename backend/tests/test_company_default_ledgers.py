"""Default chart of accounts: Equity group + common ledgers for new companies, and a one-time
backfill for existing companies."""
from uuid import uuid4

from sqlalchemy import text

from app.company_defaults import backfill_default_ledgers
from app.models import Account, Company
from tests.test_module_permissions import _make_superadmin

NEW_CODES = {"250", "2500", "2600", "2700", "1010", "1020", "3100", "3200", "6100", "6200", "6300", "6400"}


def _codes(db, company_id):
    db.expire_all()
    return {a.code: a for a in db.query(Account).filter(Account.company_id == company_id).all()}


def test_new_company_gets_equity_and_common_ledgers(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"led-{tag}")
    r = client.post("/api/v1/superadmin/companies", headers=sa, json={
        "name": f"Ledger Co {tag}", "email": f"ledger-{tag}@example.com", "password": "admin12345"})
    assert r.status_code in (200, 201), r.text
    codes = _codes(db, r.json()["company_id"])
    assert NEW_CODES <= set(codes)
    assert codes["250"].is_group and codes["250"].type == "equity"
    assert codes["2500"].type == "equity" and codes["2500"].parent_account_id == codes["250"].id


def test_backfill_adds_missing_ledgers_once_without_touching_existing(client, db):
    company = Company(name=f"Old Co {uuid4().hex[:6]}")
    db.add(company)
    db.flush()
    db.add(Account(company_id=company.id, code="2500", name="My Own 2500", type="liability"))
    db.commit()
    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    db.execute(text("DELETE FROM schema_flags WHERE name = 'default_ledgers_v2'"))
    db.commit()

    backfill_default_ledgers(db)
    codes = _codes(db, company.id)
    assert {"1100", "2200", "250", "2600", "6100"} <= set(codes)
    assert codes["2500"].name == "My Own 2500"  # an existing code is never overwritten

    db.query(Account).filter(Account.company_id == company.id, Account.code == "6100").delete()
    db.commit()
    backfill_default_ledgers(db)  # flag set: runs only once
    assert "6100" not in _codes(db, company.id)
