"""posted_account_totals(): computed once per session, never stale after a write."""
from decimal import Decimal
from uuid import uuid4

from app.models import Account, Company, JournalEntry, JournalLine
from app.routers.reports import balance_sheet_rows, posted_account_totals, trial_balance_rows


def _company(db):
    company = Company(name="Totals Co", trn=f"94{uuid4().int % 10**13:013d}", country="United Arab Emirates")
    db.add(company)
    db.flush()
    cash = Account(company_id=company.id, code="1000", name="Cash", type="asset")
    sales = Account(company_id=company.id, code="3000", name="Sales", type="sales")
    db.add_all([cash, sales])
    db.flush()
    return company, cash, sales


def _post(db, company, cash, sales, amount):
    entry = JournalEntry(company_id=company.id, entry_number=f"JE-{uuid4().hex[:6]}", description="t", status="posted")
    entry.lines = [JournalLine(account_id=cash.id, description="t", debit=amount, credit=Decimal("0")),
                   JournalLine(account_id=sales.id, description="t", debit=Decimal("0"), credit=amount)]
    db.add(entry)


def test_totals_are_shared_within_a_session_and_dropped_after_a_write(db):
    company, cash, sales = _company(db)
    _post(db, company, cash, sales, Decimal("100"))
    db.flush()
    first = posted_account_totals(db, company.id)
    assert first[cash.id] == (Decimal("100.00"), Decimal("0.00"))
    assert posted_account_totals(db, company.id) is first  # reused, not recomputed

    _post(db, company, cash, sales, Decimal("50"))
    db.flush()  # a write in the same session must not leave the old totals behind
    assert posted_account_totals(db, company.id)[cash.id] == (Decimal("150.00"), Decimal("0.00"))
    tb = {row["code"]: row for row in trial_balance_rows(db, company.id)}
    assert tb["1000"]["debit"] == "150.00" and tb["3000"]["credit"] == "150.00"
    assert balance_sheet_rows(db, company.id)["totals"]["assets"] == "150.00"
    db.rollback()
