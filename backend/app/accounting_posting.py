from decimal import Decimal

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
        entry_number=f"AUTO-{transaction.reference}",
        source_module=transaction.module,
        source_id=transaction.id,
        description=f"Auto-posted {transaction.module} {transaction.reference}",
    )
    journal.lines = lines
    return journal


def ensure_tax_line(db: Session, transaction: SourceTransaction) -> None:
    if not money(transaction.vat):
        return
    exists = (
        db.query(TaxLine)
        .filter(TaxLine.company_id == transaction.company_id, TaxLine.source_id == transaction.id)
        .first()
    )
    if exists:
        return
    tax_code = db.query(TaxCode).filter(TaxCode.company_id == transaction.company_id, TaxCode.code == "VAT5").first()
    db.add(
        TaxLine(
            company_id=transaction.company_id,
            source_id=transaction.id,
            tax_code_id=tax_code.id if tax_code else None,
            direction="output" if transaction.module in {"sales", "sales_invoice"} else "input",
            taxable_amount=money(transaction.subtotal),
            tax_amount=money(transaction.vat),
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
    for journal_line in journal.lines:
        db.add(
            GeneralLedgerEntry(
                company_id=journal.company_id,
                entry_date=journal.entry_date,
                voucher_no=voucher_no or journal.entry_number,
                voucher_type=voucher_type or journal.source_module,
                account_id=journal_line.account_id,
                journal_entry_id=journal.id,
                journal_line_id=journal_line.id,
                debit=money(journal_line.debit),
                credit=money(journal_line.credit),
                balance=money(journal_line.debit) - money(journal_line.credit),
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
