import json
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uuid() -> str:
    return str(uuid4())


class InvoiceStatus(str, Enum):
    draft = "draft"
    issued = "issued"
    paid = "paid"
    cancelled = "cancelled"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    trn: Mapped[str | None] = mapped_column(String(32), unique=True)
    country: Mapped[str] = mapped_column(String(80), default="United Arab Emirates")
    currency: Mapped[str] = mapped_column(String(3), default="AED")
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"))
    # "with_stock" (default) or "without_stock": a no-stock company (services, trading without
    # inventory) never moves stock on purchases, sales or POS. Set by Super Admin.
    stock_mode: Mapped[str] = mapped_column(String(20), default="with_stock")
    # "perpetual": stock purchases post to 1200 Inventory and each sale posts its cost
    # (Dr 5000 COGS / Cr 1200). "periodic": purchases expense to 4000 (the original behaviour,
    # kept for companies that existed before this setting -- see the migration default).
    inventory_accounting: Mapped[str] = mapped_column(String(20), default="perpetual")
    emirate: Mapped[str | None] = mapped_column(String(80), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    business_activity: Mapped[str | None] = mapped_column(String(160), nullable=True)
    legal_structure: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trade_license_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trade_license_issue_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trade_license_expiry: Mapped[str | None] = mapped_column(String(20), nullable=True)
    free_zone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    po_box: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(String(160), nullable=True)
    subscription_expires_at: Mapped[str | None] = mapped_column(String(20), nullable=True)
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    fta_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    departments: Mapped[str | None] = mapped_column(Text, nullable=True)
    branches: Mapped[str | None] = mapped_column(Text, nullable=True)
    modules_enabled: Mapped[str | None] = mapped_column(Text, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="company")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="company")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_company_last_login", "company_id", "last_login"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set on every successful password login (routers/auth.py). NULL = never recorded
    # (accounts that last signed in before this column existed) -- Super Admin's
    # "inactive companies" view only counts a company once a real login is on record.
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    company: Mapped[Company] = relationship(back_populates="users")


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "invoice_number", name="uq_invoice_company_number"),
        # Backs the repeated status filters/GROUP BYs in reports.py's
        # dashboard/summary/invoice_status queries.
        Index("ix_invoices_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    customer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.draft.value)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    company: Mapped[Company] = relationship(back_populates="invoices")
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        # list_invoices() (invoices.py) joinedload()s this relationship for
        # every invoice returned -- with no index here, that join required a
        # full scan of the entire invoice_lines table (1.5M+ rows across all
        # tenants at real scale) for every single request, not just the
        # requesting company's own rows. Found via live testing: a 10-row
        # result took 5+ seconds without this index, ~0.1s with it.
        Index("ix_invoice_lines_invoice_id", "invoice_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=5)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value)
    result: Mapped[str | None] = mapped_column(Text)


class ImpersonationSession(Base, TimestampMixin):
    """Tracks superadmin "log in as this user" sessions so they're visible
    and force-endable from the superadmin dashboard, not just discoverable
    after the fact by reading raw audit log rows. created_at doubles as
    started_at; ended_at is set by /end-impersonation or a forced end."""
    __tablename__ = "impersonation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    superadmin_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Exactly one of target_user_id/target_branch_id is set, depending on
    # whether the superadmin impersonated a company admin user (original
    # feature) or a Branch Login identity (see impersonate_company(),
    # superadmin.py). Both nullable rather than a discriminator column,
    # since the FK itself already tells you which kind a row is.
    target_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    target_branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    token_jti: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_account_company_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    # Hierarchy: 1=MAIN_LEDGER, 2-4=SUB_LEDGER, 2-5=POSTING_LEDGER (max depth 5)
    level: Mapped[int] = mapped_column(Integer, default=5)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    node_type: Mapped[str] = mapped_column(String(30), default="POSTING_LEDGER")
    normal_balance: Mapped[str] = mapped_column(String(2), default="DR")
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    opening_balance_type: Mapped[str] = mapped_column(String(2), default="DR")
    currency: Mapped[str] = mapped_column(String(10), default="AED")
    tax_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bank_cash: Mapped[bool] = mapped_column(Boolean, default=False)
    is_control_account: Mapped[bool] = mapped_column(Boolean, default=False)
    created_mode: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="active")
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ClientError(Base):
    __tablename__ = "client_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page: Mapped[str | None] = mapped_column(String(80), nullable=True)
    viewport: Mapped[str | None] = mapped_column(String(20), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalEntry(Base, TimestampMixin):
    __tablename__ = "journal_entries"
    __table_args__ = (
        # Backs _posted_journal_line_totals()'s status == "posted" filter
        # (trial balance / balance sheet, run on nearly every report call)
        # and date-range queries against entry_date.
        Index("ix_journal_entries_company_status", "company_id", "status"),
        Index("ix_journal_entries_company_date", "company_id", "entry_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    entry_number: Mapped[str] = mapped_column(String(40), nullable=False)
    source_module: Mapped[str] = mapped_column(String(60), default="manual")
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="posted")

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="journal", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    journal_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id"), index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    journal: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()


class VoucherType(Base, TimestampMixin):
    __tablename__ = "voucher_types"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_voucher_type_company_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    auto_numbering: Mapped[bool] = mapped_column(Boolean, default=True)
    default_debit_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    default_credit_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    affects_cash_bank: Mapped[bool] = mapped_column(Boolean, default=False)
    affects_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="active")


class Voucher(Base, TimestampMixin):
    __tablename__ = "vouchers"
    __table_args__ = (UniqueConstraint("company_id", "voucher_no", name="uq_voucher_company_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    voucher_type_id: Mapped[str] = mapped_column(ForeignKey("voucher_types.id"), nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(60), nullable=False)
    voucher_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    party: Mapped[str | None] = mapped_column(String(160))
    cost_center: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_journal_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id"))
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))

    voucher_type: Mapped[VoucherType] = relationship()
    lines: Mapped[list["VoucherLine"]] = relationship(back_populates="voucher", cascade="all, delete-orphan")


class VoucherLine(Base):
    __tablename__ = "voucher_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    voucher_id: Mapped[str] = mapped_column(ForeignKey("vouchers.id"), index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    party: Mapped[str | None] = mapped_column(String(160))
    cost_center: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))

    voucher: Mapped[Voucher] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()


class AccountPeriodTotal(Base):
    """Posted journal-line totals per company, branch, account and month, maintained by
    app/account_totals.py so reports read a few rows instead of every journal line."""
    __tablename__ = "account_period_totals"
    __table_args__ = (
        Index("ix_account_period_totals_company_period", "company_id", "period"),
        Index("ix_account_period_totals_company_account", "company_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM (UTC), 0000-00 = no date
    debit: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)


class GeneralLedgerEntry(Base, TimestampMixin):
    __tablename__ = "general_ledger_entries"
    __table_args__ = (
        # Backs create_gl_entries_from_journal()'s running-balance aggregate
        # (accounting_posting.py) — filtered on exactly this triple, with
        # only single-column indexes to work with before.
        Index("ix_gl_entries_company_account_date", "company_id", "account_id", "entry_date"),
        # Reversing/deleting a posting removes its GL rows by journal entry.
        Index("ix_gl_entries_journal_entry_id", "journal_entry_id"),
        # Deleting journal lines makes PostgreSQL check this foreign key per line; unindexed
        # it scanned the whole GL (6.4 s to delete one invoice's lines at 1.8M rows).
        Index("ix_gl_entries_journal_line_id", "journal_line_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    voucher_no: Mapped[str] = mapped_column(String(60), nullable=False)
    voucher_type: Mapped[str] = mapped_column(String(80), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id"))
    journal_line_id: Mapped[str | None] = mapped_column(ForeignKey("journal_lines.id"))
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    party: Mapped[str | None] = mapped_column(String(160))
    cost_center: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))

    account: Mapped[Account] = relationship()


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("company_id", "payment_no", name="uq_payment_company_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    payment_no: Mapped[str] = mapped_column(String(60), nullable=False)
    payment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payment_mode: Mapped[str] = mapped_column(String(40), default="bank")
    cash_bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    debit_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    payee_type: Mapped[str | None] = mapped_column(String(80))
    payee_name: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reference_no: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))
    attachment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))


class Receipt(Base, TimestampMixin):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("company_id", "receipt_no", name="uq_receipt_company_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    receipt_no: Mapped[str] = mapped_column(String(60), nullable=False)
    receipt_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    receipt_mode: Mapped[str] = mapped_column(String(40), default="bank")
    cash_bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    received_from: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reference_no: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))
    attachment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))


class SourceTransaction(Base, TimestampMixin):
    __tablename__ = "source_transactions"
    __table_args__ = (
        # Backs the module.in_([...]) filters (_purchase_summary,
        # monthly_revenue_vat, reports.py's expense query) and status
        # filters used throughout the reports/posting flow.
        Index("ix_source_tx_company_module", "company_id", "module"),
        Index("ix_source_tx_company_status", "company_id", "status"),
        # upsert_source_transaction() finds the posting for a saved document by reference.
        Index("ix_source_tx_company_module_ref", "company_id", "module", "reference"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    party_name: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_result: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["SourceTransactionLine"]] = relationship(back_populates="transaction", cascade="all, delete-orphan")


class SourceTransactionLine(Base):
    __tablename__ = "source_transaction_lines"
    __table_args__ = (
        # Every re-save of an invoice/purchase replaces its lines by source_id; without
        # this the PostgreSQL load test showed a 1M-row scan (~700 ms) per save.
        Index("ix_source_tx_lines_source_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_transactions.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=5)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    transaction: Mapped[SourceTransaction] = relationship(back_populates="lines")


class PostingJob(Base, TimestampMixin):
    __tablename__ = "posting_jobs"
    __table_args__ = (Index("ix_posting_jobs_source_id", "source_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_transactions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class TaxCode(Base, TimestampMixin):
    __tablename__ = "tax_codes"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_tax_code_company_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=5)
    recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    reporting_box: Mapped[str | None] = mapped_column(String(20))


class TaxLine(Base, TimestampMixin):
    __tablename__ = "tax_lines"
    __table_args__ = (
        # Backs dashboard()'s and tax_line_breakdown()'s repeated
        # company_id + direction filters (reports.py) — previously only a
        # single-column company_id index existed for this table.
        Index("ix_tax_lines_company_direction", "company_id", "direction"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tax_code_id: Mapped[str | None] = mapped_column(ForeignKey("tax_codes.id"))
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    period: Mapped[str] = mapped_column(String(20), default="2024-06")

    tax_code: Mapped[TaxCode | None] = relationship()


class TaxPeriod(Base, TimestampMixin):
    __tablename__ = "tax_periods"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="open")


class VatReturn(Base, TimestampMixin):
    __tablename__ = "vat_returns"
    __table_args__ = (UniqueConstraint("company_id", "period", name="uq_vat_return_company_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    sales_taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    purchase_taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    input_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    adjustments: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    filing_status: Mapped[str] = mapped_column(String(30), default="draft")
    filed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fta_reference_no: Mapped[str | None] = mapped_column(String(80))
    attachment: Mapped[str | None] = mapped_column(String(500))
    payment_voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))


class CorporateTaxReturn(Base, TimestampMixin):
    __tablename__ = "corporate_tax_returns"
    __table_args__ = (UniqueConstraint("company_id", "tax_period", name="uq_corp_tax_return_company_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    tax_period: Mapped[str] = mapped_column(String(20), nullable=False)
    accounting_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    non_deductible_expenses: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    exempt_income: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_loss_adjustment: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    taxable_income: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=9)
    corporate_tax_payable: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    filing_status: Mapped[str] = mapped_column(String(30), default="draft")
    filed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_no: Mapped[str | None] = mapped_column(String(80))
    attachment: Mapped[str | None] = mapped_column(String(500))
    provision_voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))
    payment_voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"))


class BankAccount(Base, TimestampMixin):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("company_id", "account_id", name="uq_bank_account_company_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(160), nullable=False)
    iban: Mapped[str | None] = mapped_column(String(40))
    account_number: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str] = mapped_column(String(10), default="AED")
    status: Mapped[str] = mapped_column(String(30), default="active")

    account: Mapped[Account] = relationship()


class BankStatementLine(Base, TimestampMixin):
    __tablename__ = "bank_statement_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    bank_account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"), nullable=False)
    statement_date: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_date: Mapped[str] = mapped_column(String(20), nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(80))
    cheque_no: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))
    party_name: Mapped[str | None] = mapped_column(String(160))
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="unmatched")

    bank_account: Mapped[BankAccount] = relationship()


class BankReconciliationMatch(Base, TimestampMixin):
    __tablename__ = "bank_reconciliation_matches"
    # Re-posting deletes GL rows, which checks this foreign key.
    __table_args__ = (Index("ix_bank_recon_matches_ledger_entry_id", "ledger_entry_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    bank_account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"), nullable=False)
    statement_line_id: Mapped[str | None] = mapped_column(ForeignKey("bank_statement_lines.id"))
    ledger_entry_id: Mapped[str | None] = mapped_column(ForeignKey("general_ledger_entries.id"))
    match_status: Mapped[str] = mapped_column(String(30), default="matched")
    match_method: Mapped[str] = mapped_column(String(40), default="manual")
    difference: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PeriodLock(Base, TimestampMixin):
    __tablename__ = "period_locks"
    __table_args__ = (UniqueConstraint("company_id", "module", "period", name="uq_period_lock_company_module_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    locked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(255))


class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str | None] = mapped_column(String(160))


class StockProductMapping(Base, TimestampMixin):
    __tablename__ = "stock_product_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(String(160))
    taxflow_name: Mapped[str | None] = mapped_column(String(160))
    sales_account_code: Mapped[str] = mapped_column(String(20), default="3000")
    purchase_account_code: Mapped[str] = mapped_column(String(20), default="4000")
    inventory_account_code: Mapped[str] = mapped_column(String(20), default="1200")
    tax_code: Mapped[str] = mapped_column(String(30), default="VAT5")
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    units_per_outer: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=1)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=5)
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    inc_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    price_outer: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # False for a row silently auto-created from purchase-line OCR text
    # (stock_mapping_for_purchase_line / purchase_line_stock_mapping) — the
    # frontend must show these as "Needs Review", never "Mapped", until a
    # user actually opens and saves the mapping. Only the explicit save path
    # (PUT /inventory/mappings/{id} / POST /inventory/mappings) sets this True.
    mapping_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # "Yes"/"No"/"Optional" from the Item Master's Stock Tracking field.
    # "No"/"Optional" both mean: the item still appears in Stock Levels, but
    # purchases/POS sales don't create StockMovement rows or deduct
    # quantity for it — see sync_purchase_stock()/sync_pos_stock().
    tracking: Mapped[str] = mapped_column(String(20), default="Yes")


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    mapping_id: Mapped[str] = mapped_column(ForeignKey("stock_product_mappings.id"), nullable=False)
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    movement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reference: Mapped[str | None] = mapped_column(String(80))


class ItemUnit(Base, TimestampMixin):
    __tablename__ = "item_units"
    __table_args__ = (UniqueConstraint("company_id", "item_code", "unit_code", name="uq_item_unit_company_item_unit"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    item_code: Mapped[str] = mapped_column(String(60), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(80), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=1)
    is_base_unit: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sales_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="active")


class ItemUnitConversion(Base, TimestampMixin):
    __tablename__ = "item_unit_conversions"
    __table_args__ = (
        UniqueConstraint("company_id", "item_code", "from_unit_code", "to_unit_code", name="uq_item_unit_conversion"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    item_code: Mapped[str] = mapped_column(String(60), nullable=False)
    from_unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    to_unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=1)
    status: Mapped[str] = mapped_column(String(30), default="active")


class InventoryValuationLayer(Base, TimestampMixin):
    __tablename__ = "inventory_valuation_layers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    item_code: Mapped[str] = mapped_column(String(60), nullable=False)
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    source_module: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    quantity_in: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    valuation_method: Mapped[str] = mapped_column(String(30), default="FIFO")


class StockAdjustmentApproval(Base, TimestampMixin):
    __tablename__ = "stock_adjustment_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    item_code: Mapped[str] = mapped_column(String(60), nullable=False)
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_no: Mapped[str] = mapped_column(String(40), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    department: Mapped[str] = mapped_column(String(80), default="Operations")
    designation: Mapped[str] = mapped_column(String(80), default="Staff")
    basic_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # Payroll previously hardcoded "allowances = 0.00" with nowhere for a
    # real figure to come from at all — these three columns are the actual
    # source generate_payroll() (payroll.py) now sums into that field.
    housing_allowance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    transport_allowance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    other_allowance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    iban: Mapped[str | None] = mapped_column(String(40))
    wps_id: Mapped[str | None] = mapped_column(String(40))
    # Compressed base64 data URL, mirrored from the "employees" AppDataRecord
    # on save (see app_data.py's sync_domain_model) -- lets ESS and other
    # SQL-backed readers show the same photo set in HRMS Edit Employee
    # without depending on the JSON blob collection.
    photo: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Access control / GPS attendance — see docs/hrms-architecture.md §6
    username: Mapped[str | None] = mapped_column(String(80))
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.id"))
    work_location_id: Mapped[str | None] = mapped_column(ForeignKey("company_locations.id"))
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayrollRun(Base, TimestampMixin):
    __tablename__ = "payroll_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    gross_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    deductions_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    items: Mapped[list["PayrollItem"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PayrollItem(Base):
    __tablename__ = "payroll_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("payroll_runs.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    basic: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    allowances: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    overtime: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    deductions: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    wps_status: Mapped[str] = mapped_column(String(30), default="ready")

    run: Mapped[PayrollRun] = relationship(back_populates="items")
    employee: Mapped[Employee] = relationship()


class WpsBatch(Base, TimestampMixin):
    __tablename__ = "wps_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    payroll_run_id: Mapped[str] = mapped_column(ForeignKey("payroll_runs.id"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending_validation")
    sif_content: Mapped[str | None] = mapped_column(Text)


class AppDataRecord(Base, TimestampMixin):
    __tablename__ = "app_data_records"
    __table_args__ = (
        Index("ix_app_data_company_collection", "company_id", "collection"),
        Index("ix_app_data_company_collection_created", "company_id", "collection", "created_at"),
        # Covers the save/delete lookup: WHERE company_id=? AND collection=? AND record_key=?
        Index("ix_app_data_company_collection_key", "company_id", "collection", "record_key"),
        Index("ix_app_data_company_collection_date", "company_id", "collection", "record_date"),
        Index("ix_app_data_company_collection_status", "company_id", "collection", "doc_status"),
        Index("ix_app_data_company_collection_party", "company_id", "collection", "party"),
        Index("ix_app_data_company_collection_fig_ref", "company_id", "collection", "fig_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    collection: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    record_key: Mapped[str | None] = mapped_column(String(160), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    # Payload's "date" (YYYY-MM-DD) for DATED_COLLECTIONS, so date-window reads can filter in SQL.
    record_date: Mapped[str | None] = mapped_column(String(10))
    # Summary columns for INDEXED_DOC_COLLECTIONS (app/doc_index.py), so paged lists, search
    # and totals run in SQL instead of parsing every payload. Stamped on every write;
    # amount_paid is kept current from the payments collection instead.
    party: Mapped[str | None] = mapped_column(String(160))
    doc_status: Mapped[str | None] = mapped_column(String(40))
    doc_kind: Mapped[str | None] = mapped_column(String(20))
    salesperson: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Report figures for sales invoices, purchases, bills and expenses (doc_index.doc_figures()),
    # so the dashboard, summary, VAT and branch figures are SQL sums.
    fig_status: Mapped[str | None] = mapped_column(String(40))
    fig_ref: Mapped[str | None] = mapped_column(String(160))
    fig_gross: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fig_net: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fig_vat: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fig_taxable: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fig_paid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class DocumentLine(Base):
    """One line item of a document kept as JSON app-data (sales invoices first), as real
    columns: maintained by app/doc_lines.py on every write, so line-level reads (stock
    movements, "invoices with this product") run in SQL instead of decoding every
    record. The JSON record stays the source of truth; these rows are derived from it.
    Always read joined to app_data_records, so a row whose record is gone never shows."""
    __tablename__ = "document_lines"
    __table_args__ = (
        Index("ix_document_lines_record", "record_id"),
        Index("ix_document_lines_company_collection", "company_id", "collection"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    collection: Mapped[str] = mapped_column(String(80), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # From the document: its "date" as saved, its number, its "source" (lower-cased).
    doc_date: Mapped[str | None] = mapped_column(String(40))
    doc_ref: Mapped[str | None] = mapped_column(String(160))
    doc_source: Mapped[str | None] = mapped_column(String(40))
    # The line: description (else product) as shown, and lower-cased match keys.
    item_name: Mapped[str | None] = mapped_column(String(255))
    description_key: Mapped[str | None] = mapped_column(String(255))
    product_name_key: Mapped[str | None] = mapped_column(String(255))
    product_key: Mapped[str | None] = mapped_column(String(255))
    product_code: Mapped[str | None] = mapped_column(String(80))
    unit: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


DATED_COLLECTIONS = frozenset({"rotaAssignments"})
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def payload_record_date(payload: str | None) -> str | None:
    try:
        day = str(json.loads(payload or "{}").get("date") or "")[:10]
    except (TypeError, ValueError, AttributeError):
        return None
    return day if _ISO_DAY.match(day) else None


@event.listens_for(AppDataRecord, "before_insert")
@event.listens_for(AppDataRecord, "before_update")
def _stamp_record_date(_mapper, _connection, target: AppDataRecord) -> None:
    if target.collection in DATED_COLLECTIONS:
        target.record_date = payload_record_date(target.payload)
    else:
        from app.doc_index import INDEXED_DOC_COLLECTIONS, stamp_doc_columns

        if target.collection in INDEXED_DOC_COLLECTIONS:
            stamp_doc_columns(target)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # Backs recent_activity()'s ORDER BY created_at DESC LIMIT, filtered
        # by company_id — audit_logs only ever grows, so this keeps that
        # query cheap as it does.
        Index("ix_audit_logs_company_created", "company_id", "created_at"),
        # Deleting an employee checks this foreign key across the whole audit log.
        Index("ix_audit_logs_employee_id", "employee_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    # Separate FK for an Employee-principal actor (HRMS sub-user, e.g. a
    # branch login writing POS/purchase data — Branch Management Phase 4) —
    # kept distinct from user_id (-> users.id) rather than relaxing that FK,
    # since exactly one of the two is ever set depending on who acted.
    # Mirrors LeaveRequest.approved_by/approved_by_employee_id.
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    # Branch Login Phase 2 — same reasoning as employee_id above, for a
    # Branch-identity actor (neither a User nor an Employee). Exactly one of
    # user_id/employee_id/branch_actor_id is ever set per row.
    branch_actor_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"))
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    record_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(Text)


class AuditLogDetail(Base, TimestampMixin):
    __tablename__ = "audit_log_details"
    __table_args__ = (Index("ix_audit_log_details_audit_log_id", "audit_log_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    audit_log_id: Mapped[str | None] = mapped_column(ForeignKey("audit_logs.id"))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(80))
    device: Mapped[str | None] = mapped_column(String(160))
    correlation_id: Mapped[str | None] = mapped_column(String(80), index=True)


class ExceptionEvent(Base, TimestampMixin):
    __tablename__ = "exception_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    source_record: Mapped[str | None] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class DomainEvent(Base, TimestampMixin):
    __tablename__ = "domain_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    event_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_module: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(80), index=True)


class EventOutbox(Base, TimestampMixin):
    __tablename__ = "event_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("domain_events.id"))
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class EventProcessingLog(Base, TimestampMixin):
    __tablename__ = "event_processing_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("domain_events.id"))
    consumer: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="processed")
    detail: Mapped[str | None] = mapped_column(Text)


class DailyGlBalance(Base, TimestampMixin):
    __tablename__ = "daily_gl_balances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    balance_date: Mapped[str] = mapped_column(String(20), nullable=False)
    account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class InventoryBalanceSnapshot(Base, TimestampMixin):
    __tablename__ = "inventory_balance_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    snapshot_date: Mapped[str] = mapped_column(String(20), nullable=False)
    item_code: Mapped[str] = mapped_column(String(60), nullable=False)
    warehouse_name: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    inventory_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class CustomerAgingSnapshot(Base, TimestampMixin):
    __tablename__ = "customer_aging_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    snapshot_date: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    bucket_current: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    bucket_30: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    bucket_60: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    bucket_90: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class VatReturnSnapshot(Base, TimestampMixin):
    __tablename__ = "vat_return_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    input_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_vat_payable: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    source_version: Mapped[str | None] = mapped_column(String(80))


class CorporateTaxRecord(Base, TimestampMixin):
    __tablename__ = "corporate_tax_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    accounting_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_adjustments: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    taxable_income: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class FixedAssetRecord(Base, TimestampMixin):
    __tablename__ = "fixed_asset_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    asset_code: Mapped[str] = mapped_column(String(60), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    purchase_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    accumulated_depreciation: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    method: Mapped[str] = mapped_column(String(40), default="Straight Line")
    location: Mapped[str | None] = mapped_column(String(120))
    custodian: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="active")


class AccrualPrepaymentRecord(Base, TimestampMixin):
    __tablename__ = "accrual_prepayment_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reversal_day: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="active")


class CostCenterRecord(Base, TimestampMixin):
    __tablename__ = "cost_center_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    department: Mapped[str | None] = mapped_column(String(80))
    branch: Mapped[str | None] = mapped_column(String(80))
    project: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="active")


class BudgetRecord(Base, TimestampMixin):
    __tablename__ = "budget_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_center: Mapped[str | None] = mapped_column(String(80))
    account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    annual_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    approval_status: Mapped[str] = mapped_column(String(30), default="draft")


class CashFlowForecastRecord(Base, TimestampMixin):
    __tablename__ = "cash_flow_forecast_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    forecast_date: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_receipts: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    expected_payments: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_cash_flow: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    method: Mapped[str] = mapped_column(String(30), default="direct")


class CreditControlRecord(Base, TimestampMixin):
    __tablename__ = "credit_control_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    outstanding_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit_status: Mapped[str] = mapped_column(String(30), default="active")
    promise_to_pay: Mapped[str | None] = mapped_column(String(20))
    bad_debt_provision: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class MonthEndCloseRecord(Base, TimestampMixin):
    __tablename__ = "month_end_close_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    checklist_item: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    owner: Mapped[str | None] = mapped_column(String(120))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class ConsolidationRecord(Base, TimestampMixin):
    __tablename__ = "consolidation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    group_name: Mapped[str] = mapped_column(String(160), nullable=False)
    subsidiary_name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="AED")
    translated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    elimination_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class BiometricDevice(Base, TimestampMixin):
    __tablename__ = "biometric_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    device_type: Mapped[str] = mapped_column(String(40), default="ZKTeco")
    ip_address: Mapped[str | None] = mapped_column(String(60))
    port: Mapped[int] = mapped_column(Integer, default=4370)
    location: Mapped[str | None] = mapped_column(String(120))
    api_key_hash: Mapped[str | None] = mapped_column(String(255))
    # ZKTeco ADMS Classic only (device_type == "ZKTeco ADMS Classic") — real
    # ADMS Cloud Server Mode identifies a device by its own hardware serial
    # number (see iclock_router, attendance.py), not a bearer key, since the
    # device's own menu has no field to paste one into. NULL for every other
    # device type. No hard uniqueness constraint — add_device() already
    # checks for a duplicate serial at the app layer before creating one.
    serial_number: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # BioTime server connection (device_type == "ZKTeco BioTime Server") — a
    # pull integration against the customer's own BioTime install, not a
    # single physical terminal. Credentials are per-company, never shared.
    biotime_base_url: Mapped[str | None] = mapped_column(String(255))
    biotime_username: Mapped[str | None] = mapped_column(String(120))
    biotime_password_enc: Mapped[str | None] = mapped_column(Text)
    biotime_token: Mapped[str | None] = mapped_column(Text)
    biotime_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendanceDetail(Base, TimestampMixin):
    """One row per (company, employee, calendar day) — the sole attendance-
    event table (its one-row-per-scan-event predecessor, AttendancePunch,
    was retired once this table had run as the sole source for every live
    endpoint without issue; see git history for that table's schema).
    `raw_events` retains every individual scan as a JSON list so per-event
    listing/delete (Sync Activity Log) and real in/out pairing (employee-
    daily drill-down) remain possible on top of the aggregate columns."""
    __tablename__ = "attendance_details"
    __table_args__ = (
        Index("ix_att_details_company_date", "company_id", "work_date"),
        UniqueConstraint("company_id", "employee_id", "work_date", name="uq_attendance_details_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(255))
    work_date: Mapped[str] = mapped_column(String(10), nullable=False)

    clock_in_1: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_out_1: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_seconds_1: Mapped[int | None] = mapped_column(Integer)
    clock_in_2: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_out_2: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_seconds_2: Mapped[int | None] = mapped_column(Integer)
    clock_in_3: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_out_3: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_seconds_3: Mapped[int | None] = mapped_column(Integer)
    clock_in_4: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_out_4: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_seconds_4: Mapped[int | None] = mapped_column(Integer)
    clock_in_5: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_out_5: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_seconds_5: Mapped[int | None] = mapped_column(Integer)

    total_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ot_seconds: Mapped[int] = mapped_column(Integer, default=0)
    under_seconds: Mapped[int] = mapped_column(Integer, default=0)
    session_count: Mapped[int] = mapped_column(Integer, default=0)

    # JSON list of every individual scan for this day: {id, punch_time (UTC
    # ISO), direction, device_id, device_name, source, employee_name}.
    raw_events: Mapped[str] = mapped_column(Text, default="[]")


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    role_name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    is_system_role: Mapped[bool] = mapped_column(Boolean, default=False)
    # JSON-encoded list of department names. Non-empty means an employee
    # holding this role (once granted ESS Portal Access) can see every
    # employee in these departments -- see GET /ess/team.
    department_scope: Mapped[str | None] = mapped_column(Text)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    permission_name: Mapped[str] = mapped_column(String(80), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class Branch(Base, TimestampMixin):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(400))
    # Reference-only fields, same GCC+UK list/derivation as the company
    # signup and superadmin "New Company" country pickers — a branch may
    # legitimately operate in a different country than its parent Company
    # (e.g. a UAE company with a Saudi branch). Deliberately doesn't affect
    # how any transaction/invoice/VAT actually gets computed today — those
    # still use the parent Company's own currency/vat_rate, unchanged; this
    # is just what the branch is labeled/reported as in the UI.
    country: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str | None] = mapped_column(String(10))
    # "Active"/"Inactive" (capitalized) — matches the pre-existing Settings >
    # Departments & Branches UI convention this replaces the backing store
    # for; keep in sync with frontend/public/taxflow/src/app.js's branch
    # modal rather than the lowercase convention used elsewhere (e.g.
    # CompanyLocation.status) to avoid a cosmetic mismatch after migration.
    status: Mapped[str] = mapped_column(String(30), default="Active")
    # Branch Login Phase 1: JSON array of module keys, same NULL/empty=
    # unrestricted convention as Company.modules_enabled (see
    # dependencies.py::company_allows_module) — every pre-existing branch
    # keeps today's unrestricted behavior on deploy day; only becomes an
    # explicit (possibly restrictive) array once a company admin saves the
    # branch modal's module grid.
    modules_enabled: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Branch Login Phase 2 — the branch entity's own shared login, separate
    # from any Employee account. Mirrors Employee's portal-access columns
    # exactly, except password_hash has NO "unset means password=employee_no"
    # fallback (Employee.password_hash's own convention) — there's no
    # equivalent obvious default for a Branch, so a real password must
    # always be set explicitly before this login works at all. username is
    # globally unique (enforced by a partial unique index in main.py's
    # migration block, mirroring uq_employees_username) so the shared
    # /login page can resolve it without a company selector, same as
    # Employee.username already does for /hr/login.
    username: Mapped[str | None] = mapped_column(String(80))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmployeeBranchAccess(Base, TimestampMixin):
    """Branch Security Layer Phase 3: additional branches an employee may
    switch into, on top of their primary Employee.branch_id (unchanged,
    still their default/home branch — used for POS receipts, HR reports,
    etc.). An employee with zero rows here behaves exactly as before this
    table existed (locked to Employee.branch_id, or unrestricted if NULL) —
    this table is purely additive. Same shape as the existing
    EmployeeLocation (Employee<->CompanyLocation), the one other "one
    identity, many X" precedent in this schema."""
    __tablename__ = "employee_branch_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class CompanyLocation(Base, TimestampMixin):
    __tablename__ = "company_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    location_name: Mapped[str] = mapped_column(String(120), nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    address: Mapped[str | None] = mapped_column(String(400))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    allowed_radius_meters: Mapped[int] = mapped_column(Integer, default=200)
    status: Mapped[str] = mapped_column(String(30), default="active")


class EmployeeLocation(Base, TimestampMixin):
    __tablename__ = "employee_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("company_locations.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)


class AttendanceSession(Base, TimestampMixin):
    __tablename__ = "attendance_sessions"
    __table_args__ = (
        Index("ix_att_session_company_emp_status", "company_id", "employee_id", "status"),
        Index("ix_att_session_status_check_in", "status", "check_in"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("company_locations.id"))
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), index=True)
    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_in_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_in_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_out_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_out_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    auto_checkout: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")


class EmployeeLocationLog(Base, TimestampMixin):
    __tablename__ = "employee_location_logs"
    __table_args__ = (
        Index("ix_emp_loc_log_company_emp_created", "company_id", "employee_id", "created_at"),
        Index("ix_emp_loc_log_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("attendance_sessions.id"), index=True)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    inside_geofence: Mapped[bool] = mapped_column(Boolean, default=True)
    device: Mapped[str | None] = mapped_column(String(120))
    battery: Mapped[int | None] = mapped_column(Integer)


class LeaveRequest(Base, TimestampMixin):
    __tablename__ = "leave_requests"
    __table_args__ = (Index("ix_leave_requests_company_employee", "company_id", "employee_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    leave_type: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[str] = mapped_column(String(20), nullable=False)
    end_date: Mapped[str] = mapped_column(String(20), nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    # Separate FK for an Employee-principal approver (HRMS sub-user) — kept
    # distinct from approved_by (-> users.id) rather than relaxing that FK,
    # since exactly one of the two is ever set depending on who approved.
    approved_by_employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when status becomes "cancelled" (by HR in HRMS, or by the employee in ESS).
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[str | None] = mapped_column(String(160))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class ApprovalMatrixRecord(Base, TimestampMixin):
    __tablename__ = "approval_matrix_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    max_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    approver_role: Mapped[str] = mapped_column(String(80), nullable=False)
    department: Mapped[str | None] = mapped_column(String(80))


class TrialRequest(Base, TimestampMixin):
    __tablename__ = "trial_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(60))
    employee_count: Mapped[str | None] = mapped_column(String(40))
    interest: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="new")



# Registers the session hooks that keep AppDataRecord.amount_paid current (needs AppDataRecord above).
import app.doc_index  # noqa: E402,F401
import app.account_totals  # noqa: E402,F401  -- keeps AccountPeriodTotal current
import app.doc_lines  # noqa: E402,F401  -- keeps DocumentLine current
