import os
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, Company, TaxCode, User, VoucherType
from app.routers import accounting, ai, app_data, audit, auth, companies, corporate_accounting, documents, events, exception_center, inventory, invoices, jobs, module_records, payroll, reports, source_transactions, superadmin, tax
from app.security import hash_password


settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def startup() -> None:
        Base.metadata.create_all(bind=engine)
        ensure_schema_updates()
        seed_initial_data()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(ai.router, prefix="/api/v1")
    app.include_router(companies.router, prefix="/api/v1")
    app.include_router(invoices.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(source_transactions.router, prefix="/api/v1")
    app.include_router(accounting.router, prefix="/api/v1")
    app.include_router(corporate_accounting.router, prefix="/api/v1")
    app.include_router(tax.router, prefix="/api/v1")
    app.include_router(inventory.router, prefix="/api/v1")
    app.include_router(payroll.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(exception_center.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(module_records.router, prefix="/api/v1")
    app.include_router(app_data.router, prefix="/api/v1")
    app.include_router(superadmin.router, prefix="/api/v1")

    # Serve frontend static files — must be mounted last so API routes take priority
    static_dir = pathlib.Path(__file__).parent.parent.parent / "frontend" / "public"
    if static_dir.exists():
        # Write config.js so the frontend knows the API URL
        api_base = os.environ.get("API_BASE_URL", "")
        config_js = static_dir / "taxflow" / "config.js"
        config_js.write_text(
            f'window.TAXFLOW_API_BASE_URL = "{api_base}";\n' if api_base
            else "// local dev — app.js falls back to localhost:8000\n"
        )
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def ensure_schema_updates() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "companies" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("companies")}
            if "subscription_expires_at" not in existing_columns:
                connection.execute(text("ALTER TABLE companies ADD COLUMN subscription_expires_at VARCHAR(20)"))
        if "users" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("users")}
            if "password_plain" not in existing_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN password_plain VARCHAR(255)"))
                connection.execute(text("UPDATE users SET password_plain='admin123' WHERE email='admin@taxflowapp.com'"))
        if "stock_product_mappings" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("stock_product_mappings")}
            required_columns = {
                "supplier_name": "VARCHAR(160)",
                "taxflow_name": "VARCHAR(160)",
                "units_per_outer": "NUMERIC(12, 4) DEFAULT 1",
                "cost": "NUMERIC(12, 2) DEFAULT 0",
                "markup_percent": "NUMERIC(8, 2) DEFAULT 0",
                "tax_rate": "NUMERIC(5, 2) DEFAULT 5",
                "vat_amount": "NUMERIC(12, 2) DEFAULT 0",
                "inc_vat": "NUMERIC(12, 2) DEFAULT 0",
                "price_outer": "NUMERIC(12, 2) DEFAULT 0",
            }
            for column_name, column_type in required_columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE stock_product_mappings ADD COLUMN {column_name} {column_type}"))
        if "item_units" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("item_units")}
            required_columns = {
                "purchase_default": "BOOLEAN DEFAULT 0",
                "sales_default": "BOOLEAN DEFAULT 0",
                "status": "VARCHAR(30) DEFAULT 'active'",
            }
            for column_name, column_type in required_columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE item_units ADD COLUMN {column_name} {column_type}"))
        if "invoices" in table_names:
            index_names = {index["name"] for index in inspector.get_indexes("invoices")}
            if "uq_invoice_company_number" not in index_names:
                connection.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_company_number ON invoices (company_id, invoice_number)")
                )
        if "accounts" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("accounts")}
            required_columns = {
                "parent_account_id": "VARCHAR(36)",
                "opening_balance": "NUMERIC(18, 2) DEFAULT 0",
                "opening_balance_type": "VARCHAR(2) DEFAULT 'DR'",
                "currency": "VARCHAR(10) DEFAULT 'AED'",
                "tax_applicable": "BOOLEAN DEFAULT 0",
                "is_bank_cash": "BOOLEAN DEFAULT 0",
                "is_control_account": "BOOLEAN DEFAULT 0",
                "level": "INTEGER DEFAULT 5",
                "is_group": "BOOLEAN DEFAULT 0",
                "node_type": "VARCHAR(30) DEFAULT 'POSTING_LEDGER'",
                "normal_balance": "VARCHAR(2) DEFAULT 'DR'",
                "created_mode": "VARCHAR(20) DEFAULT 'manual'",
                "status": "VARCHAR(20) DEFAULT 'active'",
                "ai_confidence": "NUMERIC(5,2)",
            }
            for column_name, column_type in required_columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type}"))
            # Backfill node_type for existing accounts
            connection.execute(text(
                "UPDATE accounts SET node_type='MAIN_LEDGER' WHERE is_group=1 AND level=1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
            ))
            connection.execute(text(
                "UPDATE accounts SET node_type='SUB_LEDGER' WHERE is_group=1 AND level>1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
            ))


def seed_initial_data() -> None:
    db: Session = SessionLocal()
    try:
        company = db.query(Company).filter(Company.trn == "100000000000003").first()
        if not company:
            company = Company(name="Company", trn="100000000000003")
            db.add(company)
            db.flush()
        else:
            company.name = "Company"

        user = db.query(User).filter(User.email == "admin@taxflowapp.com").first()
        legacy_user = db.query(User).filter(User.email == "admin@taxflow.local").first()
        if not user:
            if legacy_user:
                user = legacy_user
                user.email = "admin@taxflowapp.com"
            else:
                user = User(
                    company_id=company.id,
                    email="admin@taxflowapp.com",
                    full_name="Administrator",
                    role="admin",
                )
                db.add(user)

        user.full_name = "Administrator"
        user.role = "admin"
        user.password_hash = hash_password("admin123")
        user.company_id = company.id

        user.password_plain = "admin123"

        seed_accounts(db, company.id)
        seed_voucher_types(db, company.id)
        seed_tax_codes(db, company.id)

        # Seed super admin (isolated company so it never appears in regular app)
        sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
        if not sa_company:
            sa_company = Company(name="ETaxFlow Admin", trn="SUPERADMIN-INTERNAL")
            db.add(sa_company)
            db.flush()
        sa_user = db.query(User).filter(User.email == "superadmin@etaxflow.com").first()
        if not sa_user:
            sa_user = User(
                company_id=sa_company.id,
                email="superadmin@etaxflow.com",
                full_name="Super Administrator",
                password_hash=hash_password("super123"),
                password_plain="super123",
                role="superadmin",
            )
            db.add(sa_user)
        else:
            sa_user.role = "superadmin"
            sa_user.password_hash = hash_password("super123")
            sa_user.password_plain = "super123"
            sa_user.company_id = sa_company.id

        db.commit()
    finally:
        db.close()


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


app = create_app()
