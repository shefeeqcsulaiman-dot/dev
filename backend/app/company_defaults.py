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
import json

from sqlalchemy.orm import Session

from app.models import Account, AppDataRecord, TaxCode, VoucherType


def seed_accounts(db: Session, company_id: str) -> None:
    # Group-level parents so a new company's Chart of Accounts renders as a
    # tree out of the box instead of 12 unrelated flat rows. Groups are
    # purely organizational (is_group=True) — posting logic looks up leaf
    # accounts by their own code (e.g. 1100, 2200), so nesting them under a
    # parent does not affect where transactions post.
    groups = [
        ("100", "Assets", "asset"),
        ("200", "Liabilities", "liability"),
        ("250", "Equity", "equity"),
        ("300", "Income", "sales"),
        ("400", "Expenses", "purchase"),
    ]
    group_ids: dict[str, str] = {}
    for code, name, group_type in groups:
        group = db.query(Account).filter(Account.company_id == company_id, Account.code == code).first()
        if not group:
            group = Account(company_id=company_id, code=code, name=name, type=group_type, level=1, is_group=True, node_type="MAIN_LEDGER")
            db.add(group)
            db.flush()
        group_ids[code] = group.id

    accounts = [
        ("1000", "Cash and Bank", "asset", True, True, "100"),
        ("1010", "Petty Cash", "asset", True, False, "100"),
        ("1020", "Bank Account", "asset", True, False, "100"),
        ("1100", "Accounts Receivable", "asset", False, True, "100"),
        ("1200", "Inventory", "asset", False, True, "100"),
        ("2100", "Accounts Payable", "liability", False, True, "200"),
        ("2200", "VAT Output Payable", "liability", False, True, "200"),
        ("2210", "VAT Input Recoverable", "asset", False, True, "100"),
        ("2300", "Corporate Tax Payable", "liability", False, True, "200"),
        ("2500", "Owner's Capital", "equity", False, False, "250"),
        ("2600", "Retained Earnings", "equity", False, False, "250"),
        ("2700", "Owner's Drawings", "equity", False, False, "250"),
        ("3000", "Sales Income", "sales", False, False, "300"),
        ("3100", "Sales Returns", "sales", False, False, "300"),
        ("3200", "Other Income", "sales", False, False, "300"),
        ("4000", "Purchases", "purchase", False, False, "400"),
        ("5000", "Cost of Goods Sold", "direct expense", False, False, "400"),
        ("5100", "Corporate Tax Expense", "indirect expense", False, False, "400"),
        ("6000", "Salary Expense", "indirect expense", False, False, "400"),
        ("6100", "Rent Expense", "indirect expense", False, False, "400"),
        ("6200", "Utilities Expense", "indirect expense", False, False, "400"),
        ("6300", "General & Administrative Expenses", "indirect expense", False, False, "400"),
        ("6400", "Bank Charges", "indirect expense", False, False, "400"),
    ]
    for code, name, account_type, is_bank_cash, is_control, group_code in accounts:
        account = db.query(Account).filter(Account.company_id == company_id, Account.code == code).first()
        if not account:
            db.add(Account(company_id=company_id, code=code, name=name, type=account_type, is_bank_cash=is_bank_cash, is_control_account=is_control, parent_account_id=group_ids[group_code], level=5, node_type="POSTING_LEDGER"))


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


def seed_sales_units(db: Session, company_id: str) -> None:
    """Settings > Purchase Settings > Unit Setup (index.html) shows EACH/TON/
    HOUR as if they were pre-populated rows, but that table is static HTML —
    clearStaticDemoData() (app.js) unconditionally wipes it on every page
    load (same generic sweep as every other "looks seeded, isn't real" table
    in that function), and it's then re-populated purely from this
    company's own salesUnits AppDataRecord rows. A company with none (every
    company before this function existed, since nothing ever wrote them)
    sees an empty Unit Setup table and an Item Master unit dropdown with
    only "PCS" — the on-screen EACH/TON/HOUR were never actually available
    to select, just a visual placeholder nobody had wired up. Writes real
    AppDataRecord rows so the table (and Item Master / POS unit dropdowns
    fed from it) show something usable from day one. Same record shape
    saveSalesUnit() (app.js) writes when a user adds one manually, so a
    later manual edit/save of "KG" etc. updates this row rather than
    duplicating it (record_key for salesUnits is "code", see app_data.py's
    record_key())."""
    units = [
        ("EACH", "Each", "Quantity", 0),
        ("KG", "Kilogram", "Weight", 2),
        ("TON", "Ton", "Weight", 2),
        ("HOUR", "Hour", "Time", 2),
    ]
    for code, name, unit_type, decimals in units:
        existing = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == company_id,
                AppDataRecord.collection == "salesUnits",
                AppDataRecord.record_key == code,
            )
            .first()
        )
        if existing:
            continue
        db.add(
            AppDataRecord(
                company_id=company_id,
                collection="salesUnits",
                record_key=code,
                payload=json.dumps({"code": code, "name": name, "type": unit_type, "decimals": decimals, "status": "Active"}),
            )
        )


def seed_service_types(db: Session, company_id: str) -> None:
    """POS's Service Type dropdown (pos.html) used to be a fixed, hardcoded
    Dine In/Takeaway/Delivery/Online list with no backend representation at
    all. Now that it's editable in Settings > Purchase Settings > Service
    Types, a brand-new company needs to start with the same four options it
    always had, as real serviceTypes AppDataRecord rows the POS dropdown and
    the Settings table both read from — same shape saveServiceType() (app.js)
    writes, so a later manual add/edit doesn't duplicate these."""
    names = ["Dine In", "Takeaway", "Delivery", "Online"]
    for name in names:
        existing = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == company_id,
                AppDataRecord.collection == "serviceTypes",
                AppDataRecord.record_key == name,
            )
            .first()
        )
        if existing:
            continue
        db.add(
            AppDataRecord(
                company_id=company_id,
                collection="serviceTypes",
                record_key=name,
                payload=json.dumps({"name": name, "status": "Active"}),
            )
        )


def backfill_default_ledgers(db: Session) -> None:
    """Once: give every existing company the ledgers added to seed_accounts() later (Equity
    group, Petty Cash, Bank Account, Sales Returns, Rent...). Existing codes are never touched."""
    from sqlalchemy import text

    from app.models import Company

    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    if db.execute(text("SELECT 1 FROM schema_flags WHERE name = 'default_ledgers_v2'")).first():
        return
    for (company_id,) in db.query(Company.id).filter((Company.trn.is_(None)) | (Company.trn != "SUPERADMIN-INTERNAL")).all():
        seed_accounts(db, company_id)
    db.execute(text("INSERT INTO schema_flags (name) VALUES ('default_ledgers_v2') ON CONFLICT (name) DO NOTHING"))
    db.commit()


_UI_ROLE_TO_USER_ROLE = {"admin": "admin", "manager": "manager", "accountant": "accountant", "sales": "sales", "viewer": "viewer"}


def backfill_user_roles_from_ui(db: Session) -> None:
    """Once: main-app logins created before roles were enforced were all saved as "user" (full
    access) whatever was picked in Settings > Users & Roles. Give each the role shown on that
    screen (stored in the "users" collection by email). Logins with no matching entry keep "user"."""
    from sqlalchemy import text

    from app.models import User

    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    if db.execute(text("SELECT 1 FROM schema_flags WHERE name = 'user_roles_from_ui_v1'")).first():
        return
    picked: dict[tuple[str, str], str] = {}
    for company_id, payload in db.query(AppDataRecord.company_id, AppDataRecord.payload).filter(AppDataRecord.collection == "users").all():
        try:
            rec = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        email = str(rec.get("email") or "").strip().lower()
        role = _UI_ROLE_TO_USER_ROLE.get(str(rec.get("role") or "").strip().lower())
        if email and role:
            picked[(company_id, email)] = role
    for user in db.query(User).filter(User.role == "user").all():
        role = picked.get((user.company_id, (user.email or "").strip().lower()))
        if role:
            user.role = role
    db.execute(text("INSERT INTO schema_flags (name) VALUES ('user_roles_from_ui_v1') ON CONFLICT (name) DO NOTHING"))
    db.commit()


def seed_company_defaults(db: Session, company_id: str) -> None:
    """Everything a company needs before it can post a single transaction."""
    seed_accounts(db, company_id)
    seed_voucher_types(db, company_id)
    seed_tax_codes(db, company_id)
    seed_sales_units(db, company_id)
    seed_service_types(db, company_id)
