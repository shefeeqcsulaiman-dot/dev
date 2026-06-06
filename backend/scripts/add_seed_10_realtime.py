from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from app.database import Base, SessionLocal, engine
from app.models import AppDataRecord, Company, User
from app.security import hash_password


PREFIX = "RT10"
ADMIN_EMAIL = "admin@taxflowapp.com"
COMPANY_TRN = "100000000000003"
TODAY = date(2026, 5, 22)


CUSTOMERS = [
    ("Aster Building Materials LLC", "Dubai", "accounts@asterbuild.ae"),
    ("Marina Office Solutions LLC", "Dubai", "finance@marinaoffice.ae"),
    ("Al Zahra Trading FZE", "Sharjah", "ap@alzahratrading.ae"),
    ("Nexus Facility Services LLC", "Abu Dhabi", "accounts@nexusfacility.ae"),
    ("Falcon Retail Group LLC", "Ajman", "billing@falconretail.ae"),
    ("Oryx Contracting LLC", "Dubai", "finance@oryxcontracting.ae"),
    ("GulfLine Logistics LLC", "Ras Al Khaimah", "accounts@gulflinelogistics.ae"),
    ("Pearl Tech Supplies LLC", "Sharjah", "billing@pearltech.ae"),
    ("Summit Hospitality LLC", "Fujairah", "finance@summithospitality.ae"),
    ("Capital Safety Equipment LLC", "Abu Dhabi", "ap@capitalsafety.ae"),
]

VENDORS = [
    ("Vertex Steel Trading LLC", "Materials"),
    ("Emirates Freight Services LLC", "Logistics"),
    ("Bright Pack Industries LLC", "Packaging"),
    ("SafeWay PPE Trading LLC", "Safety"),
    ("Metro Electrical Supplies LLC", "Electrical"),
    ("Gulf Office Furniture LLC", "Furniture"),
    ("Prime IT Hardware LLC", "IT"),
    ("Desert Fuel Services LLC", "Fuel"),
    ("Rapid Courier LLC", "Courier"),
    ("Al Noor Maintenance LLC", "Maintenance"),
]

PRODUCTS = [
    ("RT10-STL-12", "Steel Rods 12mm", "Materials", "Ton", Decimal("4200.00")),
    ("RT10-PKG-A", "Corrugated Box A", "Packaging", "Pcs", Decimal("18.50")),
    ("RT10-PPE-H", "Safety Helmet", "Safety", "Pcs", Decimal("42.00")),
    ("RT10-CBL-R", "Copper Cable Roll", "Electrical", "Roll", Decimal("690.00")),
    ("RT10-CHR-E", "Ergonomic Office Chair", "Furniture", "Pcs", Decimal("375.00")),
    ("RT10-LAP-15", "Business Laptop 15 Inch", "IT", "Pcs", Decimal("2850.00")),
    ("RT10-FUEL", "Diesel Supply", "Fuel", "Liter", Decimal("3.15")),
    ("RT10-COU", "Document Courier", "Courier", "Trip", Decimal("35.00")),
    ("RT10-MNT", "Maintenance Technician Hour", "Maintenance", "Hour", Decimal("120.00")),
    ("RT10-VEST", "High Visibility Vest", "Safety", "Pcs", Decimal("28.00")),
]

EMPLOYEES = [
    ("RT10-EMP-001", "Hassan Kareem", "Operations", "Storekeeper"),
    ("RT10-EMP-002", "Nadia Farouk", "Finance", "Accounts Officer"),
    ("RT10-EMP-003", "Omar Nasser", "Sales", "Sales Executive"),
    ("RT10-EMP-004", "Leena George", "HR", "HR Coordinator"),
    ("RT10-EMP-005", "Imran Qureshi", "Operations", "Driver"),
    ("RT10-EMP-006", "Maya Joseph", "Procurement", "Buyer"),
    ("RT10-EMP-007", "Bilal Ahmed", "Warehouse", "Inventory Clerk"),
    ("RT10-EMP-008", "Ritu Sharma", "Admin", "Office Administrator"),
    ("RT10-EMP-009", "Samir Haddad", "IT", "Support Engineer"),
    ("RT10-EMP-010", "Farah Mansour", "Management", "Operations Lead"),
]


def decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def ensure_company_user(db) -> tuple[Company, User]:
    company = db.query(Company).filter(Company.trn == COMPANY_TRN).first()
    if not company:
        company = Company(name="TaxFlow UAE LLC", trn=COMPANY_TRN, country="United Arab Emirates")
        db.add(company)
        db.flush()

    user = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if not user:
        user = User(company_id=company.id, email=ADMIN_EMAIL, full_name="Sara Ahmed", role="admin")
        db.add(user)
    user.company_id = company.id
    user.full_name = "Sara Ahmed"
    user.role = "admin"
    user.password_hash = hash_password("admin123")
    db.flush()
    return company, user


def upsert_app_record(db, company_id: str, collection: str, key: str, payload: dict) -> None:
    existing = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == collection,
            AppDataRecord.record_key == key,
        )
        .first()
    )
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if existing:
        existing.payload = encoded
    else:
        db.add(AppDataRecord(company_id=company_id, collection=collection, record_key=key, payload=encoded))


def seed(db, company: Company) -> None:
    for index in range(1, 11):
        suffix = f"{index:03d}"
        tx_date = TODAY - timedelta(days=10 - index)
        due_date = tx_date + timedelta(days=30)
        customer_name, emirate, customer_email = CUSTOMERS[index - 1]
        vendor_name, vendor_category = VENDORS[index - 1]
        sku, product_name, product_category, unit, price = PRODUCTS[index - 1]
        employee_no, employee_name, department, designation = EMPLOYEES[index - 1]
        qty = Decimal(index + 1)
        subtotal = (price * qty).quantize(Decimal("0.01"))
        vat = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
        total = subtotal + vat
        purchase_ref = f"{PREFIX}-PUR-{suffix}"
        invoice_no = f"{PREFIX}-INV-{suffix}"

        upsert_app_record(db, company.id, "customers", f"{PREFIX}-CUS-{suffix}", {
            "name": customer_name,
            "trn": f"10066{index:010d}",
            "emirate": emirate,
            "email": customer_email,
            "phone": f"+971 50 66{index:04d}",
            "status": "Active",
        })
        upsert_app_record(db, company.id, "vendors", f"{PREFIX}-VEN-{suffix}", {
            "name": vendor_name,
            "trn": f"10077{index:010d}",
            "category": vendor_category,
            "email": f"accounts{index:02d}@rt10vendor.ae",
            "phone": f"+971 55 77{index:04d}",
            "status": "Active",
        })
        upsert_app_record(db, company.id, "products", sku, {
            "code": sku,
            "name": product_name,
            "category": product_category,
            "unit": unit,
            "cost": decimal_text(price * Decimal("0.72")),
            "price": decimal_text(price),
            "vat": "Standard 5%",
            "supplier_name": vendor_name,
            "reorder_level": 10 + index,
            "opening_stock": 20 + index,
            "status": "Active",
        })
        upsert_app_record(db, company.id, "salesInvoices", invoice_no, {
            "invoice_no": invoice_no,
            "customer": customer_name,
            "customer_trn": f"10066{index:010d}",
            "date": tx_date.isoformat(),
            "due_date": due_date.isoformat(),
            "subtotal": decimal_text(subtotal),
            "vat_amount": decimal_text(vat),
            "total": decimal_text(total),
            "status": "Paid" if index % 3 == 0 else "Ready",
            "source": "Realtime Seed",
            "lines": [{"sku": sku, "description": product_name, "qty": float(qty), "price": float(price), "amount": float(subtotal)}],
        })
        upsert_app_record(db, company.id, "quotations", f"{PREFIX}-QTN-{suffix}", {
            "quote_no": f"{PREFIX}-QTN-{suffix}",
            "customer": customer_name,
            "subject": f"Quotation for {product_name}",
            "date": tx_date.isoformat(),
            "valid_until": (tx_date + timedelta(days=14)).isoformat(),
            "subtotal": decimal_text(subtotal),
            "vat_amount": decimal_text(vat),
            "total": decimal_text(total),
            "status": "Sent" if index % 2 else "Draft",
            "lines": [{"sku": sku, "description": product_name, "qty": float(qty), "price": float(price), "amount": float(subtotal)}],
        })
        upsert_app_record(db, company.id, "purchaseRecords", purchase_ref, {
            "ref": purchase_ref,
            "supplier": vendor_name,
            "date": tx_date.isoformat(),
            "location": "Main Store",
            "items": 1,
            "net_amount": decimal_text(subtotal),
            "tax_amount": decimal_text(vat),
            "shipping": "0.00",
            "total": decimal_text(total),
            "paid": "0.00" if index % 2 else decimal_text(total),
            "due": decimal_text(total if index % 2 else Decimal("0.00")),
            "source": "Realtime Seed",
            "status": "Pending Payment" if index % 2 else "Received",
            "supplier_trn": f"10077{index:010d}",
            "lines": [{"sku": sku, "product": product_name, "unit": unit, "quantity": float(qty), "unit_cost": float(price), "line_total": float(subtotal)}],
        })
        upsert_app_record(db, company.id, "purchaseDocuments", f"{PREFIX}-DOC-{suffix}", {
            "id": f"{PREFIX}-DOC-{suffix}",
            "name": f"{purchase_ref}.pdf",
            "status": "Completed",
            "summary": "Completed 1/1 saved to purchase records",
            "saved": 1,
            "total": 1,
            "extractedAt": tx_date.isoformat(),
        })
        upsert_app_record(db, company.id, "bills", f"{PREFIX}-BILL-{suffix}", {
            "bill_no": f"{PREFIX}-BILL-{suffix}",
            "vendor": vendor_name,
            "date": tx_date.isoformat(),
            "due": due_date.isoformat(),
            "subtotal": decimal_text(subtotal),
            "vat": decimal_text(vat),
            "total": decimal_text(total),
            "status": "Awaiting Payment",
        })
        upsert_app_record(db, company.id, "payments", f"{PREFIX}-PAY-{suffix}", {
            "ref": f"{PREFIX}-PAY-{suffix}",
            "contact": customer_name if index % 2 else vendor_name,
            "type": "Customer Receipt" if index % 2 else "Supplier Payment",
            "method": "Bank Transfer",
            "date": tx_date.isoformat(),
            "amount": decimal_text(total),
            "status": "Posted",
        })
        upsert_app_record(db, company.id, "expenses", f"{PREFIX}-EXP-{suffix}", {
            "ref": f"{PREFIX}-EXP-{suffix}",
            "employee": employee_name,
            "description": f"{vendor_category} operating expense",
            "category": vendor_category,
            "date": tx_date.isoformat(),
            "amount": decimal_text(subtotal),
            "vat": decimal_text(vat),
            "total": decimal_text(total),
            "status": "Pending",
        })
        upsert_app_record(db, company.id, "bankAccounts", f"{PREFIX}-BANK-{suffix}", {
            "name": f"Business Account {index}",
            "bank": ["Emirates NBD", "ADCB", "FAB", "Mashreq"][index % 4],
            "iban": f"AE07{index:02d}1234567890123456",
            "balance": decimal_text(Decimal("25000") + Decimal(index * 1850)),
            "status": "Active",
        })
        upsert_app_record(db, company.id, "employees", employee_no, {
            "id": employee_no,
            "name": employee_name,
            "department": department,
            "designation": designation,
            "supervisor": "Sara Ahmed",
            "shift": "09:00-18:00",
            "salary": 5000 + index * 450,
            "status": "Active",
            "location": "Dubai HQ",
        })
        upsert_app_record(db, company.id, "users", f"{PREFIX}-USER-{suffix}", {
            "id": f"{PREFIX}-USER-{suffix}",
            "name": employee_name,
            "email": f"rt10.user{index:02d}@taxflow.local",
            "role": ["Manager", "Accountant", "Sales", "Viewer"][index % 4],
            "status": "Active",
            "permissions": ["sales:view", "purchases:view", "reports:view"],
        })
        upsert_app_record(db, company.id, "salesCategories", f"{PREFIX}-CAT-{suffix}", {
            "name": f"{product_category} RT {index:02d}",
            "scope": "Sales & Purchase",
            "vat": "Standard 5%",
            "status": "Active",
        })
        upsert_app_record(db, company.id, "salesUnits", f"{PREFIX}-UNIT-{suffix}", {
            "code": f"RT{index:02d}",
            "name": unit,
            "type": "Quantity",
            "decimals": "2",
            "status": "Active",
        })
        upsert_app_record(db, company.id, "accounts", f"{PREFIX}-ACC-{suffix}", {
            "code": f"9{index:03d}",
            "name": f"Realtime Control Account {index}",
            "type": "Expense" if index % 2 else "Asset",
            "category": "Current",
            "balance": decimal_text(total),
            "status": "Active",
        })
        upsert_app_record(db, company.id, "ledger", f"{PREFIX}-LED-{suffix}", {
            "date": tx_date.isoformat(),
            "account": "Realtime Sales Income",
            "description": f"Realtime journal for {invoice_no}",
            "debit": decimal_text(total if index % 2 == 0 else Decimal("0")),
            "credit": decimal_text(total if index % 2 else Decimal("0")),
            "ref": invoice_no,
        })
        upsert_app_record(db, company.id, "costCenters", f"{PREFIX}-CC-{suffix}", {
            "code": f"CC-RT-{suffix}",
            "name": f"Realtime Cost Center {index}",
            "manager": employee_name,
            "budget": decimal_text(Decimal("10000") + Decimal(index * 500)),
            "status": "Active",
        })


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        company, _ = ensure_company_user(db)
        seed(db, company)
        db.commit()
        print("Added/updated 10 realtime records across main TaxFlow UI tables.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
