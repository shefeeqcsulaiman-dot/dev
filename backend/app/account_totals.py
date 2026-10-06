"""Pre-calculated account totals: posted debit/credit per company, branch, account and month.

The trial balance, balance sheet and cost of sales used to add up every posted journal
line of a company on each request, a cost that grows with every year of history. They
now read account_period_totals (models.AccountPeriodTotal) instead: a few rows per
account per month.

Keeping it exact, whatever code path writes journals:
- Unit-of-work changes (JournalEntry/JournalLine added, edited, deleted through the
  session): the touched (company, month) pairs are recomputed from journal_lines right
  after the flush, inside the same transaction.
- Bulk statements (query(...).delete()/update(), insert(JournalLine) batches): caught in
  do_orm_execute wherever they run. Deletes/updates note their companies *before*
  running (the rows are gone afterwards); inserts note theirs from the parameters. Those
  companies are rebuilt in full when the session commits.
- REPORT_TOTALS_SOURCE=live makes reports add up journal lines directly again (the old
  behaviour) without a deploy, should a stored total ever be in doubt;
  verify_company() compares the two, and the test suite runs it on every company.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, event, func, insert, inspect, select
from sqlalchemy.orm import Session

from app.models import AccountPeriodTotal, JournalEntry, JournalLine

NO_DATE = "0000-00"
_PERIODS_KEY = "_account_totals_periods"     # {(company_id, period)} from flushes
_COMPANIES_KEY = "_account_totals_companies"  # {company_id} needing a full rebuild at commit
_JOURNALS_KEY = "_account_totals_journals"    # {journal_id} from bulk line inserts
_BUSY_KEY = "_account_totals_busy"


def period_of(value: Any) -> str:
    """The month bucket of an entry_date. Time-zone-aware values are taken in UTC, matching
    _period_bounds(): PostgreSQL compares timestamptz as instants, so an entry at 02:00 on
    the 1st in Dubai (+04:00) belongs to the previous month's UTC range."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.UTC)
        return value.strftime("%Y-%m")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m")
    return NO_DATE


def _period_bounds(period: str) -> tuple[_dt.datetime, _dt.datetime]:
    year, month = int(period[:4]), int(period[5:7])
    start = _dt.datetime(year, month, 1, tzinfo=_dt.UTC)
    end = _dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=_dt.UTC)
    return start, end


def _live_grouped(company_id: str, period: str | None):
    """Posted journal-line sums per (branch, account), for one month or all time."""
    query = (
        select(JournalEntry.branch_id, JournalLine.account_id,
               func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalEntry.company_id == company_id, JournalEntry.status == "posted")
        .group_by(JournalEntry.branch_id, JournalLine.account_id)
    )
    if period == NO_DATE:
        query = query.where(JournalEntry.entry_date.is_(None))
    elif period is not None:
        start, end = _period_bounds(period)
        query = query.where(JournalEntry.entry_date >= start, JournalEntry.entry_date < end)
    return query


def recompute_periods(db: Session, company_id: str, periods: Iterable[str]) -> None:
    """Replace the stored rows of `company_id` for these months with fresh sums."""
    db.info[_BUSY_KEY] = True
    try:
        with db.no_autoflush:
            for period in sorted(set(periods)):
                db.execute(delete(AccountPeriodTotal).where(
                    AccountPeriodTotal.company_id == company_id, AccountPeriodTotal.period == period))
                rows = [
                    {"company_id": company_id, "branch_id": branch_id, "account_id": account_id,
                     "period": period, "debit": debit, "credit": credit}
                    for branch_id, account_id, debit, credit in db.execute(_live_grouped(company_id, period))
                    if debit or credit
                ]
                if rows:
                    db.execute(insert(AccountPeriodTotal), rows)
    finally:
        db.info.pop(_BUSY_KEY, None)


def rebuild_company(db: Session, company_id: str) -> None:
    """Recompute every month a company has journals in, and drop months it no longer has."""
    with db.no_autoflush:
        dates = db.execute(select(JournalEntry.entry_date).where(JournalEntry.company_id == company_id)).scalars()
        periods = {period_of(d) for d in dates}
        stored = set(db.execute(select(AccountPeriodTotal.period).where(
            AccountPeriodTotal.company_id == company_id).distinct()).scalars())
    recompute_periods(db, company_id, periods | stored)


def account_totals(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, tuple[Decimal, Decimal]]:
    """{account_id: (debit, credit)} over all posted journal lines, from the stored totals.
    A branch sees its own rows plus untagged (NULL-branch) ones, as the live query did."""
    query = (
        select(AccountPeriodTotal.account_id, func.sum(AccountPeriodTotal.debit), func.sum(AccountPeriodTotal.credit))
        .where(AccountPeriodTotal.company_id == company_id)
        .group_by(AccountPeriodTotal.account_id)
    )
    if branch_id:
        query = query.where((AccountPeriodTotal.branch_id == branch_id) | AccountPeriodTotal.branch_id.is_(None))
    return {account_id: (Decimal(str(d or 0)), Decimal(str(c or 0))) for account_id, d, c in db.execute(query)}


def live_account_totals(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, tuple[Decimal, Decimal]]:
    query = _live_grouped(company_id, None)
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for row_branch, account_id, debit, credit in db.execute(query):
        if branch_id and row_branch not in (branch_id, None):
            continue
        d, c = out.get(account_id, (Decimal("0"), Decimal("0")))
        out[account_id] = (d + Decimal(str(debit or 0)), c + Decimal(str(credit or 0)))
    return out


def verify_company(db: Session, company_id: str) -> list[str]:
    """Differences between stored and live totals for a company (empty when they agree)."""
    stored, live = account_totals(db, company_id), live_account_totals(db, company_id)
    problems = []
    for account_id in set(stored) | set(live):
        s, l_ = stored.get(account_id, (0, 0)), live.get(account_id, (0, 0))
        if Decimal(str(s[0])) != Decimal(str(l_[0])) or Decimal(str(s[1])) != Decimal(str(l_[1])):
            problems.append(f"{company_id} account {account_id}: stored {s} != live {l_}")
    return problems


# ── keeping the totals current ────────────────────────────────────────────────

def _old(obj: Any, attr: str) -> list[Any]:
    return list(inspect(obj).attrs[attr].history.deleted or ())


_RESOLVE_KEY = "_account_totals_resolve"     # journal ids whose stored date decides the month


@event.listens_for(Session, "after_flush")
def _collect_flush_changes(session: Session, _ctx) -> None:
    """Note which (company, month) pairs this flush touched. Current months are read back
    from the database afterwards (a new entry's entry_date is a server default, not yet
    on the object); months an entry or line is leaving come from attribute history."""
    if session.info.get(_BUSY_KEY):
        return
    periods: set[tuple[str, str]] = session.info.setdefault(_PERIODS_KEY, set())
    resolve: set[str] = session.info.setdefault(_RESOLVE_KEY, set())
    rebuild: set[str] = session.info.setdefault(_COMPANIES_KEY, set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, JournalEntry):
            loaded = inspect(obj).dict
            old_companies = _old(obj, "company_id")
            for date in _old(obj, "entry_date"):
                for company_id in {loaded.get("company_id"), *old_companies}:
                    periods.add((company_id, period_of(date)))
            if obj in session.deleted:
                if "entry_date" in loaded and loaded.get("company_id"):
                    periods.add((loaded["company_id"], period_of(loaded["entry_date"])))
                elif loaded.get("company_id"):
                    rebuild.add(loaded["company_id"])
            else:
                resolve.add(obj.id)
                for company_id in old_companies:  # moved to another company: rebuild the old one
                    rebuild.add(company_id)
        elif isinstance(obj, JournalLine):
            resolve.update(j for j in {inspect(obj).dict.get("journal_id"), *_old(obj, "journal_id")} if j)


@event.listens_for(Session, "after_flush_postexec")
def _apply_flush_changes(session: Session, _ctx) -> None:
    if session.info.get(_BUSY_KEY):
        return
    periods: set[tuple[str, str]] = session.info.pop(_PERIODS_KEY, set())
    resolve: set[str] = session.info.pop(_RESOLVE_KEY, set())
    if resolve:
        with session.no_autoflush:
            for company_id, date in session.execute(
                    select(JournalEntry.company_id, JournalEntry.entry_date).where(JournalEntry.id.in_(resolve))):
                periods.add((company_id, period_of(date)))
    by_company: dict[str, set[str]] = {}
    for company_id, period in periods:
        if company_id:
            by_company.setdefault(company_id, set()).add(period)
    for company_id, company_periods in by_company.items():
        recompute_periods(session, company_id, company_periods)


@event.listens_for(Session, "do_orm_execute")
def _note_bulk_statements(state) -> None:
    session = state.session
    if session.info.get(_BUSY_KEY) or not (state.is_delete or state.is_update or state.is_insert):
        return
    mapper = state.bind_mapper
    target = mapper.class_ if mapper is not None else None
    if target not in (JournalEntry, JournalLine):
        return
    companies: set[str] = session.info.setdefault(_COMPANIES_KEY, set())
    stmt = state.statement
    if state.is_insert:
        params = state.parameters if isinstance(state.parameters, list) else [state.parameters or {}]
        if target is JournalEntry:
            companies.update(p.get("company_id") for p in params if p.get("company_id"))
        else:
            session.info.setdefault(_JOURNALS_KEY, set()).update(p.get("journal_id") for p in params if p.get("journal_id"))
        return
    where = stmt.whereclause
    if target is JournalEntry:
        query = select(JournalEntry.company_id).distinct()
    else:
        query = select(JournalEntry.company_id).join(JournalLine, JournalLine.journal_id == JournalEntry.id).distinct()
    if where is not None:
        query = query.where(where)
    with session.no_autoflush:
        companies.update(c for c in session.execute(query).scalars() if c)


@event.listens_for(Session, "before_commit")
def _rebuild_after_bulk_statements(session: Session) -> None:
    session.flush()  # unit-of-work changes first (handled per month in after_flush)
    companies: set[str] = session.info.pop(_COMPANIES_KEY, set())
    journals: set[str] = session.info.pop(_JOURNALS_KEY, set())
    if journals:
        companies.update(c for c in session.execute(
            select(JournalEntry.company_id).where(JournalEntry.id.in_(journals)).distinct()).scalars() if c)
    for company_id in companies:
        rebuild_company(session, company_id)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    for key in (_PERIODS_KEY, _COMPANIES_KEY, _JOURNALS_KEY, _RESOLVE_KEY):
        session.info.pop(key, None)


def _main() -> None:
    """python -m app.account_totals [--rebuild] [COMPANY_ID ...]

    Without --rebuild: report companies whose stored totals differ from their journals.
    With --rebuild: recompute them (all companies when none are named)."""
    import sys

    from app.database import SessionLocal

    args = sys.argv[1:]
    rebuild = "--rebuild" in args
    names = [a for a in args if not a.startswith("--")]
    with SessionLocal() as db:
        companies = names or list(db.execute(select(JournalEntry.company_id).distinct()).scalars())
        bad = 0
        for company_id in companies:
            if rebuild:
                rebuild_company(db, company_id)
                db.commit()
            problems = verify_company(db, company_id)
            bad += bool(problems)
            for line in problems[:5]:
                print(line)
        print(f"{len(companies)} companies checked, {bad} out of step" + (" after rebuild" if rebuild else ""))
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    _main()
