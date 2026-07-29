"""Default chart of accounts, voucher types, and tax codes seeded for every
company. Shared between app.main's startup dev-seed logic and
routers/auth.py's real registration flow — moved out of main.py so
routers can import it without a circular import (main.py imports and
registers every router).

seed_accounts() in particular is load-bearing: post_source_transaction()
(accounting_posting.py) requires control accounts 1100 (Accounts
Receivable) and 2200 (VAT Output Payable) to exist before it can post any
sales invoice to the ledger. A company with none of these accounts gets a
PostingJob that fails silently — the source save still returns 200 and the
UI shows "Invoice saved", but nothing ever reaches the General Ledger or
VAT report. Found via an E2E test that created a real invoice against a
company with no seeded accounts and traced the missing journal entry back
to this exact failure mode.
"""
from sqlalchemy.orm import Session

from app.models import Account, TaxCode, VoucherType


def seed_accounts(db: Session, company_id: str) -> None:
    accounts = [
        ("1000", "Cash and Bank", "asset", True, True),
        ("1100", "Accounts Receivable", "asset", False, True),
        ("1200", "Inventory", "asset", False, True),
        ("2100", "Accounts Payable", "liability", False, True),
        ("2200", "VAT Output Payable", "liability", False, True),
        ("2210", "VAT Input Recoverable", "asset", False, True),
        ("2300", "Corporate Tax Payable", "liability", False, True),
        ("3000", "Sales Income", "sales", False, False),
        ("4000", "Purchases", "purchase", False, False),
        ("5000", "Cost of Goods Sold", "direct expense", False, False),
        ("5100", "Corporate Tax Expense", "indirect expense", False, False),
        ("6000", "Salary Expense", "indirect expense", False, False),
    ]
    for code, name, account_type, is_bank_cash, is_control in accounts:
        account = db.query(Account).filter(Account.company_id == company_id, Account.code == code).first()
        if not account:
            db.add(Account(company_id=company_id, code=code, name=name, type=account_type, is_bank_cash=is_bank_cash, is_control_account=is_control))


def seed_voucher_types(db: Session, company_id: str) -> None:
    rows = [
        ("Payment Voucher", "PAY", "PAY", True, True),
        ("Receipt Voucher", "RCT", "RCT", True, True),
        ("Journal Voucher", "JRN", "JRN", True, False),
        ("Sales Voucher", "SAL", "SAL", True, True),
        ("Purchase Voucher", "PUR", "PUR", True, True),
        ("Contra Voucher", "CON", "CON", True, False),
        ("Debit Note", "DN", "DN", True, True),
        ("Credit Note", "CN", "CN", True, True),
        ("Adjustment Voucher", "ADJ", "ADJ", True, True),
        ("Opening Balance Voucher", "OB", "OB", True, False),
    ]
    for name, code, prefix, approval_required, affects_vat in rows:
        voucher_type = db.query(VoucherType).filter(VoucherType.company_id == company_id, VoucherType.code == code).first()
        if not voucher_type:
            db.add(
                VoucherType(
                    company_id=company_id,
                    name=name,
                    code=code,
                    prefix=prefix,
                    approval_required=approval_required,
                    affects_cash_bank=code in {"PAY", "RCT", "CON"},
                    affects_vat=affects_vat,
                )
            )


def seed_tax_codes(db: Session, company_id: str) -> None:
    codes = [
        ("VAT5", "Standard UAE VAT", "5.00", True, "Box 1"),
        ("ZERO", "Zero-rated export", "0.00", False, "Box 4"),
        ("EXEMPT", "Exempt supply", "0.00", False, "Box 6"),
        ("RCM", "Reverse charge", "5.00", True, "Box 3"),
    ]
    for code, name, rate, recoverable, box in codes:
        tax_code = db.query(TaxCode).filter(TaxCode.company_id == company_id, TaxCode.code == code).first()
        if not tax_code:
            db.add(TaxCode(company_id=company_id, code=code, name=name, rate=rate, recoverable=recoverable, reporting_box=box))


def seed_company_defaults(db: Session, company_id: str) -> None:
    """Everything a company needs before it can post a single transaction."""
    seed_accounts(db, company_id)
    seed_voucher_types(db, company_id)
    seed_tax_codes(db, company_id)
