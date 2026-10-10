"""Sample data for one company, filled through the app's own API (Settings > Data > Fill demo
data, POST /api/v1/demo-data).

Every record is created with the same API calls the screens make, as the signed-in admin,
in-process (FastAPI's TestClient on the running app), so ledger postings, VAT, stock,
payroll and audit logs all run normally; nothing is written to the database directly.
Keys are stable ("DEMO-..."), so running it again updates the same records instead of
adding copies. Runs as a background job (app/background.py); the result lists how many
of each kind were saved and any that failed.

What it fills: 4 branches, 10 employees (full HR details) across them, departments'
shifts, this week's rota, 10 working days of attendance, holidays, leave (2 approved,
1 pending), overtime, a loan and a salary advance, tasks, last month's payroll run;
sales categories and units, 8 customers, 6 suppliers, 15 products, 10 purchases (they
stock every product before anything is sold), 4 bills, 12 sales invoices, 4 quotations,
customer receipts and supplier payments against them, 6 expenses, a bank account with
statement lines, and 2 manual journal entries.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any, Callable

TAG = "DEMO"
VAT = 5.0

BRANCHES = [
    ("Dubai Marina", "DXBMAR", "Dubai", "Marina Plaza, Office 1204"),
    ("Deira", "DXBDEI", "Dubai", "Al Maktoum Road, Building 7"),
    ("Mussafah", "AUHMUS", "Abu Dhabi", "Mussafah M-10, Warehouse 3"),
    ("Sharjah Industrial", "SHJIND", "Sharjah", "Industrial Area 6, Unit 22"),
]
DEPARTMENTS = [
    ("Management", "Branch Manager"), ("Sales", "Sales Executive"), ("Sales", "Account Manager"),
    ("Finance", "Accountant"), ("Operations", "Warehouse Supervisor"), ("Operations", "Storekeeper"),
    ("Operations", "Driver"), ("HR", "HR Officer"), ("IT", "IT Support"), ("Sales", "Sales Coordinator"),
]
PEOPLE = [
    ("Ahmed", "Al Mansoori", "Male", "United Arab Emirates"), ("Fatima", "Khan", "Female", "Pakistan"),
    ("Rahul", "Nair", "Male", "India"), ("Maria", "Santos", "Female", "Philippines"),
    ("Omar", "Haddad", "Male", "Jordan"), ("Priya", "Menon", "Female", "India"),
    ("Yousef", "Farouk", "Male", "Egypt"), ("Aisha", "Al Zaabi", "Female", "United Arab Emirates"),
    ("Joseph", "Fernandes", "Male", "India"), ("Sara", "Rahman", "Female", "Sri Lanka"),
]
SALARIES = [14000, 7000, 8000, 6500, 6000, 3500, 2800, 6500, 6000, 5000]
CUSTOMERS = [
    ("Al Noor Trading LLC", "Dubai"), ("Blue Pearl Logistics FZE", "Dubai"), ("Gulf Star Contracting LLC", "Abu Dhabi"),
    ("Emirates Facility Services LLC", "Sharjah"), ("Desert Line Supplies LLC", "Dubai"),
    ("Royal Horizon Retail Group LLC", "Abu Dhabi"), ("Union Tech Solutions FZE", "Sharjah"), ("Palm View Trading LLC", "Ajman"),
]
SUPPLIERS = [
    ("Atlas Building Supplies LLC", "Materials"), ("Bright Electrical Wholesale LLC", "Electrical"),
    ("SafePro Equipment LLC", "Safety"), ("Oasis Office Furniture LLC", "Furniture"),
    ("Vertex IT Solutions FZE", "IT"), ("Al Massa Packaging LLC", "Packaging"),
]
PRODUCTS = [
    ("Rebar 12mm Grade 60", "Materials", "Ton", 3900), ("Cement Bag 50kg", "Materials", "Bag", 18),
    ("Plywood Sheet 18mm", "Materials", "PCS", 65), ("Copper Cable 4mm", "Electrical", "Roll", 410),
    ("LED Panel Light 60x60", "Electrical", "PCS", 85), ("Circuit Breaker 32A", "Electrical", "PCS", 48),
    ("Hard Hat Class E", "Safety", "PCS", 25), ("Nitrile Work Gloves", "Safety", "Pair", 9), ("Hi-Vis Vest", "Safety", "PCS", 14),
    ("Office Chair Ergonomic", "Furniture", "PCS", 320), ("Office Desk 140cm", "Furniture", "PCS", 690),
    ("Laptop 14in i5", "IT", "PCS", 2650), ("Network Switch 24-port", "IT", "PCS", 780),
    ("Packing Carton Large", "Packaging", "PCS", 4), ("Stretch Film Roll", "Packaging", "Roll", 22),
]
SUPPLIER_OF = {"Materials": 0, "Electrical": 1, "Safety": 2, "Furniture": 3, "IT": 4, "Packaging": 5}
SHIFTS = [("Morning", "M", "08:00", "17:00", 60), ("Evening", "E", "14:00", "23:00", 60), ("Night", "N", "22:00", "07:00", 60)]


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


class DemoSeeder:
    def __init__(self, call: Callable[[str, str, Any], Any], today: date | None = None, seed: int = 42):
        """call(method, path, json_body) -> parsed JSON; raises RuntimeError on an error answer."""
        self.call = call
        self.today = today or date.today()
        self.rng = random.Random(seed)
        self.counts: dict[str, int] = {}
        self.failures: list[str] = []

    # ── helpers ──────────────────────────────────────────────────────────────

    def _try(self, kind: str, label: str, fn: Callable[[], Any]) -> Any:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 -- reported in the job result, the rest carries on
            self.failures.append(f"{kind} {label}: {str(exc)[:200]}")
            return None
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return result

    def save(self, kind: str, collection: str, record: dict, label: str = "") -> Any:
        return self._try(kind, label or str(record.get("id") or record.get("ref") or record.get("name") or ""),
                         lambda: self.call("POST", "/app-data?action=save", {"collection": collection, "record": record}))

    def _trn(self) -> str:
        return "100" + "".join(str(self.rng.randint(0, 9)) for _ in range(12))

    def _day(self, days_ago: int) -> str:
        return (self.today - timedelta(days=days_ago)).isoformat()

    # ── steps ────────────────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        branches = self.branches()
        employees = self.employees(branches)
        self.hr(employees)
        self.reference_lists()
        customers, products = self.parties_and_products()
        self.purchases_and_bills(products)
        invoices = self.sales(customers, products)
        self.payments(invoices)
        self.expenses()
        self.bank_and_journals()
        self.payroll()
        return {"counts": self.counts, "failures": self.failures[:50], "failed": len(self.failures)}

    def branches(self) -> dict[str, str]:
        existing = {b["name"].lower(): b["id"] for b in (self.call("GET", "/branches", None) or [])}
        ids: dict[str, str] = {}
        for name, code, city, address in BRANCHES:
            if name.lower() in existing:
                ids[name] = existing[name.lower()]
                self.counts["branches"] = self.counts.get("branches", 0) + 1
                continue
            made = self._try("branches", name, lambda name=name, code=code, city=city, address=address: self.call(
                "POST", "/branches", {"name": name, "code": code, "city": city, "address": address,
                                      "country": "United Arab Emirates", "currency": "AED", "status": "Active"}))
            if made:
                ids[name] = made["id"]
        return ids

    def employees(self, branches: dict[str, str]) -> list[dict]:
        names = list(branches) or [b[0] for b in BRANCHES]
        out = []
        for i, ((first, last, gender, nationality), (dept, title), salary) in enumerate(zip(PEOPLE, DEPARTMENTS, SALARIES)):
            branch = names[i % len(names)]
            emp_id = f"{TAG}-E{i + 1:03d}"
            email = f"{first}.{last}".lower().replace(" ", "") + "@demo-staff.ae"
            joined = self.today - timedelta(days=200 + i * 97)
            emp = {
                "id": emp_id, "name": f"{first} {last}", "first_name": first, "last_name": last,
                "email": email, "mobile": f"+97150{1000000 + i * 7311}", "phone": f"+97150{1000000 + i * 7311}",
                "department": dept, "designation": title, "role": title, "gender": gender, "nationality": nationality,
                "dob": (self.today - timedelta(days=365 * (26 + i) + 40 * i)).isoformat(),
                "marital_status": "Married" if i % 2 else "Single",
                "join_date": joined.isoformat(), "contract": "Full-Time", "contract_type": "Unlimited",
                "probation_end": (joined + timedelta(days=180)).isoformat(),
                "salary": salary, "housing_allowance": _money(salary * 0.25), "transport_allowance": 800,
                "other_allowance": 300 if i % 3 == 0 else 0,
                "salary_bank": ["ENBD", "ADCB", "FAB", "Mashreq"][i % 4],
                "iban": "AE07" + f"{33 + i:02d}" + "".join(str((i * 7 + k) % 10) for k in range(17)),
                "bank_account": f"10{i:02d}{i * 1234 % 100000:05d}{i:03d}",
                "location": branch, "branch": branch, "branch_id": branches.get(branch, ""),
                "status": "Active", "shift_hours_type": "weekly", "shift_hours": 48, "leave_policy": "Standard",
                "annual_leave_days": 30, "emirates_id": f"784-19{80 + i}-{1234567 + i * 1111}-{i % 10}",
                "emirates_id_expiry": (self.today + timedelta(days=300 + i * 30)).isoformat(),
                "passport_no": f"P{7000000 + i * 4321}", "passport_expiry": (self.today + timedelta(days=900 + i * 40)).isoformat(),
                "visa_no": f"201/{2024 - i % 3}/{55000 + i * 17}", "visa_expiry": (self.today + timedelta(days=500 + i * 25)).isoformat(),
                "labour_card_no": f"LC{9100000 + i * 37}", "labour_card_expiry": (self.today + timedelta(days=480 + i * 20)).isoformat(),
                "medical_insurance": "Daman Enhanced", "insurance_expiry": (self.today + timedelta(days=200 + i * 15)).isoformat(),
                "address": f"Flat {100 + i}, {['Al Barsha', 'Deira', 'Mussafah', 'Al Nahda'][i % 4]}",
                "emergency_contact": f"{['Hassan', 'Layla', 'Arun', 'Grace'][i % 4]} (family) +97155{2000000 + i * 911}",
                "notes": "Demo employee",
            }
            if self.save("employees", "employees", emp, emp_id):
                out.append(emp)
        return out

    def hr(self, employees: list[dict]) -> None:
        for name, code, start, end, brk in SHIFTS:
            hours = ((int(end[:2]) - int(start[:2])) % 24) - brk / 60
            self.save("shifts", "rotaShifts", {"id": f"{TAG}-{code}", "name": name, "code": f"{TAG}-{code}", "start": start,
                                                "end": end, "break_minutes": brk, "hours": hours, "grace": "10",
                                                "ot_after": "9", "departments": [], "status": "Active"})
        week_start = self.today - timedelta(days=self.today.weekday())
        for emp in employees:
            for d in range(7):
                day = week_start + timedelta(days=d)
                off = d == 4  # Friday off
                name, code, start, end, brk = SHIFTS[0 if emp["department"] != "Operations" else (d % 2)]
                self.save("rota", "rotaAssignments", {
                    "id": f"{emp['id']}-{day.isoformat()}", "employee_id": emp["id"], "employee_name": emp["name"],
                    "role": emp["designation"], "department": emp["department"], "location": emp["location"],
                    "date": day.isoformat(), "day": day.strftime("%a"), "type": "Off" if off else name,
                    "code": "OFF" if off else code, "start": "" if off else start, "end": "" if off else end,
                    "mark": "Off" if off else code, "className": "off" if off else name.lower(),
                    "break_minutes": 0 if off else brk, "notes": "", "tasks": [], "status": "Published"}, f"{emp['id']} {day}")
        # Attendance: the last 10 working days, through the same import the device bridge uses.
        rows = []
        for emp in employees:
            n = 0
            back = 1
            while n < 10:
                day = self.today - timedelta(days=back)
                back += 1
                if day.weekday() == 4:
                    continue
                n += 1
                late = self.rng.choice([0, 0, 0, 5, 12])
                rows.append({"employee_id": emp["id"], "employee_name": emp["name"], "work_date": day.isoformat(),
                             "clock_in_1": f"08:{late:02d}", "clock_out_1": f"17:{self.rng.choice([0, 5, 20, 45]):02d}"})
        self._try("attendance_days", "import", lambda: self.call("POST", "/attendance/import-rows", {"rows": rows}))
        self.save("holidays", "hrHolidays", {"id": f"{TAG}-HOL-NATIONAL", "date": f"{self.today.year}-12-02",
                                             "display_date": f"2 Dec {self.today.year}", "name": "UAE National Day",
                                             "location": "All", "paid": True, "status": "Active"})
        self.save("holidays", "hrHolidays", {"id": f"{TAG}-HOL-COMMEMORATION", "date": f"{self.today.year}-12-01",
                                             "display_date": f"1 Dec {self.today.year}", "name": "Commemoration Day",
                                             "location": "All", "paid": True, "status": "Active"})
        emp_rows = {e["employee_no"]: e["id"] for e in self._employee_rows()}
        # Leave already filed by an earlier run (same employee and start day) is kept as is.
        filed = {(r.get("employee_id"), str(r.get("start_date"))[:10]) for r in (self._get("/leave/requests") or [])
                 if isinstance(r, dict)}
        for i, (emp, leave_type, start, days, approve) in enumerate([
            (employees[1] if len(employees) > 1 else None, "Annual Leave", 20, 3, True),
            (employees[4] if len(employees) > 4 else None, "Sick Leave", 8, 1, True),
            (employees[7] if len(employees) > 7 else None, "Annual Leave", -14, 5, False),
        ]):
            if not emp or emp["id"] not in emp_rows:
                continue
            first = self.today - timedelta(days=start)
            if (emp_rows[emp["id"]], first.isoformat()) in filed:
                self.counts["leave_requests"] = self.counts.get("leave_requests", 0) + 1
                continue
            made = self._try("leave_requests", emp["id"], lambda emp=emp, first=first, days=days, leave_type=leave_type: self.call(
                "POST", "/leave/requests", {"employee_id": emp_rows[emp["id"]], "leave_type": leave_type,
                                            "start_date": first.isoformat(),
                                            "end_date": (first + timedelta(days=days - 1)).isoformat(),
                                            "reason": "Demo leave request"}))
            if made and approve:
                self._try("leave_approved", emp["id"], lambda made=made: self.call("POST", f"/leave/requests/{made['id']}/approve", {}))
        for i, emp in enumerate(employees[:4]):
            hours = [2, 1.5, 3, 2.5][i]
            self.save("overtime", "overtimeRequests", {
                "id": f"{TAG}-OT-{i + 1}", "employee": emp["name"], "employee_id": emp["id"], "department": emp["department"],
                "date": self._day(3 + i), "shift": "Morning", "login": "08:00", "logout": f"{17 + int(hours)}:{'30' if hours % 1 else '00'}",
                "ot_hours": hours, "ot_type": "Normal", "multiplier": 1.25, "reason": "Month-end stock count",
                "status": "Approved" if i < 3 else "Pending", "submitted": f"{self._day(3 + i)}T18:00:00Z"})
        if len(employees) > 6:
            emp = employees[5]
            self.save("loans", "employeeLoans", {"id": f"{TAG}-LN-1", "employee": emp["name"], "employee_id": emp["id"],
                                                  "type": "Personal Loan", "amount": 6000, "emi": 1000, "months": 6,
                                                  "balance": 5000, "deduct_from": "Salary", "reason": "Family expenses",
                                                  "date": self._day(40), "status": "Approved"})
            emp = employees[6]
            self.save("salary_advances", "salaryAdvances", {"id": f"{TAG}-ADV-1", "employee": emp["name"], "employee_id": emp["id"],
                                                             "amount": 1500, "month": self.today.strftime("%Y-%m"),
                                                             "reason": "Rent deposit", "requested": f"{self._day(5)}T09:00:00Z",
                                                             "status": "Approved"})
        for i, (title, priority, status) in enumerate([
            ("Prepare monthly stock count", "High", "in_progress"), ("Update customer price list", "Medium", "todo"),
            ("Renew trade licence documents", "High", "todo"), ("Train new storekeeper on POS", "Low", "done"),
            ("Follow up overdue invoices", "High", "in_progress"), ("Arrange vehicle service", "Medium", "todo"),
        ]):
            emp = employees[i % len(employees)] if employees else {"id": "", "name": ""}
            self.save("tasks", "tasks", {"id": f"{TAG}-TASK-{i + 1}", "title": title, "description": f"{title} (demo task)",
                                         "assigned_to": emp["id"], "assigned_to_name": emp["name"], "priority": priority,
                                         "due_date": self._day(-3 - i), "status": status,
                                         "progress": 100 if status == "done" else (50 if status == "in_progress" else 0),
                                         "repeat": "none", "created_at": f"{self._day(10)}T08:00:00Z"})

    def _employee_rows(self) -> list[dict]:
        """Employee table rows (id + employee_no): leave requests take the table id."""
        rows = self._get("/payroll/employees") or []
        return rows if isinstance(rows, list) else []

    def reference_lists(self) -> None:
        for name in ("Materials", "Electrical", "Safety", "Furniture", "IT", "Packaging"):
            self.save("sales_categories", "salesCategories", {"name": name, "scope": "Sales & Purchase",
                                                               "vat": "Standard 5%", "status": "Active"})
        for code, name in (("PCS", "Pieces"), ("BOX", "Box"), ("TON", "Ton"), ("BAG", "Bag"), ("ROLL", "Roll"), ("PAIR", "Pair")):
            self.save("sales_units", "salesUnits", {"code": code, "name": name, "type": "Quantity", "decimals": "2", "status": "Active"})

    def parties_and_products(self) -> tuple[list[dict], list[dict]]:
        customers = []
        for i, (name, emirate) in enumerate(CUSTOMERS):
            c = {"name": name, "trn": self._trn(), "emirate": emirate,
                 "address": f"Office {101 + i}, {['Business Bay', 'Deira', 'Khalifa City', 'Al Majaz'][i % 4]}, {emirate}",
                 "email": f"accounts@{name.split()[0].lower()}{i + 1}.example.ae", "phone": f"+9714{3300000 + i * 917}",
                 "contact_person": ["Khalid", "Reem", "Imran", "Grace"][i % 4], "credit_limit": 50000 + i * 10000,
                 "payment_terms": "Net 30", "status": "Active"}
            if self.save("customers", "customers", c, name):
                customers.append(c)
        for i, (name, category) in enumerate(SUPPLIERS):
            self.save("suppliers", "vendors", {"name": name, "trn": self._trn(), "category": category,
                                                "email": f"sales@{name.split()[0].lower()}.example.ae",
                                                "phone": f"+9714{8800000 + i * 523}", "address": f"Warehouse {i + 1}, Jebel Ali",
                                                "contact_person": ["Sameer", "Noura", "Victor", "Hind"][i % 4],
                                                "payment_terms": "Net 30", "status": "Active"}, name)
        products = []
        for i, (name, category, unit, cost) in enumerate(PRODUCTS):
            p = {"code": f"{TAG}-P{i + 1:03d}", "name": name, "type": "Stock Item", "category": category, "unit": unit,
                 "description": f"{name} (demo item)", "cost": cost, "selling_price": _money(cost * 1.9), "price": _money(cost * 1.9),
                 "vat": "Standard 5%", "tracking": "Yes", "supplier_name": SUPPLIERS[SUPPLIER_OF[category]][0],
                 "reorder_level": 10, "min_stock": 5, "max_stock": 500, "opening_date": self._day(120), "status": "Active"}
            if self.save("products", "products", p, p["code"]):
                products.append(p)
        return customers, products

    def purchases_and_bills(self, products: list[dict]) -> None:
        self.stock = {p["code"]: 0 for p in products}
        for i in range(10):
            picks = products[i::10][:2] if i < 8 else self.rng.sample(products, 2)
            supplier = SUPPLIERS[SUPPLIER_OF[picks[0]["category"]]][0] if picks else SUPPLIERS[0][0]
            d = self.today - timedelta(days=90 - i * 3)
            lines, net = [], 0.0
            for p in picks:
                qty = 200 if p["cost"] < 50 else (60 if p["cost"] < 500 else 15)
                self.stock[p["code"]] += qty
                total = _money(qty * p["cost"])
                net += total
                lines.append({"product": p["name"], "sku": p["code"], "quantity": qty, "unit_of_measure": p["unit"],
                              "unit_cost": p["cost"], "discount_percent": 0, "unit_cost_before_tax": p["cost"], "line_total": total})
            net = _money(net)
            tax = _money(net * VAT / 100)
            total = _money(net + tax)
            paid = total if i % 3 == 0 else 0.0
            self.save("purchases", "purchaseRecords", {
                "ref": f"{TAG}-PUR-{i + 1:03d}", "supplier": supplier, "date": d.isoformat(),
                "status": "Paid" if paid else "Pending Payment", "location": "Main Store", "pay_term": "Net 30",
                "due_date": (d + timedelta(days=30)).isoformat(), "items": sum(l["quantity"] for l in lines),
                "net_amount": net, "discount": 0, "tax_amount": tax, "shipping": 0, "additional_expenses": [],
                "additional_expense_amount": 0, "total": total, "paid": paid, "due": _money(total - paid),
                "discount_type": "None", "discount_value": 0, "tax_type": "VAT 5%", "lines": lines,
                "payment_method": "Bank Transfer" if paid else "Credit", "payment_account": "None", "payment_note": "",
                "paid_on": d.isoformat() if paid else "", "notes": "Demo purchase", "source": "Manual",
                "document_type": "Purchase Invoice"})
        for i, (desc, qty, price) in enumerate([("Office rent - month", 1, 10000), ("Warehouse forklift service", 2, 850),
                                                ("Internet & phone - month", 1, 1200), ("Uniforms for staff", 10, 95)]):
            net = _money(qty * price)
            vat = _money(net * VAT / 100)
            self.save("bills", "bills", {"id": f"{TAG}-BILL-{i + 1}", "vendor": SUPPLIERS[(i + 3) % len(SUPPLIERS)][0],
                                         "bill_no": f"{TAG}-BILL-{i + 1:03d}", "date": self._day(40 - i * 8),
                                         "due": self._day(10 - i * 8), "notes": "Demo bill",
                                         "lines": [{"description": desc, "qty": qty, "unit_price": price, "vat_pct": VAT,
                                                    "net": net, "vat": vat, "total": _money(net + vat)}],
                                         "subtotal": net, "vat": vat, "total": _money(net + vat), "status": "Awaiting Payment"})

    def sales(self, customers: list[dict], products: list[dict]) -> list[dict]:
        invoices = []
        stock = getattr(self, "stock", {})
        bought = dict(stock)
        for i in range(12):
            cust = customers[i % len(customers)] if customers else {"name": "Walk-in Customer", "trn": "", "address": ""}
            d = self.today - timedelta(days=60 - i * 5)
            lines, sub = [], 0.0
            # Three products per invoice in rotation; each sale takes ~40% of what was bought,
            # so most stock sells over the 12 invoices (a business that turns its stock over).
            picks = [products[(i * 3 + k) % len(products)] for k in range(3)] if products else []
            for p in [x for x in picks if stock.get(x["code"], 0) > 0]:
                qty = min(stock[p["code"]], max(1, -(-bought[p["code"]] * 2 // 5)))
                stock[p["code"]] -= qty
                amount = _money(qty * p["selling_price"])
                sub += amount
                lines.append({"description": p["name"], "unit": p["unit"], "qty": qty, "price": p["selling_price"],
                              "unit_price": p["selling_price"], "amount": amount, "product_code": p["code"],
                              "product_name": p["name"], "price_source": "Product", "price_snapshot": p["selling_price"],
                              "tax_rate": VAT})
            if not lines:
                continue
            sub = _money(sub)
            vat = _money(sub * VAT / 100)
            inv = {"invoice_no": f"{TAG}-INV-{i + 1:04d}", "document_type": "Sales Invoice", "customer": cust["name"],
                   "customer_trn": cust.get("trn") or "", "customer_address": cust.get("address", ""),
                   "po_number": f"PO-{4100 + i}", "date": d.isoformat(), "due_date": (d + timedelta(days=30)).isoformat(),
                   # The first three are settled in full by the receipts below (payments()).
                   "subtotal": sub, "vat_amount": vat, "total": _money(sub + vat), "status": "Paid" if i < 3 else "Sent",
                   "salesperson": ["Fatima Khan", "Rahul Nair", "Sara Rahman"][i % 3], "source": "Manual",
                   "lines": lines, "notes": "Demo invoice"}
            if self.save("sales_invoices", "salesInvoices", inv, inv["invoice_no"]):
                invoices.append(inv)
        for i in range(4):
            cust = customers[(i + 2) % len(customers)] if customers else {"name": "Walk-in Customer"}
            p = products[i * 3 % len(products)] if products else {"name": "Item", "selling_price": 100, "unit": "PCS"}
            qty = 5 + i
            sub = _money(qty * p["selling_price"])
            vat = _money(sub * VAT / 100)
            self.save("quotations", "quotations", {
                "quote_no": f"{TAG}-QT-{i + 1:03d}", "customer": cust["name"], "date": self._day(15 - i * 3),
                "valid_until": self._day(-15 + i * 3), "subject": f"Quotation for {p['name']}",
                "subtotal": f"{sub:.2f}", "vat_amount": f"{vat:.2f}", "total": f"{_money(sub + vat):.2f}",
                "status": ["Sent", "Draft", "Accepted", "Sent"][i], "owner": "Sales Team",
                "lines": [{"description": p["name"], "qty": qty, "price": p["selling_price"], "amount": sub,
                           "product_code": p.get("code", ""), "unit": p["unit"]}]})
        return invoices

    def payments(self, invoices: list[dict]) -> None:
        for i, inv in enumerate(invoices[:5]):
            amount = inv["total"] if i < 3 else _money(inv["total"] / 2)
            self.save("receipts", "payments", {
                "type": "Customer Receipt", "ref": f"{TAG}-RCPT-{i + 1:03d}", "contact": inv["customer"], "amount": amount,
                "method": "Bank Transfer", "date": self._day(20 - i * 3), "bank": "ENBD Current", "comments": "Demo receipt",
                "detail": "", "document_ref": inv["invoice_no"],
                "allocations": [{"doc_ref": inv["invoice_no"], "amount": amount}]})
        for i in range(3):
            bill_no = f"{TAG}-BILL-{i + 1:03d}"
            vendor = SUPPLIERS[(i + 3) % len(SUPPLIERS)][0]
            amount = [10500, 1785, 1260][i]
            self.save("supplier_payments", "payments", {
                "type": "Supplier Payment", "ref": f"{TAG}-SPAY-{i + 1:03d}", "contact": vendor, "amount": amount,
                "method": "Bank Transfer", "date": self._day(12 - i * 3), "bank": "ENBD Current", "comments": "Demo supplier payment",
                "detail": "", "document_ref": bill_no, "allocations": [{"doc_ref": bill_no, "amount": amount}]})

    def expenses(self) -> None:
        for i, (category, vendor, desc, amount, status) in enumerate([
            ("Utilities", "DEWA", "Electricity & water - head office", 2350, "Approved"),
            ("Fuel", "ADNOC", "Delivery van fuel", 640, "Approved"),
            ("Office Supplies", "Oasis Office Furniture LLC", "Printer paper and toner", 410, "Approved"),
            ("Travel", "Emirates", "Supplier visit flight", 1850, "Pending"),
            ("Maintenance", "Atlas Building Supplies LLC", "Warehouse shelving repair", 980, "Approved"),
            ("Direct Expense - Freight", "Green Way Logistics FZE", "Inbound freight for steel order", 1200, "Pending"),
        ]):
            vat = _money(amount * VAT / 100)
            self.save("expenses", "expenses", {"ref": f"{TAG}-EXP-{i + 1:03d}", "date": self._day(30 - i * 4),
                                               "category": category, "vendor": vendor, "description": desc,
                                               "amount": amount, "vat_amount": vat, "total": _money(amount + vat), "status": status})

    def bank_and_journals(self) -> None:
        accounts = self.call("GET", "/accounts", None) or []
        by_code = {a.get("code"): a for a in accounts if isinstance(a, dict)}
        bank_ledger = by_code.get("1020") or by_code.get("1000")
        self.save("bank_accounts", "bankAccounts", {"id": "AE070331234567890123456", "bank": "Emirates NBD", "holder": "Company",
                                                    "iban": "AE070331234567890123456", "type": "Current", "currency": "AED",
                                                    "swift": "EBILAEAD", "branch": "Deira", "balance": 250000,
                                                    "opening_balance": 250000, "status": "Active"})
        if bank_ledger:
            existing = [b for b in (self._get("/bank-accounts") or []) if b.get("iban") == "AE070331234567890123456"]
            bank = existing[0] if existing else self._try("bank_accounts_ledger", "ENBD", lambda: self.call(
                "POST", "/bank-accounts", {"account_id": bank_ledger["id"], "bank_name": "Emirates NBD",
                                           "iban": "AE070331234567890123456", "account_number": "1234567890",
                                           "currency": "AED", "status": "active"}))
            if bank and not existing:
                for i, (narration, party, debit, credit) in enumerate([
                    ("Customer receipt Al Noor", "Al Noor Trading LLC", 0, 8190), ("DEWA bill", "DEWA", 2467.5, 0),
                    ("Salary transfer WPS", "Payroll", 42000, 0), ("Customer receipt Blue Pearl", "Blue Pearl Logistics FZE", 0, 5250),
                    ("Bank charges", "Emirates NBD", 52.5, 0), ("Supplier payment Atlas", "Atlas Building Supplies LLC", 31500, 0),
                ]):
                    d = self._day(25 - i * 4)
                    self._try("bank_lines", narration, lambda bank=bank, i=i, d=d, narration=narration, party=party, debit=debit, credit=credit: self.call(
                        "POST", "/bank-statement-lines", {"bank_account_id": bank["id"], "statement_date": d,
                                                          "transaction_date": d, "reference_no": f"{TAG}-BNK-{i + 1:03d}",
                                                          "narration": narration, "party_name": party,
                                                          "debit": debit, "credit": credit}))
        rent, cash, capital = by_code.get("6100"), by_code.get("1000"), by_code.get("2500")
        journal = self._get("/journal?limit=200")
        entries = journal.get("entries") or journal.get("items") or [] if isinstance(journal, dict) else (journal or [])
        existing_numbers = {j.get("entry_number") for j in entries if isinstance(j, dict)}
        for number, desc, debit_acc, credit_acc, amount in [
            (f"{TAG}-JV-001", "Owner capital introduced", cash, capital, 500000),
            (f"{TAG}-JV-002", "Prepaid rent recognised for the month", rent, cash, 10000),
        ]:
            if not (debit_acc and credit_acc) or number in existing_numbers:
                continue
            self._try("journals", number, lambda number=number, desc=desc, debit_acc=debit_acc, credit_acc=credit_acc, amount=amount: self.call(
                "POST", "/journal", {"entry_number": number, "entry_date": f"{self._day(50)}T09:00:00",
                                     "description": desc, "source_module": "manual",
                                     "lines": [{"account_id": debit_acc["id"], "description": desc, "debit": amount, "credit": 0},
                                               {"account_id": credit_acc["id"], "description": desc, "debit": 0, "credit": amount}]}))

    def _get(self, path: str) -> Any:
        try:
            return self.call("GET", path, None)
        except RuntimeError:
            return None

    def payroll(self) -> None:
        first = self.today.replace(day=1) - timedelta(days=1)
        period = first.strftime("%Y-%m")
        runs = self._get("/payroll/runs") or []
        if any(isinstance(r, dict) and r.get("period") == period for r in runs):
            self.counts["payroll_runs"] = self.counts.get("payroll_runs", 0) + 1
            return
        run = self._try("payroll_runs", period, lambda: self.call("POST", "/payroll/generate", {"period": period}))
        if run:
            self._try("payroll_approved", period, lambda: self.call("POST", f"/payroll/runs/{run['id']}/approve", {}))


def run_for_token(token: str) -> dict[str, Any]:
    """Fill the signed-in admin's company, calling the app in-process with their token."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {token}"}

    def call(method: str, path: str, body: Any) -> Any:
        r = client.request(method, "/api/v1" + path, headers=headers, json=body)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise RuntimeError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    return DemoSeeder(call).run()
