from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Account,
    AuditLog,
    GeneralLedgerEntry,
    JournalEntry,
    JournalLine,
    PostingJob,
    SourceTransaction,
    TaxCode,
    TaxLine,
)


MONEY = Decimal("0.01")


class PostingError(Exception):
    pass


def post_source_transaction(db: Session, job: PostingJob, user_id: str | None = None) -> PostingJob:
    transaction = (
        db.query(SourceTransaction)
        .options(joinedload(SourceTransaction.lines))
        .filter(SourceTransaction.id == job.source_id, SourceTransaction.company_id == job.company_id)
        .first()
    )
    if not transaction:
        return fail_job(job, "Source transaction not found")
    if transaction.status not in {"approved", "posted"}:
        return fail_job(job, "Source transaction must be approved before posting")

    existing = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.company_id == job.company_id,
            JournalEntry.source_module == transaction.module,
            JournalEntry.source_id == transaction.id,
        )
        .first()
    )
    if existing:
        job.status = "posted"
        job.error_message = None
        transaction.status = "posted"
        return job

    job.status = "processing"
    try:
        journal = build_journal(db, transaction)
    except PostingError as exc:
        return fail_job(job, str(exc))

    db.add(journal)
    db.flush()
    create_gl_entries_from_journal(db, journal, transaction.reference, transaction.module, transaction.party_name)
    ensure_tax_line(db, transaction)
    transaction.status = "posted"
    job.status = "posted"
    job.error_message = None
    db.add(
        AuditLog(
            company_id=job.company_id,
            user_id=user_id,
            module=transaction.module,
            action="posted_to_ledger",
            record_id=transaction.id,
            detail=journal.entry_number,
        )
    )
    return job


def reverse_journal_entry(db: Session, original: JournalEntry, user_id: str | None = None) -> JournalEntry:
    """Same logic as accounting.py's POST /journal/{id}/reverse endpoint,
    factored out so repost_source_transaction() below can reuse it without
    duplicating the reversal mechanics. Posted journals are never mutated or
    deleted (docs/architecture.md's "corrections use reversal journals"
    rule) — this posts an equal-and-opposite entry instead."""
    reversal = JournalEntry(
        company_id=original.company_id,
        branch_id=original.branch_id,
        entry_number=f"REV-{original.entry_number}",
        source_module="reversal",
        source_id=original.id,
        entry_date=datetime.now(timezone.utc),
        description=f"Reversal of {original.entry_number}: {original.description}",
        status="posted",
    )
    reversal.lines = [
        JournalLine(
            account_id=journal_line.account_id,
            description=f"Reversal: {journal_line.description or ''}",
            debit=money(journal_line.credit),
            credit=money(journal_line.debit),
        )
        for journal_line in original.lines
    ]
    db.add(reversal)
    db.flush()
    create_gl_entries_from_journal(db, reversal, voucher_no=f"REV-{original.entry_number}", voucher_type="reversal")
    db.add(AuditLog(
        company_id=original.company_id,
        user_id=user_id,
        module="accounting",
        action="journal_reversed",
        record_id=original.id,
        detail=reversal.entry_number,
    ))
    return reversal


def repost_source_transaction(db: Session, transaction: SourceTransaction, user_id: str | None = None) -> JournalEntry:
    """Manual re-sync for a source transaction that was edited AFTER it had
    already posted to the ledger (e.g. correcting a purchase's amounts).
    post_source_transaction() above is a no-op once ANY journal exists for a
    source — by design, so a normal re-save never double-posts — but that
    means an edit's new amounts never reach the GL/VAT on their own. This is
    the explicit, user-triggered counterpart: reverses the still-active
    journal for this source (if one exists and isn't already reversed) and
    posts a fresh one from the transaction's current amounts. Also updates
    the transaction's TaxLine in place, since ensure_tax_line() only ever
    creates one and would otherwise leave VAT reporting stale too."""
    # NOT .first() — every repost's fresh journal keeps the SAME
    # (source_module, source_id) as the original (build_journal always
    # stamps it from the transaction, which never changes id), so after one
    # repost there are already two rows matching this filter. Must find
    # every one of them and reverse whichever aren't already reversed,
    # or a second repost could pick the wrong (already-reversed) row via
    # arbitrary row order and silently skip reversing the real active one.
    existing_journals = (
        db.query(JournalEntry)
        .options(joinedload(JournalEntry.lines))
        .filter(
            JournalEntry.company_id == transaction.company_id,
            JournalEntry.source_module == transaction.module,
            JournalEntry.source_id == transaction.id,
        )
        .all()
    )
    if existing_journals:
        reversed_journal_ids = {
            row[0]
            for row in db.query(JournalEntry.source_id)
            .filter(
                JournalEntry.company_id == transaction.company_id,
                JournalEntry.source_module == "reversal",
                JournalEntry.source_id.in_([j.id for j in existing_journals]),
            )
            .all()
        }
        for stale_journal in existing_journals:
            if stale_journal.id not in reversed_journal_ids:
                reverse_journal_entry(db, stale_journal, user_id)

    journal = build_journal(db, transaction)
    db.add(journal)
    db.flush()
    create_gl_entries_from_journal(db, journal, transaction.reference, transaction.module, transaction.party_name)

    tax_line = (
        db.query(TaxLine)
        .filter(TaxLine.company_id == transaction.company_id, TaxLine.source_id == transaction.id)
        .first()
    )
    if tax_line:
        tax_line.taxable_amount = money(transaction.subtotal)
        tax_line.tax_amount = money(transaction.vat)
    else:
        ensure_tax_line(db, transaction)

    transaction.status = "posted"
    db.add(AuditLog(
        company_id=transaction.company_id,
        user_id=user_id,
        module=transaction.module,
        action="reposted_to_ledger",
        record_id=transaction.id,
        detail=journal.entry_number,
    ))
    return journal


def build_journal(db: Session, transaction: SourceTransaction) -> JournalEntry:
    accounts = accounts_by_code(db, transaction.company_id)
    lines: list[JournalLine] = []
    subtotal = money(transaction.subtotal)
    vat = money(transaction.vat)
    total = money(transaction.total)

    if transaction.module in {"sales", "sales_invoice"}:
        require_accounts(accounts, ["1100", "2200"])
        lines.append(line(accounts["1100"], "Customer receivable", debit=total))
        for source_line in transaction.lines:
            source_amount = money(source_line.amount)
            if source_amount:
                account = accounts.get(source_line.account_code)
                if not account:
                    raise PostingError(f"Missing account mapping: {source_line.account_code}")
                lines.append(line(account, source_line.description, credit=source_amount))
        if vat:
            lines.append(line(accounts["2200"], "Output VAT", credit=vat))
    elif transaction.module in {"purchase", "purchase_bill", "expense", "expenses"}:
        require_accounts(accounts, ["2100", "2210"])
        for source_line in transaction.lines:
            source_amount = money(source_line.amount)
            if source_amount:
                account = accounts.get(source_line.account_code)
                if not account:
                    raise PostingError(f"Missing account mapping: {source_line.account_code}")
                lines.append(line(account, source_line.description, debit=source_amount))
        if vat:
            lines.append(line(accounts["2210"], "Input VAT", debit=vat))
        lines.append(line(accounts["2100"], "Supplier payable", credit=total))
    elif transaction.module in {"cogs", "cogs_return"}:
        # Perpetual inventory: a sale moves its cost out of stock (Dr COGS, Cr Inventory); a
        # return moves it back. Source lines carry the COGS account (5000).
        require_accounts(accounts, ["1200"])
        is_return = transaction.module == "cogs_return"
        for source_line in transaction.lines:
            source_amount = money(source_line.amount)
            if source_amount:
                account = accounts.get(source_line.account_code)
                if not account:
                    raise PostingError(f"Missing account mapping: {source_line.account_code}")
                lines.append(line(account, source_line.description, credit=source_amount) if is_return
                             else line(account, source_line.description, debit=source_amount))
        lines.append(line(accounts["1200"], "Inventory", debit=total) if is_return else line(accounts["1200"], "Inventory", credit=total))
    elif transaction.module in {"receipt", "payment"}:
        # Customer receipt: Dr Cash & Bank, Cr Accounts Receivable (clears AR).
        # Supplier payment: Dr Accounts Payable, Cr Cash & Bank (clears AP).
        # No VAT line -- invoice/bill posting already recognized VAT; a
        # receipt/payment only moves cash against a control account.
        is_supplier = transaction.module == "payment"
        require_accounts(accounts, ["1000", "2100" if is_supplier else "1100"])
        cash_bank = accounts["1000"]
        control = accounts["2100"] if is_supplier else accounts["1100"]
        description = f"{'Payment to' if is_supplier else 'Receipt from'} {transaction.party_name or transaction.reference}"
        if is_supplier:
            lines.append(line(control, description, debit=total))
            lines.append(line(cash_bank, description, credit=total))
        else:
            lines.append(line(cash_bank, description, debit=total))
            lines.append(line(control, description, credit=total))
    else:
        raise PostingError(f"Unsupported source module for posting: {transaction.module}")

    debit = sum((journal_line.debit for journal_line in lines), Decimal("0.00"))
    credit = sum((journal_line.credit for journal_line in lines), Decimal("0.00"))
    if money(debit) != money(credit):
        raise PostingError("Generated journal is not balanced")
    if not lines or subtotal < 0 or vat < 0 or total < 0:
        raise PostingError("Source transaction has invalid posting amounts")

    journal = JournalEntry(
        company_id=transaction.company_id,
        branch_id=transaction.branch_id,
        entry_number=f"AUTO-{transaction.reference}",
        source_module=transaction.module,
        source_id=transaction.id,
        description=f"Auto-posted {transaction.module} {transaction.reference}",
    )
    journal.lines = lines
    return journal


def ensure_tax_line(db: Session, transaction: SourceTransaction) -> None:
    # Zero VAT can mean zero-rated (still a real taxable supply, must be
    # reported) rather than "nothing to report" — only skip truly empty lines.
    if not money(transaction.subtotal) or transaction.module in {"cogs", "cogs_return"}:
        return
    exists = (
        db.query(TaxLine)
        .filter(TaxLine.company_id == transaction.company_id, TaxLine.source_id == transaction.id)
        .first()
    )
    if exists:
        return
    # No per-line VAT-treatment selector exists yet, so this is a best-effort
    # inference: zero VAT on a non-zero supply is treated as zero-rated.
    code_name = "ZERO" if not money(transaction.vat) else "VAT5"
    tax_code = db.query(TaxCode).filter(TaxCode.company_id == transaction.company_id, TaxCode.code == code_name).first()
    if not tax_code:
        tax_code = db.query(TaxCode).filter(TaxCode.company_id == transaction.company_id, TaxCode.code == "VAT5").first()
    period = (transaction.created_at or datetime.now(timezone.utc)).strftime("%Y-%m")
    db.add(
        TaxLine(
            company_id=transaction.company_id,
            source_id=transaction.id,
            tax_code_id=tax_code.id if tax_code else None,
            direction="output" if transaction.module in {"sales", "sales_invoice"} else "input",
            taxable_amount=money(transaction.subtotal),
            tax_amount=money(transaction.vat),
            period=period,
        )
    )


def ensure_credit_note_tax_line(
    db: Session,
    company_id: str,
    reference: str,
    taxable_amount: Decimal,
    tax_amount: Decimal,
    period: str,
) -> None:
    """Reverse output VAT for a POS/app-data credit note (negative-signed
    Sales Return) that deliberately skips full ledger posting — see
    sync_domain_model()'s salesInvoices branch in app_data.py. This only
    ever touches the standalone TaxLine table /tax/vat-return sums; it does
    not create a SourceTransaction, JournalEntry, or GeneralLedgerEntry, so
    the "no full ledger posting for returns" decision stays untouched.

    Credit notes have no SourceTransaction.id to key dedup off (TaxLine.
    source_id has no FK constraint, so a synthetic id is safe here), so a
    deterministic uuid5 of (company_id, reference) stands in for it —
    re-saving the same credit note record is then a safe no-op instead of
    double-counting the VAT reversal.
    """
    if not money(taxable_amount):
        return
    synthetic_id = str(uuid5(NAMESPACE_URL, f"credit-note:{company_id}:{reference}"))
    exists = (
        db.query(TaxLine)
        .filter(TaxLine.company_id == company_id, TaxLine.source_id == synthetic_id)
        .first()
    )
    if exists:
        return
    db.add(
        TaxLine(
            company_id=company_id,
            source_id=synthetic_id,
            tax_code_id=None,
            direction="output",
            taxable_amount=money(taxable_amount),
            tax_amount=money(tax_amount),
            period=period,
        )
    )


def create_gl_entries_from_journal(
    db: Session,
    journal: JournalEntry,
    voucher_no: str | None = None,
    voucher_type: str | None = None,
    party: str | None = None,
    cost_center: str | None = None,
) -> None:
    existing = (
        db.query(GeneralLedgerEntry)
        .filter(GeneralLedgerEntry.company_id == journal.company_id, GeneralLedgerEntry.journal_entry_id == journal.id)
        .first()
    )
    if existing:
        return

    distinct_account_ids = {journal_line.account_id for journal_line in journal.lines}

    # One query for every account this journal touches, instead of one
    # query per line (a journal typically touches 2-4 distinct accounts
    # even with 5+ lines — e.g. several sales lines all crediting "3000").
    accounts_by_id = {a.id: a for a in db.query(Account).filter(Account.id.in_(distinct_account_ids)).all()}

    # One aggregate query for the pre-existing balance of every distinct
    # account this journal touches, instead of one per line. Scoped to
    # entries dated on or before this journal's own entry_date (not raw
    # insertion order) and to the same branch — otherwise a backdated
    # journal's stored "balance" snapshot would include chronologically-
    # later entries, and two branches sharing an account would have their
    # running balances mixed together. This is still only a point-in-time
    # snapshot: entries already posted after this one's date are not
    # retroactively recalculated when a backdated entry lands between them
    # (documented limitation, not fixed here — would require rewriting
    # every later entry's balance).
    #
    # Safe to compute once instead of per line: none of THIS journal's own
    # lines exist in the DB yet at this point (they're what this loop is
    # about to insert), so this snapshot is exactly what the old per-line
    # queries would each have produced via SQLAlchemy's autoflush (a query
    # mid-loop saw any already-db.add()-ed rows from earlier in the SAME
    # loop) — the running_by_account accumulator below reproduces that
    # same same-account-multiple-lines chaining in memory instead.
    prior_query = db.query(
        GeneralLedgerEntry.account_id,
        func.coalesce(func.sum(GeneralLedgerEntry.debit - GeneralLedgerEntry.credit), Decimal("0.00")),
    ).filter(
        GeneralLedgerEntry.account_id.in_(distinct_account_ids),
        GeneralLedgerEntry.company_id == journal.company_id,
        GeneralLedgerEntry.entry_date <= journal.entry_date,
    )
    if journal.branch_id:
        prior_query = prior_query.filter(
            (GeneralLedgerEntry.branch_id == journal.branch_id) | (GeneralLedgerEntry.branch_id.is_(None))
        )
    prior_by_account = dict(prior_query.group_by(GeneralLedgerEntry.account_id).all())

    running_by_account: dict[str, Decimal] = {}
    for journal_line in journal.lines:
        account_id = journal_line.account_id
        if account_id not in running_by_account:
            account = accounts_by_id.get(account_id)
            ob = money(account.opening_balance if account else 0)
            ob_type = (account.opening_balance_type or "DR") if account else "DR"
            running_by_account[account_id] = (ob if ob_type == "DR" else -ob) + money(prior_by_account.get(account_id, 0) or 0)
        line_net = money(journal_line.debit) - money(journal_line.credit)
        running_by_account[account_id] += line_net
        db.add(
            GeneralLedgerEntry(
                company_id=journal.company_id,
                branch_id=journal.branch_id,
                entry_date=journal.entry_date,
                voucher_no=voucher_no or journal.entry_number,
                voucher_type=voucher_type or journal.source_module,
                account_id=account_id,
                journal_entry_id=journal.id,
                journal_line_id=journal_line.id,
                debit=money(journal_line.debit),
                credit=money(journal_line.credit),
                balance=running_by_account[account_id],
                party=party,
                cost_center=cost_center,
                narration=journal_line.description or journal.description,
            )
        )


def accounts_by_code(db: Session, company_id: str) -> dict[str, Account]:
    rows = db.query(Account).filter(Account.company_id == company_id, Account.is_active.is_(True)).all()
    return {account.code: account for account in rows}


def require_accounts(accounts: dict[str, Account], codes: list[str]) -> None:
    missing = [code for code in codes if code not in accounts]
    if missing:
        raise PostingError(f"Missing control account mappings: {', '.join(missing)}")


def line(account: Account, description: str | None, debit: Decimal = Decimal("0.00"), credit: Decimal = Decimal("0.00")) -> JournalLine:
    return JournalLine(account_id=account.id, description=description, debit=money(debit), credit=money(credit))


def money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY)


def fail_job(job: PostingJob, message: str) -> PostingJob:
    job.status = "failed"
    job.retry_count = (job.retry_count or 0) + 1
    job.error_message = message
    return job
