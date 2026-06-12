import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Account, Company, TaxCode, User, VoucherType
from app.routers import accounting, ai, app_data, audit, auth, companies, corporate_accounting, documents, events, exception_center, inventory, invoices, jobs, module_records, payroll, reports, source_transactions, superadmin, tax
from app.security import hash_password


settings = get_settings()


def create_app() -> FastAPI:
    from app.limiter import limiter

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def cache_invalidation(request: Request, call_next):
        response = await call_next(request)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/v1/" in request.url.path:
            try:
                token = (request.headers.get("authorization", "")).removeprefix("Bearer ").strip()
                if token:
                    from app.security import user_id_from_token
                    user_id = user_id_from_token(token)
                    if user_id:
                        db = SessionLocal()
                        try:
                            user = db.query(User).filter(User.id == user_id).first()
                            if user:
                                import app.cache as _cache
                                _cache.invalidate_company(user.company_id)
                        finally:
                            db.close()
            except Exception:
                pass
        return response

    @app.on_event("startup")
    def startup() -> None:
        Base.metadata.create_all(bind=engine)
        ensure_schema_updates()
        seed_initial_data()

    static_dir = pathlib.Path(__file__).parent.parent / "frontend" / "public"
    site_dir = static_dir / "site"

    @app.get("/", response_model=None)
    def root():
        from fastapi.responses import RedirectResponse
        f = site_dir / "landing.html"
        if f.exists():
            return FileResponse(str(f))
        return RedirectResponse(url="/taxflow/", status_code=302)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    @app.get("/landing.html", include_in_schema=False)
    def landing() -> FileResponse:
        return FileResponse(str(site_dir / "landing.html"))

    @app.get("/signup.html", include_in_schema=False)
    def signup() -> FileResponse:
        return FileResponse(str(site_dir / "signup.html"))

    @app.get("/contact.html", include_in_schema=False)
    def contact() -> FileResponse:
        return FileResponse(str(site_dir / "contact.html"))

    @app.get("/taxflow/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "login.html"))

    @app.get("/taxflow/superadmin", include_in_schema=False)
    def superadmin_page() -> FileResponse:
        return FileResponse(str(static_dir / "taxflow" / "superadmin.html"))

    @app.get("/taxflow/config.js", include_in_schema=False)
    def config_js() -> Response:
        api_base = os.environ.get("API_BASE_URL", "")
        content = (
            f'window.TAXFLOW_API_BASE_URL = "{api_base}";\n' if api_base
            else "// local dev — app.js falls back to localhost:8000\n"
        )
        return Response(content=content, media_type="application/javascript")

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

    # Serve frontend static files
    if static_dir.exists():
        app.mount("/taxflow", StaticFiles(directory=str(static_dir / "taxflow"), html=True), name="taxflow")
        clients_dir = static_dir / "clients"
        if clients_dir.exists():
            app.mount("/clients", StaticFiles(directory=str(clients_dir)), name="clients")
        app.mount("/static-assets", StaticFiles(directory=str(static_dir)), name="assets")

    return app


def ensure_schema_updates() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "companies" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("companies")}
            if "subscription_expires_at" not in existing_columns:
                connection.execute(text("ALTER TABLE companies ADD COLUMN subscription_expires_at VARCHAR(20)"))
            if "logo" not in existing_columns:
                connection.execute(text("ALTER TABLE companies ADD COLUMN logo TEXT"))
            for col, typedef in [
                ("trade_name", "VARCHAR(160)"),
                ("emirate", "VARCHAR(80)"),
                ("business_type", "VARCHAR(80)"),
                ("business_activity", "VARCHAR(160)"),
                ("legal_structure", "VARCHAR(80)"),
                ("trade_license_no", "VARCHAR(80)"),
                ("address", "VARCHAR(400)"),
                ("po_box", "VARCHAR(20)"),
                ("phone", "VARCHAR(40)"),
                ("website", "VARCHAR(160)"),
            ]:
                if col not in existing_columns:
                    connection.execute(text(f"ALTER TABLE companies ADD COLUMN {col} {typedef}"))
        if "users" in table_names:
            existing_columns = {column["name"] for column in inspector.get_columns("users")}
            if "password_plain" not in existing_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN password_plain VARCHAR(255)"))
            if "is_active" not in existing_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL"))
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
                "purchase_default": "BOOLEAN DEFAULT FALSE",
                "sales_default": "BOOLEAN DEFAULT FALSE",
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
                "tax_applicable": "BOOLEAN DEFAULT FALSE",
                "is_bank_cash": "BOOLEAN DEFAULT FALSE",
                "is_control_account": "BOOLEAN DEFAULT FALSE",
                "level": "INTEGER DEFAULT 5",
                "is_group": "BOOLEAN DEFAULT FALSE",
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
                "UPDATE accounts SET node_type='MAIN_LEDGER' WHERE is_group=TRUE AND level=1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
            ))
            connection.execute(text(
                "UPDATE accounts SET node_type='SUB_LEDGER' WHERE is_group=TRUE AND level>1 AND (node_type IS NULL OR node_type='POSTING_LEDGER')"
            ))
        if "client_errors" not in table_names:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS client_errors (
                    id VARCHAR(36) PRIMARY KEY,
                    company_id VARCHAR(36),
                    user_id VARCHAR(36),
                    message TEXT NOT NULL,
                    stack TEXT,
                    url VARCHAR(500),
                    context VARCHAR(120),
                    user_agent VARCHAR(500),
                    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            connection.execute(text("CREATE INDEX IF NOT EXISTS idx_client_errors_company ON client_errors (company_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS idx_client_errors_occurred ON client_errors (occurred_at DESC)"))
        # Composite indexes on app_data_records for scale
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_app_data_company_collection "
            "ON app_data_records (company_id, collection)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_app_data_company_collection_created "
            "ON app_data_records (company_id, collection, created_at)"
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

        admin_pwd = settings.admin_password
        superadmin_pwd = settings.superadmin_password
        if admin_pwd in ("admin123", "change-me") and settings.app_env == "production":
            import warnings
            warnings.warn("ADMIN_PASSWORD is using the default value in production — set it via environment variable.", stacklevel=2)

        user.full_name = "Administrator"
        user.role = "admin"
        user.password_hash = hash_password(admin_pwd)
        user.company_id = company.id

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
                password_hash=hash_password(superadmin_pwd),
                role="superadmin",
            )
            db.add(sa_user)
        else:
            sa_user.role = "superadmin"
            sa_user.password_hash = hash_password(superadmin_pwd)
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
