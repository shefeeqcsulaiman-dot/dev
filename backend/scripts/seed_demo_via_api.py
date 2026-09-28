"""Adds demo data to an existing TaxFlow company through the app's own API,
exactly as the UI saves it (so ledger postings, VAT, stock and audit logs all
run normally). Nothing is written to the database directly.

    python backend/scripts/seed_demo_via_api.py --base-url https://dev.etaxflow.com \\
        --email admin@taxflowapp.com

The password is prompted for (or read from SEED_PASSWORD) and never stored.
Defaults: 14 branches, 25 customers, 15 suppliers, 100 products, 100
employees, 100 purchases, 100 sales invoices. Re-running updates the same
records (stable DEMO- keys) instead of duplicating them.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

TAG = "DEMO"
VAT_RATE = 5.0

BRANCHES = [
    ("Dubai Marina", "DXB-MAR", "Dubai"), ("Deira", "DXB-DEI", "Dubai"), ("Business Bay", "DXB-BBY", "Dubai"),
    ("Al Quoz", "DXB-QOZ", "Dubai"), ("JLT", "DXB-JLT", "Dubai"), ("Khalifa City", "AUH-KHC", "Abu Dhabi"),
    ("Mussafah", "AUH-MUS", "Abu Dhabi"), ("Al Ain", "AAN-CTR", "Al Ain"), ("Sharjah Industrial", "SHJ-IND", "Sharjah"),
    ("Al Majaz", "SHJ-MAJ", "Sharjah"), ("Ajman", "AJM-CTR", "Ajman"), ("Ras Al Khaimah", "RAK-CTR", "Ras Al Khaimah"),
    ("Fujairah", "FUJ-CTR", "Fujairah"), ("Umm Al Quwain", "UAQ-CTR", "Umm Al Quwain"),
]
CUSTOMER_NAMES = [
    "Al Noor Trading LLC", "Blue Pearl Logistics FZE", "Gulf Star Contracting LLC", "Emirates Facility Services LLC",
    "Desert Line Supplies LLC", "Royal Horizon Retail Group LLC", "Union Tech Solutions FZE", "Prime Metal Works LLC",
    "Oasis Office Furniture LLC", "Capital Building Materials LLC", "Metro Safety Supplies LLC", "Falcon Logistics FZE",
    "Crescent Contracting LLC", "Harbor Marine Services LLC", "Skyline Facility Services LLC", "Palm View Trading LLC",
    "Nova Gulf Solutions FZE", "Al Maha Retail Group LLC", "City Link Logistics FZE", "Red Sea Trading LLC",
    "Green Oasis Supplies LLC", "Future Build Contracting LLC", "Silver Coast Trading LLC", "Golden Gate Supplies LLC",
    "Nile Metal Works LLC",
]
SUPPLIERS = [
    ("Atlas Building Supplies LLC", "Materials"), ("National Steel Trading LLC", "Materials"),
    ("Bright Electrical Wholesale LLC", "Electrical"), ("SafePro Equipment LLC", "Equipment"),
    ("Al Massa Packaging LLC", "Packaging"), ("Green Way Logistics FZE", "Logistics"),
    ("City Print Services LLC", "Printing"), ("Gulf Fuel Distribution LLC", "Fuel"),
    ("Vertex IT Solutions FZE", "IT"), ("Dubai Uniforms Trading LLC", "Uniforms"),
    ("Pearl Water Supplies LLC", "Supplies"), ("Apex Tools Trading LLC", "Equipment"),
    ("Eastern Hardware LLC", "Materials"), ("Omega Warehouse Supplies LLC", "Supplies"),
    ("United Packaging Industries LLC", "Packaging"),
]
PRODUCT_BASES = [
    ("Steel Rods", "Materials", "Ton", 3900), ("Cement Bag 50kg", "Materials", "Bag", 18), ("Plywood Sheet", "Materials", "PCS", 65),
    ("PVC Pipe", "Materials", "PCS", 22), ("Copper Cable", "Electrical", "Roll", 410), ("LED Panel Light", "Electrical", "PCS", 85),
    ("Circuit Breaker", "Electrical", "PCS", 48), ("Safety Helmet", "Safety", "PCS", 25), ("Safety Gloves", "Safety", "Pair", 9),
    ("Hi-Vis Vest", "Safety", "PCS", 14), ("Office Chair", "Furniture", "PCS", 320), ("Office Desk", "Furniture", "PCS", 690),
    ("A4 Paper Box", "Stationery", "Box", 95), ("Printer Toner", "Stationery", "PCS", 240), ("Laptop 14in", "IT", "PCS", 2650),
    ("Wireless Mouse", "IT", "PCS", 45), ("Network Switch", "IT", "PCS", 780), ("Drinking Water 5 Gal", "Supplies", "Bottle", 7),
    ("Cleaning Detergent", "Supplies", "Can", 32), ("Packing Carton", "Packaging", "PCS", 4),
]
PRODUCT_VARIANTS = ["Standard", "Premium", "Heavy Duty", "Compact", "Pro"]
FIRST_NAMES = [
    "Ahmed", "Fatima", "Mohammed", "Aisha", "Omar", "Mariam", "Khalid", "Sara", "Yousef", "Noura", "Rashid", "Layla",
    "Hassan", "Huda", "Ali", "Reem", "Saeed", "Amna", "Tariq", "Hind", "Rahul", "Priya", "Arjun", "Anjali", "Joseph",
    "Maria", "John", "Grace", "Imran", "Ayesha",
]
LAST_NAMES = [
    "Al Mansoori", "Al Zaabi", "Al Hashimi", "Al Suwaidi", "Al Nuaimi", "Khan", "Rahman", "Nair", "Menon", "Pillai",
    "Fernandes", "D'Souza", "Santos", "Reyes", "Hussain", "Qureshi", "Siddiqui", "Farouk", "Haddad", "Nasser",
]
NATIONALITIES = ["United Arab Emirates", "India", "Pakistan", "Philippines", "Egypt", "Jordan", "Sri Lanka", "Nepal"]
DEPARTMENTS = [
    ("Sales", ["Sales Executive", "Account Manager", "Sales Coordinator"]),
    ("Operations", ["Operations Officer", "Warehouse Supervisor", "Driver", "Storekeeper"]),
    ("Finance", ["Accountant", "Finance Officer"]),
    ("HR", ["HR Officer", "HR Coordinator"]),
    ("IT", ["IT Support", "Systems Administrator"]),
    ("Management", ["Branch Manager", "Assistant Manager"]),
]


class Api:
    def __init__(self, base: str, token: str | None = None):
        self.base = base.rstrip("/") + "/api/v1"
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from None

    def save(self, collection: str, record: dict):
        return self.call("POST", "/app-data?action=save", {"collection": collection, "record": record})


def money(x: float) -> float:
    return round(x + 1e-9, 2)


def trn(rng: random.Random) -> str:
    return "100" + "".join(str(rng.randint(0, 9)) for _ in range(12))


def build_products(rng: random.Random) -> list[dict]:
    products = []
    for i in range(100):
        base, category, unit, cost = PRODUCT_BASES[i % len(PRODUCT_BASES)]
        variant = PRODUCT_VARIANTS[i // len(PRODUCT_BASES) % len(PRODUCT_VARIANTS)]
        cost = money(cost * rng.uniform(0.9, 1.15))
        supplier = SUPPLIERS[i % len(SUPPLIERS)][0]
        products.append({
            "code": f"{TAG}-P{i + 1:03d}", "name": f"{base} {variant}", "category": category, "unit": unit,
            "cost": cost, "price": money(cost * rng.uniform(1.2, 1.45)), "vat": "5%", "tracking": "Yes",
            "supplier_name": supplier, "reorder_level": rng.choice([5, 10, 20]), "status": "Active",
        })
    return products


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="https://dev.etaxflow.com")
    p.add_argument("--email", required=True, help="Company admin login")
    p.add_argument("--seed", type=int, default=42, help="Random seed (same seed = same data)")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = p.parse_args()

    password = os.environ.get("SEED_PASSWORD") or getpass.getpass(f"Password for {args.email}: ")
    api = Api(args.base_url)
    login = api.call("POST", "/auth/login", {"email": args.email, "password": password})
    api.token = login["access_token"]
    company = api.call("GET", "/companies/current")
    print(f"Signed in to {args.base_url} as {args.email} - company: {company.get('name')}")
    if not args.yes and input("Add 14 branches, 25 customers, 15 suppliers, 100 products, 100 employees, "
                              "100 purchases and 100 sales invoices to this company? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1

    rng = random.Random(args.seed)
    today = date.today()
    failures: list[str] = []

    def attempt(label: str, fn):
        try:
            fn()
            return True
        except RuntimeError as exc:
            failures.append(f"{label}: {exc}")
            return False

    # Branches (created once; re-runs reuse by name)
    existing = {b["name"].lower(): b for b in api.call("GET", "/branches") or []}
    branch_ids: dict[str, str] = {}
    for name, code, city in BRANCHES:
        if name.lower() in existing:
            branch_ids[name] = existing[name.lower()]["id"]
            continue
        def create(name=name, code=code, city=city):
            b = api.call("POST", "/branches", {"name": name, "code": code[:6], "city": city,
                                               "country": "United Arab Emirates", "currency": "AED", "status": "Active"})
            branch_ids[name] = b["id"]
        attempt(f"branch {name}", create)
    print(f"Branches: {len(branch_ids)}")

    customers = []
    for i, name in enumerate(CUSTOMER_NAMES):
        c = {"name": name, "trn": trn(rng), "emirate": rng.choice(["Dubai", "Abu Dhabi", "Sharjah", "Ajman"]),
             "address": f"Office {100 + i}, {rng.choice(['Al Quoz', 'Deira', 'Mussafah', 'Al Nahda'])}",
             "email": f"accounts{i + 1}@example.ae", "phone": f"+9715{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}"}
        if attempt(f"customer {name}", lambda c=c: api.save("customers", c)):
            customers.append(c)
    print(f"Customers: {len(customers)}")

    for i, (name, category) in enumerate(SUPPLIERS):
        v = {"name": name, "trn": trn(rng), "category": category, "email": f"sales{i + 1}@supplier.example.ae",
             "phone": f"+9714{rng.randint(1000000, 9999999)}", "address": f"Warehouse {i + 1}, Jebel Ali"}
        attempt(f"supplier {name}", lambda v=v: api.save("vendors", v))
    print(f"Suppliers: {len(SUPPLIERS)}")

    products = build_products(rng)
    saved = sum(attempt(f"product {pr['code']}", lambda pr=pr: api.save("products", pr)) for pr in products)
    print(f"Products: {saved}")

    branch_names = list(branch_ids) or [b[0] for b in BRANCHES]
    saved = 0
    for i in range(100):
        dept, titles = DEPARTMENTS[i % len(DEPARTMENTS)]
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        branch = branch_names[i % len(branch_names)]
        salary = rng.choice([3500, 4500, 6000, 7500, 9000, 12000, 15000])
        emp = {
            "id": f"{TAG}-E{i + 1:03d}", "name": f"{first} {last}", "email": f"{first}.{last}.{i + 1}".lower().replace(" ", "").replace("'", "") + "@example.ae",
            "mobile": f"+97150{rng.randint(1000000, 9999999)}", "department": dept, "designation": rng.choice(titles),
            "salary": salary, "housing_allowance": money(salary * 0.25), "transport_allowance": 500, "other_allowance": 0,
            "contract": "Full-Time", "location": branch, "branch": branch, "branch_id": branch_ids.get(branch, ""),
            "status": "Active", "nationality": rng.choice(NATIONALITIES), "gender": rng.choice(["Male", "Female"]),
            "join_date": (today - timedelta(days=rng.randint(60, 1500))).isoformat(),
            "shift_hours_type": "weekly", "shift_hours": 48, "leave_policy": "Standard",
            "iban": "AE07" + "".join(str(rng.randint(0, 9)) for _ in range(19)), "salary_bank": rng.choice(["ENBD", "ADCB", "FAB", "Mashreq"]),
            "created_at": f"{today.isoformat()}T08:00:00Z",
        }
        saved += attempt(f"employee {emp['id']}", lambda emp=emp: api.save("employees", emp))
    print(f"Employees: {saved}")

    # Purchases first so tracked stock exists before the sales take it out;
    # the first 25 purchases stock every product (4 each) and sales never
    # sell more than was bought, so no product goes negative.
    stock = {pr["code"]: 0 for pr in products}
    saved = 0
    for i in range(100):
        supplier = SUPPLIERS[i % len(SUPPLIERS)][0]
        d = today - timedelta(days=rng.randint(91, 120) if i < 25 else rng.randint(5, 120))
        lines, net = [], 0.0
        picks = products[i * 4:i * 4 + 4] if i < 25 else rng.sample(products, rng.randint(1, 4))
        for pr in picks:
            qty = rng.randint(20, 80)
            stock[pr["code"]] += qty
            total = money(qty * pr["cost"])
            net += total
            lines.append({"product": pr["name"], "quantity": qty, "unit_of_measure": pr["unit"], "unit_cost": pr["cost"],
                          "discount_percent": 0, "unit_cost_before_tax": pr["cost"], "line_total": total})
        net = money(net)
        tax = money(net * VAT_RATE / 100)
        total = money(net + tax)
        paid = total if i % 2 == 0 else 0.0
        rec = {
            "ref": f"{TAG}-PUR-{i + 1:04d}", "supplier": supplier, "address": "", "date": d.isoformat(),
            "status": "Paid" if paid else "Pending Payment", "location": "", "pay_term": "Net 30",
            "due_date": (d + timedelta(days=30)).isoformat(), "items": sum(l["quantity"] for l in lines),
            "net_amount": net, "discount": 0, "tax_amount": tax, "shipping": 0, "additional_expenses": [],
            "additional_expense_amount": 0, "total": total, "paid": paid, "due": money(total - paid),
            "discount_type": "None", "discount_value": 0, "tax_type": "VAT 5%", "lines": lines,
            "payment_method": "Bank Transfer" if paid else "Credit", "payment_account": "None", "payment_note": "",
            "paid_on": d.isoformat() if paid else "", "shipping_details": "", "notes": "Demo data",
            "source": "Manual", "document_type": "Purchase Invoice",
        }
        saved += attempt(f"purchase {rec['ref']}", lambda rec=rec: api.save("purchaseRecords", rec))
    print(f"Purchases: {saved}")

    saved = 0
    for i in range(100):
        cust = customers[i % len(customers)] if customers else {"name": "Walk-in Customer", "trn": "", "address": ""}
        d = today - timedelta(days=rng.randint(0, 90))
        lines, sub = [], 0.0
        for pr in [x for x in rng.sample(products, rng.randint(1, 4)) if stock[x["code"]] > 0] or [max(products, key=lambda x: stock[x["code"]])]:
            qty = min(rng.randint(1, 10), stock[pr["code"]])
            stock[pr["code"]] -= qty
            amount = money(qty * pr["price"])
            sub += amount
            lines.append({"description": pr["name"], "unit": pr["unit"], "qty": qty, "price": pr["price"],
                          "unit_price": pr["price"], "amount": amount, "product_code": pr["code"], "product_name": pr["name"],
                          "price_source": "Product", "price_snapshot": pr["price"], "tax_rate": VAT_RATE})
        sub = money(sub)
        vat = money(sub * VAT_RATE / 100)
        inv = {
            "invoice_no": f"{TAG}-INV-{i + 1:04d}", "document_type": "Sales Invoice", "customer": cust["name"],
            "customer_trn": cust.get("trn") or "TRN not provided", "customer_address": cust.get("address", ""),
            "po_number": f"PO-{rng.randint(1000, 9999)}", "delivery_note_no": "", "reference_no": "",
            "date": d.isoformat(), "due_date": (d + timedelta(days=30)).isoformat(),
            "subtotal": sub, "vat_amount": vat, "total": money(sub + vat), "status": rng.choice(["Sent", "Sent", "Paid"]),
            "source": "Manual", "lines": lines, "notes": "Demo data",
        }
        saved += attempt(f"invoice {inv['invoice_no']}", lambda inv=inv: api.save("salesInvoices", inv))
    print(f"Sales invoices: {saved}")

    if failures:
        print(f"\n{len(failures)} item(s) failed:")
        for f in failures[:30]:
            print("  -", f)
        return 2
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
