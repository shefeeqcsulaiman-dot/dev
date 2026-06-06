from __future__ import annotations

import json
from decimal import Decimal

from app.database import Base, SessionLocal, engine
from app.models import AppDataRecord

import scripts.add_seed_50_all as bulk_seed


SEED_COUNT = 15
PREFIX = "REAL15"

CUSTOMERS = [
    ("Al Noor Building Materials LLC", "Dubai", "accounts@alnoorbuild.ae"),
    ("Blue Pearl Logistics FZE", "Sharjah", "finance@bluepearllogistics.ae"),
    ("Gulf Star Trading LLC", "Abu Dhabi", "accounts@gulfstartrading.ae"),
    ("Emirates Facility Services LLC", "Dubai", "ap@emiratesfacility.ae"),
    ("Desert Line Contracting LLC", "Ajman", "finance@desertline.ae"),
    ("Royal Horizon Supplies LLC", "Dubai", "billing@royalhorizon.ae"),
    ("Union Tech Solutions FZE", "Ras Al Khaimah", "accounts@uniontech.ae"),
    ("Prime Metal Works LLC", "Sharjah", "finance@primemetal.ae"),
    ("Oasis Retail Group LLC", "Dubai", "ap@oasisretail.ae"),
    ("Capital Office Furniture LLC", "Abu Dhabi", "accounts@capitaloffice.ae"),
    ("Metro Safety Equipment LLC", "Dubai", "billing@metrosafety.ae"),
    ("Falcon Auto Parts LLC", "Sharjah", "accounts@falconparts.ae"),
    ("Nile Foodstuff Trading LLC", "Dubai", "finance@nilefoodstuff.ae"),
    ("Crescent Cleaning Services LLC", "Ajman", "accounts@crescentclean.ae"),
    ("Harbor Marine Supplies FZE", "Fujairah", "finance@harbormarine.ae"),
]

SUPPLIERS = [
    ("Atlas Steel Suppliers LLC", "Materials"),
    ("National Freight Services LLC", "Logistics"),
    ("Bright Office Supplies LLC", "Supplies"),
    ("SafePro Equipment Trading LLC", "Equipment"),
    ("Al Massa Electricals LLC", "Electrical"),
    ("Green Way Packaging LLC", "Packaging"),
    ("City Print Solutions LLC", "Printing"),
    ("Gulf Fuel Services LLC", "Fuel"),
    ("Vertex IT Hardware LLC", "IT"),
    ("Dubai Uniforms Factory LLC", "Uniforms"),
    ("Pearl Water Trading LLC", "Utilities"),
    ("Apex Maintenance LLC", "Maintenance"),
    ("Marhaba Courier Services LLC", "Courier"),
    ("Eastern Tools Trading LLC", "Tools"),
    ("Omega Warehouse Rentals LLC", "Rentals"),
]

PRODUCTS = [
    ("STL-12MM", "Steel Rods 12mm", "Materials", "Ton", Decimal("4200.00")),
    ("LOG-LOCAL", "Local Delivery Service", "Logistics", "Trip", Decimal("850.00")),
    ("OFF-CHAIR", "Ergonomic Office Chair", "Furniture", "Pcs", Decimal("375.00")),
    ("PPE-HELMET", "Safety Helmet", "Safety", "Pcs", Decimal("42.00")),
    ("ELE-CABLE", "Copper Cable Roll", "Electrical", "Roll", Decimal("690.00")),
    ("PKG-BOX", "Corrugated Packing Box", "Packaging", "Box", Decimal("18.50")),
    ("PRN-FLYER", "Printed Flyer Pack", "Printing", "Pack", Decimal("240.00")),
    ("FUEL-DIESEL", "Diesel Supply", "Fuel", "Liter", Decimal("3.15")),
    ("IT-MON24", "24 Inch LED Monitor", "IT", "Pcs", Decimal("520.00")),
    ("UNI-STAFF", "Staff Uniform Set", "Uniforms", "Set", Decimal("95.00")),
    ("WTR-CASE", "Drinking Water Case", "Utilities", "Case", Decimal("14.00")),
    ("MNT-HOUR", "Maintenance Technician Hour", "Maintenance", "Hour", Decimal("120.00")),
    ("COU-DOC", "Document Courier", "Courier", "Trip", Decimal("35.00")),
    ("TLS-DRILL", "Cordless Drill Machine", "Tools", "Pcs", Decimal("310.00")),
    ("WH-SPACE", "Warehouse Space Rental", "Rentals", "Month", Decimal("6500.00")),
]


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


def reseed_app_collections(db, company_id: str) -> None:
    db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.record_key.like(f"{PREFIX}%"),
    ).delete(synchronize_session=False)
    db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.record_key.like("QTN-REAL-%"),
    ).delete(synchronize_session=False)

    for index in range(1, SEED_COUNT + 1):
        suffix = f"{index:03d}"
        customer_name, emirate, customer_email = CUSTOMERS[index - 1]
        supplier_name, category = SUPPLIERS[index - 1]
        sku, product_name, product_category, unit, price = PRODUCTS[index - 1]
        subtotal = Decimal("1500.00") + Decimal(index * 275)
        vat = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
        total = subtotal + vat

        upsert_app_record(db, company_id, "customers", customer_name, {
            "name": customer_name,
            "trn": f"10044{index:010d}",
            "emirate": emirate,
            "email": customer_email,
        })
        upsert_app_record(db, company_id, "products", sku, {
            "code": sku,
            "name": product_name,
            "category": product_category,
            "unit": unit,
            "price": str(price),
            "vat": "Standard 5%",
        })
        upsert_app_record(db, company_id, "salesInvoices", f"INV-REAL-{suffix}", {
            "invoice_no": f"INV-REAL-{suffix}",
            "customer": customer_name,
            "date": "11 May 2026",
            "due_date": "10 Jun 2026",
            "subtotal": str(subtotal),
            "vat_amount": str(vat),
            "total": str(total),
            "source": "Manual",
            "status": "Ready",
        })
        upsert_app_record(db, company_id, "quotations", f"QTN-REAL-{suffix}", {
            "quote_no": f"QTN-REAL-{suffix}",
            "customer": customer_name,
            "date": "11 May 2026",
            "valid_until": "26 May 2026",
            "subtotal": str(subtotal),
            "vat_amount": str(vat),
            "total": str(total),
            "status": "Sent" if index % 3 else "Draft",
            "owner": "Sales Team",
        })
        upsert_app_record(db, company_id, "vendors", supplier_name, {
            "name": supplier_name,
            "trn": f"10055{index:010d}",
            "category": category,
            "email": f"accounts@{supplier_name.lower().replace(' ', '').replace('.', '')[:20]}.ae",
        })
        upsert_app_record(db, company_id, "purchaseRecords", f"PUR-REAL-{suffix}", {
            "ref": f"PUR-REAL-{suffix}",
            "supplier": supplier_name,
            "date": "11 May 2026",
            "location": "Main Store",
            "items": 1 + index % 5,
            "net_amount": str(subtotal),
            "tax_amount": str(vat),
            "shipping": "0.00",
            "total": str(total),
            "paid": "0.00",
            "due": str(total),
            "source": "Manual",
            "status": "Pending Payment",
        })
        upsert_app_record(db, company_id, "bills", f"BILL-REAL-{suffix}", {
            "bill_no": f"BILL-REAL-{suffix}",
            "vendor": supplier_name,
            "date": "11 May 2026",
            "due": "10 Jun 2026",
            "subtotal": str(subtotal),
            "vat": str(vat),
            "total": str(total),
            "status": "Awaiting Payment",
        })
        upsert_app_record(db, company_id, "payments", f"PAY-REAL-{suffix}", {
            "ref": f"PAY-REAL-{suffix}",
            "contact": customer_name if index % 2 else supplier_name,
            "type": "Customer Receipt" if index % 2 else "Supplier Payment",
            "method": "Bank Transfer",
            "date": "11 May 2026",
            "amount": str(total),
        })
        upsert_app_record(db, company_id, "salesCategories", f"CAT-REAL-{suffix}", {
            "name": product_category,
            "scope": "Sales & Purchase",
            "vat": "Standard 5%",
            "status": "Active",
        })
        upsert_app_record(db, company_id, "salesUnits", f"UNIT-REAL-{suffix}", {
            "code": unit.upper()[:6],
            "name": unit,
            "type": "Quantity",
            "decimals": "2",
            "status": "Active",
        })


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        company, user = bulk_seed.ensure_company_user(db)
        bulk_seed.PREFIX = "BULK50"
        bulk_seed.delete_seed_rows(db)
        bulk_seed.PREFIX = PREFIX
        bulk_seed.SEED_COUNT = SEED_COUNT
        bulk_seed.seed(db, company, user)
        reseed_app_collections(db, company.id)
        db.commit()
        print(f"Added {SEED_COUNT} realistic records across backend tables and UI collections.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
