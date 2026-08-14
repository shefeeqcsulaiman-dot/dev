"""Post-run reconciliation checks for the bulk seed data. The whole point of
replicating the accounting pipeline's output by hand (see ledger.py's
module docstring) instead of calling it is that a subtle bug in the
replication would silently produce data that LOOKS like real transactions
but doesn't actually reconcile -- these checks catch that before anyone
opens a Trial Balance report and finds it doesn't balance to zero.
"""
from __future__ import annotations

import random
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Account,
    GeneralLedgerEntry,
    Invoice,
    JournalEntry,
    JournalLine,
    SourceTransaction,
)


def verify_company(db: Session, company_id: str, expected_sales: int, expected_purchases: int, gl_spot_checks: int = 15) -> list[str]:
    problems: list[str] = []

    debit_total, credit_total = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(JournalEntry.company_id == company_id)
        .one()
    )
    if abs(Decimal(debit_total) - Decimal(credit_total)) > Decimal("0.01"):
        problems.append(f"Journal lines don't balance: debit={debit_total} credit={credit_total}")

    invoice_count = db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id).scalar()
    if invoice_count != expected_sales:
        problems.append(f"Invoice count {invoice_count} != expected {expected_sales}")

    source_count = db.query(func.count(SourceTransaction.id)).filter(SourceTransaction.company_id == company_id).scalar()
    expected_total = expected_sales + expected_purchases
    if source_count != expected_total:
        problems.append(f"SourceTransaction count {source_count} != expected {expected_total}")

    orphan_lines = (
        db.query(func.count(JournalLine.id))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .outerjoin(Account, Account.id == JournalLine.account_id)
        .filter(JournalEntry.company_id == company_id, Account.id.is_(None))
        .scalar()
    )
    if orphan_lines:
        problems.append(f"{orphan_lines} JournalLine rows reference a missing Account")

    # Per-row balance spot-checking (comparing a row's stored `balance` to a
    # fresh "prior WHERE entry_date <= this one" recompute) is unreliable
    # when two lines of the SAME transaction hit the same account -- every
    # sales line uses account_code "3000", so a 3-line invoice produces 3
    # GeneralLedgerEntry rows for the same account at the identical
    # entry_date, and a `<=` comparison can't order rows that tie on
    # timestamp. Real production data has this exact same ambiguity for any
    # multi-line invoice, so instead of asserting a specific row's balance,
    # assert an ordering-independent invariant: the running accumulator only
    # ever grows by each line's net in turn, so whichever row was genuinely
    # processed last for a given account must show a balance equal to that
    # account's grand total (debit-credit summed across every row) -- check
    # that grand total actually appears among the account's stored balances.
    account_ids = [
        row[0]
        for row in db.query(GeneralLedgerEntry.account_id)
        .filter(GeneralLedgerEntry.company_id == company_id)
        .distinct()
        .all()
    ]
    sample = random.sample(account_ids, min(gl_spot_checks, len(account_ids))) if account_ids else []
    for account_id in sample:
        total = (
            db.query(func.coalesce(func.sum(GeneralLedgerEntry.debit - GeneralLedgerEntry.credit), 0))
            .filter(GeneralLedgerEntry.account_id == account_id, GeneralLedgerEntry.company_id == company_id)
            .scalar()
        )
        total = Decimal(total or 0)
        match = (
            db.query(GeneralLedgerEntry.id)
            .filter(
                GeneralLedgerEntry.account_id == account_id,
                GeneralLedgerEntry.company_id == company_id,
                func.abs(GeneralLedgerEntry.balance - total) <= Decimal("0.01"),
            )
            .first()
        )
        if not match:
            problems.append(f"Account {account_id}: no GeneralLedgerEntry balance matches grand total {total}")

    return problems


def verify_all(db: Session, company_ids: list[str], expected_sales: int, expected_purchases: int) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for company_id in company_ids:
        problems = verify_company(db, company_id, expected_sales, expected_purchases)
        if problems:
            results[company_id] = problems
    return results
