from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)
    phone: str | None = None
    company_name: str = Field(min_length=1)
    business_type: str | None = None
    emirate: str | None = None
    trn: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    plan: str = "starter"

    @field_validator("trn")
    @classmethod
    def validate_trn(cls, v: str | None) -> str | None:
        if v is not None and (not v.isdigit() or len(v) != 15):
            raise ValueError("TRN must be exactly 15 numeric digits")
        return v


class CompanyOut(BaseModel):
    id: str
    name: str
    trade_name: str | None = None
    trn: str | None = None
    country: str
    currency: str = "AED"
    vat_rate: Decimal = Decimal("5.00")
    emirate: str | None = None
    business_type: str | None = None
    business_activity: str | None = None
    legal_structure: str | None = None
    trade_license_no: str | None = None
    trade_license_issue_date: str | None = None
    trade_license_expiry: str | None = None
    free_zone: str | None = None
    address: str | None = None
    po_box: str | None = None
    phone: str | None = None
    website: str | None = None
    logo: str | None = None
    fta_username: str | None = None
    departments: str | None = None
    branches: str | None = None
    subscription_expires_at: str | None = None

    model_config = {"from_attributes": True}


class CompanyUpdate(BaseModel):
    name: str | None = None
    trade_name: str | None = None
    trn: str | None = None

    @field_validator("trn")
    @classmethod
    def validate_trn(cls, v: str | None) -> str | None:
        if v is not None and (not v.isdigit() or len(v) != 15):
            raise ValueError("TRN must be exactly 15 numeric digits")
        return v
    country: str | None = None
    currency: str | None = None
    vat_rate: Decimal | None = None
    emirate: str | None = None
    business_type: str | None = None
    business_activity: str | None = None
    legal_structure: str | None = None
    trade_license_no: str | None = None
    trade_license_issue_date: str | None = None
    trade_license_expiry: str | None = None
    free_zone: str | None = None
    address: str | None = None
    po_box: str | None = None
    phone: str | None = None
    website: str | None = None
    logo: str | None = None
    fta_username: str | None = None
    departments: str | None = None
    branches: str | None = None


class ImpersonatorOut(BaseModel):
    id: str
    email: EmailStr


class CompanyBrief(BaseModel):
    """Minimal company fields for embedding in /auth/me — deliberately excludes
    logo/departments/branches, which can be a large base64 blob and aren't used
    anywhere /auth/me's response is consumed (only .name is read, for the
    impersonation banner). Full company data is fetched separately via
    GET /companies/current when actually needed."""
    id: str
    name: str

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    company: CompanyBrief
    impersonated_by: ImpersonatorOut | None = None

    model_config = {"from_attributes": True}


class InvoiceLineIn(BaseModel):
    description: str
    quantity: Decimal = Field(default=1, ge=0)
    unit_price: Decimal = Field(default=0, ge=0)
    vat_rate: Decimal = Field(default=5, ge=0)


class InvoiceCreate(BaseModel):
    customer_name: str
    invoice_number: str
    lines: list[InvoiceLineIn]


class InvoiceLineOut(InvoiceLineIn):
    id: str

    model_config = {"from_attributes": True}


class InvoiceOut(BaseModel):
    id: str
    customer_name: str
    invoice_number: str
    status: str
    subtotal: Decimal
    vat: Decimal
    total: Decimal
    created_at: datetime
    lines: list[InvoiceLineOut] = []

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    storage_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    result: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountIn(BaseModel):
    code: str
    name: str
    type: str
    parent_account_id: str | None = None
    level: int = 5
    is_group: bool = False
    node_type: str = "POSTING_LEDGER"
    normal_balance: str = "DR"
    opening_balance: Decimal = Decimal("0.00")
    opening_balance_type: str = "DR"
    currency: str = "AED"
    tax_applicable: bool = False
    is_bank_cash: bool = False
    is_control_account: bool = False
    created_mode: str = "manual"
    status: str = "active"
    ai_confidence: Decimal | None = None
    is_active: bool = True


class AccountOut(AccountIn):
    id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountTreeNode(AccountOut):
    children: list["AccountTreeNode"] = []

    model_config = {"from_attributes": True}


AccountTreeNode.model_rebuild()


class JournalLineIn(BaseModel):
    account_id: str
    description: str | None = None
    debit: Decimal = Field(default=0, ge=0)
    credit: Decimal = Field(default=0, ge=0)


class JournalCreate(BaseModel):
    entry_number: str
    entry_date: datetime | None = None
    description: str
    source_module: str = "manual"
    source_id: str | None = None
    lines: list[JournalLineIn]


class JournalLineOut(JournalLineIn):
    id: str

    model_config = {"from_attributes": True}


class JournalOut(BaseModel):
    id: str
    entry_number: str
    source_module: str
    source_id: str | None
    entry_date: datetime
    description: str
    status: str
    created_at: datetime
    lines: list[JournalLineOut] = []

    model_config = {"from_attributes": True}


class SourceLineIn(BaseModel):
    description: str
    account_code: str
    quantity: Decimal = Field(default=1, ge=0)
    unit_price: Decimal = Field(default=0, ge=0)
    vat_rate: Decimal = Field(default=5, ge=0)


class SourceTransactionCreate(BaseModel):
    module: str
    reference: str
    party_name: str | None = None
    lines: list[SourceLineIn]


class RepostByReferenceIn(BaseModel):
    module: str
    reference: str


class SourceLineOut(BaseModel):
    id: str
    description: str
    account_code: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    amount: Decimal
    vat_amount: Decimal

    model_config = {"from_attributes": True}


class SourceTransactionOut(BaseModel):
    id: str
    module: str
    reference: str
    party_name: str | None
    status: str
    subtotal: Decimal
    vat: Decimal
    total: Decimal
    validation_result: str | None
    created_at: datetime
    lines: list[SourceLineOut] = []

    model_config = {"from_attributes": True}


class PostingJobOut(BaseModel):
    id: str
    source_id: str
    status: str
    retry_count: int
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VoucherTypeIn(BaseModel):
    name: str
    code: str
    prefix: str
    auto_numbering: bool = True
    default_debit_account_id: str | None = None
    default_credit_account_id: str | None = None
    approval_required: bool = True
    affects_cash_bank: bool = False
    affects_vat: bool = False
    status: str = "active"


class VoucherTypeOut(VoucherTypeIn):
    id: str

    model_config = {"from_attributes": True}


class VoucherLineIn(BaseModel):
    account_id: str
    debit: Decimal = Field(default=0, ge=0)
    credit: Decimal = Field(default=0, ge=0)
    party: str | None = None
    cost_center: str | None = None
    narration: str | None = None


class VoucherCreate(BaseModel):
    voucher_type_id: str
    voucher_no: str | None = None
    voucher_date: datetime | None = None
    party: str | None = None
    cost_center: str | None = None
    narration: str | None = None
    lines: list[VoucherLineIn]


class VoucherLineOut(VoucherLineIn):
    id: str

    model_config = {"from_attributes": True}


class VoucherOut(BaseModel):
    id: str
    voucher_type_id: str
    voucher_no: str
    voucher_date: datetime
    party: str | None
    cost_center: str | None
    narration: str | None
    status: str
    posted_journal_id: str | None
    lines: list[VoucherLineOut] = []

    model_config = {"from_attributes": True}


class GeneralLedgerEntryOut(BaseModel):
    id: str
    entry_date: datetime
    voucher_no: str
    voucher_type: str
    account_id: str
    debit: Decimal
    credit: Decimal
    balance: Decimal
    party: str | None
    cost_center: str | None
    narration: str | None

    model_config = {"from_attributes": True}


class PaymentCreate(BaseModel):
    payment_no: str | None = None
    payment_date: datetime | None = None
    payment_mode: str = "bank"
    cash_bank_account_id: str
    debit_account_id: str
    payee_type: str | None = None
    payee_name: str
    amount: Decimal = Field(gt=0)
    reference_no: str | None = None
    narration: str | None = None
    attachment: str | None = None
    post: bool = True


class PaymentOut(BaseModel):
    id: str
    payment_no: str
    payment_date: datetime
    payment_mode: str
    cash_bank_account_id: str
    debit_account_id: str
    payee_type: str | None
    payee_name: str
    amount: Decimal
    reference_no: str | None
    narration: str | None
    attachment: str | None
    status: str
    voucher_id: str | None

    model_config = {"from_attributes": True}


class ReceiptCreate(BaseModel):
    receipt_no: str | None = None
    receipt_date: datetime | None = None
    receipt_mode: str = "bank"
    cash_bank_account_id: str
    credit_account_id: str
    received_from: str
    amount: Decimal = Field(gt=0)
    reference_no: str | None = None
    narration: str | None = None
    attachment: str | None = None
    post: bool = True


class ReceiptOut(BaseModel):
    id: str
    receipt_no: str
    receipt_date: datetime
    receipt_mode: str
    cash_bank_account_id: str
    credit_account_id: str
    received_from: str
    amount: Decimal
    reference_no: str | None
    narration: str | None
    attachment: str | None
    status: str
    voucher_id: str | None

    model_config = {"from_attributes": True}


class TaxCodeIn(BaseModel):
    code: str
    name: str
    rate: Decimal = Field(default=5, ge=0)
    recoverable: bool = True
    reporting_box: str | None = None


class TaxCodeOut(TaxCodeIn):
    id: str

    model_config = {"from_attributes": True}


class TaxLineOut(BaseModel):
    id: str
    source_id: str | None
    direction: str
    taxable_amount: Decimal
    tax_amount: Decimal
    period: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VatReturnCreate(BaseModel):
    period: str
    adjustments: Decimal = Decimal("0.00")
    filing_status: str = "draft"
    fta_reference_no: str | None = None
    attachment: str | None = None


class VatReturnOut(BaseModel):
    id: str
    period: str
    sales_taxable_amount: Decimal
    output_vat: Decimal
    purchase_taxable_amount: Decimal
    input_vat: Decimal
    adjustments: Decimal
    net_vat: Decimal
    filing_status: str
    filed_date: datetime | None
    fta_reference_no: str | None
    attachment: str | None

    model_config = {"from_attributes": True}


class CorporateTaxReturnCreate(BaseModel):
    tax_period: str
    accounting_profit: Decimal = Decimal("0.00")
    non_deductible_expenses: Decimal = Decimal("0.00")
    exempt_income: Decimal = Decimal("0.00")
    tax_loss_adjustment: Decimal = Decimal("0.00")
    tax_rate: Decimal = Decimal("9.00")
    filing_status: str = "draft"
    reference_no: str | None = None
    attachment: str | None = None


class CorporateTaxReturnOut(BaseModel):
    id: str
    tax_period: str
    accounting_profit: Decimal
    non_deductible_expenses: Decimal
    exempt_income: Decimal
    tax_loss_adjustment: Decimal
    taxable_income: Decimal
    tax_rate: Decimal
    corporate_tax_payable: Decimal
    filing_status: str
    filed_date: datetime | None
    reference_no: str | None
    attachment: str | None

    model_config = {"from_attributes": True}


class BankAccountCreate(BaseModel):
    account_id: str
    bank_name: str
    iban: str | None = None
    account_number: str | None = None
    currency: str = "AED"
    status: str = "active"


class BankAccountOut(BankAccountCreate):
    id: str

    model_config = {"from_attributes": True}


class BankStatementLineCreate(BaseModel):
    bank_account_id: str
    statement_date: str
    transaction_date: str
    reference_no: str | None = None
    cheque_no: str | None = None
    narration: str | None = None
    party_name: str | None = None
    debit: Decimal = Field(default=0, ge=0)
    credit: Decimal = Field(default=0, ge=0)


class BankStatementLineOut(BankStatementLineCreate):
    id: str
    status: str

    model_config = {"from_attributes": True}


class BankMatchCreate(BaseModel):
    statement_line_id: str
    ledger_entry_id: str
    match_method: str = "manual"
    difference: Decimal = Decimal("0.00")


class BankMatchOut(BaseModel):
    id: str
    bank_account_id: str
    statement_line_id: str | None
    ledger_entry_id: str | None
    match_status: str
    match_method: str
    difference: Decimal
    confirmed_at: datetime | None

    model_config = {"from_attributes": True}


class PeriodLockIn(BaseModel):
    module: str
    period: str
    status: str = "locked"
    reason: str | None = None


class PeriodLockOut(PeriodLockIn):
    id: str
    locked_at: datetime | None

    model_config = {"from_attributes": True}


class WarehouseIn(BaseModel):
    name: str
    location: str | None = None


class WarehouseOut(WarehouseIn):
    id: str

    model_config = {"from_attributes": True}


class StockMappingIn(BaseModel):
    sku: str
    name: str
    supplier_name: str | None = None
    taxflow_name: str | None = None
    sales_account_code: str = "3000"
    purchase_account_code: str = "4000"
    inventory_account_code: str = "1200"
    tax_code: str = "VAT5"
    reorder_level: Decimal = Field(default=0, ge=0)
    units_per_outer: Decimal = Field(default=1, gt=0)
    cost: Decimal = Field(default=0, ge=0)
    markup_percent: Decimal = Field(default=0, ge=0)
    tax_rate: Decimal = Field(default=5, ge=0)
    vat_amount: Decimal = Field(default=0, ge=0)
    inc_vat: Decimal = Field(default=0, ge=0)
    price_outer: Decimal = Field(default=0, ge=0)


class StockMappingOut(StockMappingIn):
    id: str
    # Output-only — set server-side only when a user explicitly saves via
    # POST/PUT /inventory/mappings, never from client input. False means
    # this row was auto-created from purchase-line OCR text and nobody has
    # actually reviewed it yet.
    mapping_confirmed: bool = False

    model_config = {"from_attributes": True}


class ItemUnitIn(BaseModel):
    item_code: str
    unit_code: str
    unit_name: str
    conversion_factor: Decimal = Field(default=1, ge=0)
    is_base_unit: bool = False
    purchase_default: bool = False
    sales_default: bool = False
    status: str = "active"


class ItemUnitOut(ItemUnitIn):
    id: str

    model_config = {"from_attributes": True}


class ItemUnitConversionIn(BaseModel):
    item_code: str
    from_unit_code: str
    to_unit_code: str
    conversion_factor: Decimal = Field(default=1, gt=0)
    status: str = "active"


class ItemUnitConversionOut(ItemUnitConversionIn):
    id: str

    model_config = {"from_attributes": True}


class InventoryValuationLayerOut(BaseModel):
    id: str
    item_code: str
    warehouse_id: str | None
    source_module: str
    source_id: str | None
    quantity_in: Decimal
    quantity_remaining: Decimal
    unit_cost: Decimal
    valuation_method: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StockAdjustmentApprovalIn(BaseModel):
    item_code: str
    warehouse_id: str | None = None
    quantity_delta: Decimal
    reason: str = Field(min_length=1)

    @field_validator("quantity_delta")
    @classmethod
    def quantity_not_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("quantity_delta must not be zero")
        return v


class StockAdjustmentApprovalOut(StockAdjustmentApprovalIn):
    id: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EmployeeOut(BaseModel):
    id: str
    employee_no: str
    full_name: str
    department: str
    designation: str
    basic_salary: Decimal
    iban: str | None
    wps_id: str | None
    status: str

    model_config = {"from_attributes": True}


class PayrollGenerate(BaseModel):
    period: str = Field(default="2024-06", pattern=r"^\d{4}-\d{2}$")


class PayrollItemOut(BaseModel):
    id: str
    employee_id: str
    basic: Decimal
    allowances: Decimal
    overtime: Decimal
    deductions: Decimal
    net_pay: Decimal
    wps_status: str

    model_config = {"from_attributes": True}


class PayrollRunOut(BaseModel):
    id: str
    period: str
    status: str
    gross_total: Decimal
    deductions_total: Decimal
    net_total: Decimal
    items: list[PayrollItemOut] = []

    model_config = {"from_attributes": True}


class WpsBatchOut(BaseModel):
    id: str
    payroll_run_id: str
    batch_number: str
    status: str
    sif_content: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogOut(BaseModel):
    id: str
    module: str
    action: str
    record_id: str | None
    detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AppRecordIn(BaseModel):
    record_key: str | None = None
    payload: dict


class AppRecordOut(BaseModel):
    id: str
    record_key: str | None
    payload: dict
    created_at: datetime
    updated_at: datetime


class ExceptionEventIn(BaseModel):
    module: str
    category: str
    severity: str = "medium"
    source_record: str | None = None
    message: str
    status: str = "open"


class ExceptionEventOut(ExceptionEventIn):
    id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DomainEventIn(BaseModel):
    event_name: str
    source_module: str
    source_id: str | None = None
    payload: dict | None = None
    correlation_id: str | None = None


class DomainEventOut(BaseModel):
    id: str
    event_name: str
    source_module: str
    source_id: str | None
    payload: str | None
    correlation_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AIAssistRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AITransactionValidationRequest(BaseModel):
    source: SourceTransactionCreate
    supplier_trn: str | None = None
    evidence_present: bool = False
    tax_treatment: str = "standard"


class AIExceptionExplainRequest(BaseModel):
    module: str
    category: str
    severity: str = "medium"
    source_record: str | None = None
    message: str


class AIResponse(BaseModel):
    answer: str
    confidence: int = Field(ge=0, le=100)
    controls: list[str] = []
    suggested_actions: list[str] = []
    context: dict = {}
