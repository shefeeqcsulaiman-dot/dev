from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="users")


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "invoice_number", name="uq_invoice_company_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalEntry(Base, TimestampMixin):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
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
    journal_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
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
    voucher_id: Mapped[str] = mapped_column(ForeignKey("vouchers.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    party: Mapped[str | None] = mapped_column(String(160))
    cost_center: Mapped[str | None] = mapped_column(String(80))
    narration: Mapped[str | None] = mapped_column(String(500))

    voucher: Mapped[Voucher] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()


class GeneralLedgerEntry(Base, TimestampMixin):
    __tablename__ = "general_ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    voucher_no: Mapped[str] = mapped_column(String(60), nullable=False)
    voucher_type: Mapped[str] = mapped_column(String(80), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
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


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
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
    iban: Mapped[str | None] = mapped_column(String(40))
    wps_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="active")


class PayrollRun(Base, TimestampMixin):
    __tablename__ = "payroll_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    gross_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    deductions_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    net_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    items: Mapped[list["PayrollItem"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PayrollItem(Base):
    __tablename__ = "payroll_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("payroll_runs.id"), nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), nullable=False)
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
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    collection: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    record_key: Mapped[str | None] = mapped_column(String(160), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    record_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(Text)


class AuditLogDetail(Base, TimestampMixin):
    __tablename__ = "audit_log_details"

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
    status: Mapped[str] = mapped_column(String(30), default="active")
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendancePunch(Base, TimestampMixin):
    __tablename__ = "attendance_punches"
    __table_args__ = (Index("ix_att_punch_company_date", "company_id", "punch_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(255))
    punch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    punch_date: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="in")
    device_id: Mapped[str | None] = mapped_column(String(36))
    device_name: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(32), default="manual")


class ApprovalMatrixRecord(Base, TimestampMixin):
    __tablename__ = "approval_matrix_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    max_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    approver_role: Mapped[str] = mapped_column(String(80), nullable=False)
    department: Mapped[str | None] = mapped_column(String(80))
