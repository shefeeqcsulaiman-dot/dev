from __future__ import annotations

from decimal import Decimal

import json

from app.database import Base, SessionLocal, engine
from app.models import (
    Account,
    AppDataRecord,
    AuditLog,
    Company,
    Document,
    Employee,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    PayrollRun,
    SourceTransaction,
    SourceTransactionLine,
    StockProductMapping,
    TaxCode,
    TaxLine,
    User,
)
from app.security import hash_password


SEED_COUNT = 50
PREFIX = "REAL50"
ADMIN_EMAIL = "admin@taxflowapp.com"
ADMIN_PASSWORD = "admin123"
COMPANY_TRN = "100000000000003"

CUSTOMER_PREFIXES = [
    "Al Noor", "Blue Pearl", "Gulf Star", "Emirates", "Desert Line",
    "Royal Horizon", "Union Tech", "Prime Metal", "Oasis", "Capital",
    "Metro Safety", "Falcon", "Nile", "Crescent", "Harbor Marine",
    "Skyline", "Golden Gate", "Silver Coast", "Palm View", "Nova Gulf",
    "Al Maha", "City Link", "Red Sea", "Green Oasis", "Future Build",
]
CUSTOMER_SUFFIXES = [
    "Building Materials LLC", "Logistics FZE", "Trading LLC", "Facility Services LLC",
    "Contracting LLC", "Supplies LLC", "Solutions FZE", "Metal Works LLC",
    "Retail Group LLC", "Office Furniture LLC",
]
SUPPLIER_PREFIXES = [
    "Atlas", "National", "Bright", "SafePro", "Al Massa", "Green Way",
    "City Print", "Gulf Fuel", "Vertex", "Dubai Uniforms", "Pearl Water",
    "Apex", "Marhaba", "Eastern Tools", "Omega Warehouse", "Rapid Freight",
    "Modern Electrical", "First Choice", "Al Fajr", "United Packaging",
]
SUPPLIER_CATEGORIES = [
    "Materials", "Logistics", "Supplies", "Equipment", "Electrical",
    "Packaging", "Printing", "Fuel", "IT", "Uniforms",
]
PRODUCTS = [
    ("STL-12MM", "Steel Rods 12mm", "Materials", "Ton", "4200.00"),
    ("LOG-LOCAL", "Local Delivery Service", "Logistics", "Trip", "850.00"),
    ("OFF-CHAIR", "Ergonomic Office Chair", "Furniture", "Pcs", "375.00"),
    ("PPE-HELMET", "Safety Helmet", "Safety", "Pcs", "42.00"),
    ("ELE-CABLE", "Copper Cable Roll", "Electrical", "Roll", "690.00"),
    ("PKG-BOX", "Corrugated Packing Box", "Packaging", "Box", "18.50"),
    ("PRN-FLYER", "Printed Flyer Pack", "Printing", "Pack", "240.00"),
    ("FUEL-DIESEL", "Diesel Supply", "Fuel", "Liter", "3.15"),
    ("IT-MON24", "24 Inch LED Monitor", "IT", "Pcs", "520.00"),
    ("UNI-STAFF", "Staff Uniform Set", "Uniforms", "Set", "95.00"),
    ("WTR-CASE", "Drinking Water Case", "Utilities", "Case", "14.00"),
    ("MNT-HOUR", "Maintenance Technician Hour", "Maintenance", "Hour", "120.00"),
    ("COU-DOC", "Document Courier", "Courier", "Trip", "35.00"),
    ("TLS-DRILL", "Cordless Drill Machine", "Tools", "Pcs", "310.00"),
    ("WH-SPACE", "Warehouse Space Rental", "Rentals", "Month", "6500.00"),
    ("PPE-VEST", "High Visibility Safety Vest", "Safety", "Pcs", "28.00"),
    ("JAN-CLEAN", "Deep Cleaning Service", "Services", "Hour", "75.00"),
    ("IT-LAP15", "Business Laptop 15 Inch", "IT", "Pcs", "2850.00"),
    ("ELE-LED", "LED Panel Light", "Electrical", "Pcs", "86.00"),
    ("TLS-HAM", "Industrial Hammer", "Tools", "Pcs", "64.00"),
]
EMIRATES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah", "Umm Al Quwain"]


def generated_customers() -> list[tuple[str, str, str]]:
    rows = []
    for index in range(1, SEED_COUNT + 1):
        prefix = CUSTOMER_PREFIXES[(index - 1) % len(CUSTOMER_PREFIXES)]
        suffix = CUSTOMER_SUFFIXES[(index - 1) % len(CUSTOMER_SUFFIXES)]
        name = f"{prefix} {suffix}"
        if index > len(CUSTOMER_PREFIXES):
            name = f"{prefix} {index:02d} {suffix}"
        domain = "".join(ch for ch in prefix.lower() if ch.isalnum())
        rows.append((name, EMIRATES[(index - 1) % len(EMIRATES)], f"accounts{index:02d}@{domain}.ae"))
    return rows


def generated_suppliers() -> list[tuple[str, str]]:
    rows = []
    for index in range(1, SEED_COUNT + 1):
        prefix = SUPPLIER_PREFIXES[(index - 1) % len(SUPPLIER_PREFIXES)]
        category = SUPPLIER_CATEGORIES[(index - 1) % len(SUPPLIER_CATEGORIES)]
        name = f"{prefix} {category} LLC"
        if index > len(SUPPLIER_PREFIXES):
            name = f"{prefix} {index:02d} {category} LLC"
        rows.append((name, category))
    return rows


def generated_products() -> list[tuple[str, str, str, str, Decimal]]:
    rows = []
    for index in range(1, SEED_COUNT + 1):
        code, name, category, unit, price = PRODUCTS[(index - 1) % len(PRODUCTS)]
        round_no = ((index - 1) // len(PRODUCTS)) + 1
        rows.append((f"{code}-{round_no:02d}", f"{name} {round_no:02d}", category, unit, Decimal(price) + Decimal(index * 3)))
    return rows


def upsert_app_record(db, company_id: str, collection: str, record_key: str, payload: dict) -> None:
    existing = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == collection,
            AppDataRecord.record_key == record_key,
        )
        .first()
    )
    encoded = json.dumps(payload, default=str)
    if existing:
        existing.payload = encoded
    else:
        db.add(AppDataRecord(company_id=company_id, collection=collection, record_key=record_key, payload=encoded))


def ensure_company_user(db) -> tuple[Company, User]:
    company = db.query(Company).filter(Company.trn == COMPANY_TRN).first()
    if not company:
        company = Company(name="TaxFlow Demo LLC", trn=COMPANY_TRN, country="United Arab Emirates")
        db.add(company)
        db.flush()
    company.name = "TaxFlow Demo LLC"
    company.country = "United Arab Emirates"

    user = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if not user:
        user = User(company_id=company.id, email=ADMIN_EMAIL, full_name="Sara Ahmed", role="admin")
        db.add(user)
    user.company_id = company.id
    user.full_name = "Sara Ahmed"
    user.role = "admin"
    user.password_hash = hash_password(ADMIN_PASSWORD)
    db.flush()
    return company, user


def ensure_account(db, company: Company, code: str, name: str, account_type: str) -> Account:
    account = db.query(Account).filter(Account.company_id == company.id, Account.code == code).first()
    if not account:
        account = Account(company_id=company.id, code=code, name=name, type=account_type)
        db.add(account)
    account.name = name
    account.type = account_type
    db.flush()
    return account


def ensure_tax_code(db, company: Company) -> TaxCode:
    tax_code = db.query(TaxCode).filter(TaxCode.company_id == company.id, TaxCode.code == "VAT5").first()
    if not tax_code:
        tax_code = TaxCode(company_id=company.id, code="VAT5", name="Standard UAE VAT", rate=5, recoverable=True, reporting_box="Box 1")
        db.add(tax_code)
    db.flush()
    return tax_code


def clean_previous_seed(db, company_id: str) -> None:
    ui_collections = (
        "customers",
        "products",
        "salesInvoices",
        "quotations",
        "vendors",
        "purchaseRecords",
        "bills",
        "payments",
        "salesCategories",
        "salesUnits",
    )
    db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection.in_(ui_collections),
    ).delete(synchronize_session=False)
    prefixes = ("REAL15", "REAL50", "BULK50")
    for prefix in prefixes:
        db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key.like("QTN-REAL-%")).delete(synchronize_session=False)
        db.query(Invoice).filter(Invoice.company_id == company_id, Invoice.invoice_number.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(SourceTransaction).filter(SourceTransaction.company_id == company_id, SourceTransaction.reference.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(JournalEntry).filter(JournalEntry.company_id == company_id, JournalEntry.entry_number.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(Account).filter(Account.company_id == company_id, Account.code.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(TaxCode).filter(TaxCode.company_id == company_id, TaxCode.code.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(Document).filter(Document.company_id == company_id, Document.storage_key.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(Employee).filter(Employee.company_id == company_id, Employee.employee_no.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(PayrollRun).filter(PayrollRun.company_id == company_id, PayrollRun.period.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.company_id == company_id, AuditLog.action.like(f"{prefix}%")).delete(synchronize_session=False)
        db.query(StockProductMapping).filter(StockProductMapping.company_id == company_id, StockProductMapping.sku.like(f"{prefix}%")).delete(synchronize_session=False)


def reseed_records(db, company: Company, user: User) -> None:
    customers = generated_customers()
    suppliers = generated_suppliers()
    products = generated_products()
    ar = ensure_account(db, company, f"{PREFIX}-1100", "Real Customer Receivables", "asset")
    sales_account = ensure_account(db, company, f"{PREFIX}-3000", "Real Sales Income", "revenue")
    purchase_account = ensure_account(db, company, f"{PREFIX}-4000", "Real Purchase Expense", "expense")
    ensure_account(db, company, f"{PREFIX}-5000", "Real Cost of Sales", "expense")
    tax_code = ensure_tax_code(db, company)

    for index in range(1, SEED_COUNT + 1):
        suffix = f"{index:03d}"
        customer_name, emirate, customer_email = customers[index - 1]
        supplier_name, category = suppliers[index - 1]
        sku, product_name, product_category, unit, price = products[index - 1]
        subtotal = Decimal("1850.00") + Decimal(index * 245)
        vat = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
        total = subtotal + vat

        invoice_no = f"{PREFIX}-INV-{suffix}"
        quote_no = f"QTN-REAL-{suffix}"
        purchase_ref = f"{PREFIX}-PUR-{suffix}"
        bill_no = f"{PREFIX}-BILL-{suffix}"
        payment_ref = f"{PREFIX}-PAY-{suffix}"

        upsert_app_record(db, company.id, "customers", customer_name, {
            "name": customer_name, "trn": f"10044{index:010d}", "emirate": emirate, "email": customer_email,
        })
        upsert_app_record(db, company.id, "products", sku, {
            "code": sku, "name": product_name, "category": product_category, "unit": unit, "price": str(price), "vat": "Standard 5%",
        })
        upsert_app_record(db, company.id, "salesInvoices", invoice_no, {
            "invoice_no": invoice_no, "customer": customer_name, "date": "12 May 2026", "due_date": "11 Jun 2026",
            "subtotal": str(subtotal), "vat_amount": str(vat), "total": str(total), "source": "Manual", "status": "Ready",
        })
        upsert_app_record(db, company.id, "quotations", quote_no, {
            "quote_no": quote_no, "customer": customer_name, "date": "12 May 2026", "valid_until": "27 May 2026",
            "subtotal": str(subtotal), "vat_amount": str(vat), "total": str(total),
            "status": "Sent" if index % 3 else "Draft", "owner": "Sales Team",
        })
        upsert_app_record(db, company.id, "vendors", supplier_name, {
            "name": supplier_name, "trn": f"10055{index:010d}", "category": category,
            "email": f"accounts{index:02d}@supplier{index:02d}.ae",
        })
        upsert_app_record(db, company.id, "purchaseRecords", purchase_ref, {
            "ref": purchase_ref, "supplier": supplier_name, "date": "12 May 2026", "location": "Main Store",
            "items": 1 + index % 6, "net_amount": str(subtotal), "tax_amount": str(vat), "shipping": "0.00",
            "total": str(total), "paid": "0.00", "due": str(total), "source": "Manual", "status": "Pending Payment",
        })
        upsert_app_record(db, company.id, "bills", bill_no, {
            "bill_no": bill_no, "vendor": supplier_name, "date": "12 May 2026", "due": "11 Jun 2026",
            "subtotal": str(subtotal), "vat": str(vat), "total": str(total), "status": "Awaiting Payment",
        })
        upsert_app_record(db, company.id, "payments", payment_ref, {
            "ref": payment_ref, "contact": customer_name if index % 2 else supplier_name,
            "type": "Customer Receipt" if index % 2 else "Supplier Payment", "method": "Bank Transfer",
            "date": "12 May 2026", "amount": str(total),
        })
        upsert_app_record(db, company.id, "salesCategories", f"{PREFIX}-CAT-{suffix}", {
            "name": f"{product_category} {index:02d}", "scope": "Sales & Purchase", "vat": "Standard 5%", "status": "Active",
        })
        upsert_app_record(db, company.id, "salesUnits", f"{PREFIX}-UNIT-{suffix}", {
            "code": f"{unit.upper()[:4]}{index:02d}", "name": unit, "type": "Quantity", "decimals": "2", "status": "Active",
        })

        invoice = Invoice(company_id=company.id, customer_name=customer_name, invoice_number=invoice_no, status="paid" if index % 4 == 0 else "issued", subtotal=subtotal, vat=vat, total=total)
        invoice.lines = [InvoiceLine(description=product_name, quantity=1 + index % 5, unit_price=price, vat_rate=5)]
        db.add(invoice)

        source = SourceTransaction(company_id=company.id, module="sales" if index % 2 else "purchase", reference=f"{PREFIX}-SRC-{suffix}", party_name=customer_name if index % 2 else supplier_name, status="approved", subtotal=subtotal, vat=vat, total=total, approved_by=user.id, validation_result="Real 50 test data validated")
        source.lines = [SourceTransactionLine(description=product_name, account_code=sales_account.code if index % 2 else purchase_account.code, quantity=1, unit_price=subtotal, vat_rate=5, amount=subtotal, vat_amount=vat)]
        db.add(source)
        db.flush()

        db.add(TaxLine(company_id=company.id, source_id=source.id, tax_code_id=tax_code.id, direction="output" if index % 2 else "input", taxable_amount=subtotal, tax_amount=vat, period="2026-05"))
        journal = JournalEntry(company_id=company.id, entry_number=f"{PREFIX}-JE-{suffix}", source_module="real50", source_id=source.id, description=f"Real 50 journal {suffix}")
        journal.lines = [
            JournalLine(account_id=ar.id, description=f"Invoice {invoice_no}", debit=total, credit=0),
            JournalLine(account_id=sales_account.id, description=f"Revenue {invoice_no}", debit=0, credit=subtotal),
        ]
        db.add(journal)
        db.add(StockProductMapping(company_id=company.id, sku=f"{PREFIX}-{sku}", name=product_name, tax_code="VAT5", reorder_level=10 + index))
        db.add(Document(company_id=company.id, filename=f"{invoice_no}.pdf", content_type="application/pdf", storage_key=f"{PREFIX}/documents/{invoice_no}.pdf"))
        db.add(Employee(company_id=company.id, employee_no=f"{PREFIX}-EMP-{suffix}", full_name=f"Real Employee {suffix}", department=["Finance", "Sales", "Operations", "HR"][index % 4], designation="Staff", basic_salary=Decimal("4500.00") + Decimal(index * 25)))
        db.add(PayrollRun(company_id=company.id, period=f"{PREFIX}-2026-05-{suffix}", gross_total=Decimal("5200.00") + Decimal(index * 40), deductions_total=Decimal("250.00"), net_total=Decimal("4950.00") + Decimal(index * 40), status="approved"))
        db.add(AuditLog(company_id=company.id, user_id=user.id, module="real50", action=f"{PREFIX} audit {suffix}", record_id=invoice_no))


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        company, user = ensure_company_user(db)
        clean_previous_seed(db, company.id)
        reseed_records(db, company, user)
        db.commit()
        print(f"Added {SEED_COUNT} realistic records across backend tables and UI collections.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
