"""Demo data: fill one company with months of realistic records, for showing clients the app.

Started by Super Admin (POST /superadmin/companies/{id}/demo-data) and run in the
background. Everything goes through the app's own API, signed in as the company's admin
(an in-process client), so a demo invoice is posted to the ledger, a purchase moves stock,
an employee gets the SQL row payroll and ESS use, and so on -- exactly as if a person had
typed it. Attendance punches go through attendance_store, the same code the biometric
devices use.

What it adds (6 months by default, ending today): departments, shifts, public holidays,
~28 employees with full profiles and ESS logins, a published rota, daily attendance (some
late, some overtime), leave requests, overtime requests, loans and salary advances, payroll
for every finished month, tasks, announcements, recruitment; customers, suppliers, products,
bank accounts, quotations, sales invoices (with receipts: paid, part-paid, unpaid, overdue),
purchases (with supplier payments), and expenses.

It only adds: numbers already used in the company are skipped, nothing is deleted or
overwritten. Deterministic per company (seeded by its id), so a re-run gives the same names.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import app.cache as cache

log = logging.getLogger("taxflow")

_JOB_KEY = "tf:demo:job:{id}"
_jobs: dict[str, dict] = {}

ESS_PASSWORD = "Demo@2026"

# ── reference data ─────────────────────────────────────────────────────────────

DEPARTMENTS = [
    ("SAL", "Sales", "Customer sales, accounts and quotations"),
    ("OPS", "Operations", "Delivery, installation and service"),
    ("WHS", "Warehouse", "Stock, receiving and dispatch"),
    ("FIN", "Finance & Admin", "Accounts, VAT and administration"),
    ("HRD", "Human Resources", "People, payroll and recruitment"),
    ("TEC", "Technical Support", "IT support and maintenance contracts"),
]

# (first, last, gender, nationality)
PEOPLE = [
    ("Rahul", "Menon", "Male", "Indian"), ("Fatima", "Al Hammadi", "Female", "Emirati"),
    ("Ahmed", "Hassan", "Male", "Egyptian"), ("Maria", "Santos", "Female", "Filipino"),
    ("Imran", "Qureshi", "Male", "Pakistani"), ("Aisha", "Rahman", "Female", "Indian"),
    ("Omar", "Haddad", "Male", "Jordanian"), ("Priya", "Nair", "Female", "Indian"),
    ("Joseph", "Fernandes", "Male", "Indian"), ("Grace", "Mendoza", "Female", "Filipino"),
    ("Bilal", "Akhtar", "Male", "Pakistani"), ("Sara", "Khalil", "Female", "Lebanese"),
    ("Kamal", "Perera", "Male", "Sri Lankan"), ("Nisha", "Pillai", "Female", "Indian"),
    ("Yousef", "Saleh", "Male", "Syrian"), ("Rina", "Gurung", "Female", "Nepali"),
    ("Daniel", "Brooks", "Male", "British"), ("Hana", "Mostafa", "Female", "Egyptian"),
    ("Arjun", "Krishnan", "Male", "Indian"), ("Lea", "Villanueva", "Female", "Filipino"),
    ("Tariq", "Mahmood", "Male", "Pakistani"), ("Noura", "Al Suwaidi", "Female", "Emirati"),
    ("Suresh", "Kumar", "Male", "Indian"), ("Mariam", "Yousif", "Female", "Sudanese"),
    ("Anil", "Thapa", "Male", "Nepali"), ("Jessica", "Cruz", "Female", "Filipino"),
    ("Faisal", "Ibrahim", "Male", "Jordanian"), ("Deepa", "Varghese", "Female", "Indian"),
]

# department -> [(designation, salary range)] ; first entry is the head
ROLES = {
    "Sales": [("Sales Manager", (16000, 19000)), ("Senior Sales Executive", (8500, 10500)),
              ("Sales Executive", (6000, 7500)), ("Sales Executive", (6000, 7500)), ("Sales Coordinator", (5000, 6000))],
    "Operations": [("Operations Manager", (15000, 18000)), ("Installation Engineer", (7000, 9000)),
                   ("Installation Technician", (4500, 5500)), ("Driver", (3000, 3800)), ("Driver", (3000, 3800))],
    "Warehouse": [("Warehouse Supervisor", (7000, 8500)), ("Storekeeper", (4000, 5000)),
                  ("Warehouse Assistant", (2800, 3400)), ("Warehouse Assistant", (2800, 3400))],
    "Finance & Admin": [("Finance Manager", (17000, 20000)), ("Accountant", (8000, 9500)),
                        ("Accounts Assistant", (5000, 6000)), ("Receptionist", (4000, 4800))],
    "Human Resources": [("HR Manager", (14000, 17000)), ("HR Officer", (6500, 7500)), ("PRO", (5500, 6500))],
    "Technical Support": [("IT Support Lead", (12000, 14000)), ("Support Engineer", (7000, 8500)),
                          ("Support Engineer", (7000, 8500)), ("Helpdesk Agent", (4500, 5500)), ("Network Technician", (5500, 6500))],
}

# UAE public holidays (official / expected dates)
HOLIDAYS = [
    ("2026-01-01", "New Year's Day"), ("2026-03-19", "Eid al-Fitr"), ("2026-03-20", "Eid al-Fitr"),
    ("2026-03-21", "Eid al-Fitr"), ("2026-05-26", "Arafat Day"), ("2026-05-27", "Eid al-Adha"),
    ("2026-05-28", "Eid al-Adha"), ("2026-05-29", "Eid al-Adha"), ("2026-06-16", "Islamic New Year"),
    ("2026-08-25", "Prophet Muhammad's Birthday"), ("2026-12-01", "Commemoration Day"),
    ("2026-12-02", "UAE National Day"), ("2026-12-03", "UAE National Day"),
    ("2027-01-01", "New Year's Day"), ("2027-03-09", "Eid al-Fitr"), ("2027-03-10", "Eid al-Fitr"),
    ("2027-03-11", "Eid al-Fitr"),
]

CUSTOMER_NAMES = [
    "Al Futtaim Facilities LLC", "Emirates Logistics Hub FZE", "Gulf Medical Centre LLC", "Desert Rose Hotels LLC",
    "Bluewave Marine Services LLC", "Skyline Real Estate Brokers", "Al Noor Pharmacy Group", "Crescent Engineering Consultants",
    "Palm Vista Schools LLC", "Horizon Freight Forwarding LLC", "Silver Sands Restaurants LLC", "Oasis Dental Clinic",
    "Al Barsha Trading Co LLC", "Marina Fitness Clubs LLC", "Sharjah Packaging Industries", "Jebel Ali Steel Works FZE",
    "Golden Falcon Travel & Tourism", "Union Insurance Brokers LLC", "Al Qudra Contracting LLC", "Meraas Events Management",
    "Zenith Accounting Services", "Bright Minds Nursery LLC", "Al Wasl Auto Parts LLC", "Coral Bay Properties LLC",
    "Prime Legal Consultants", "Fujairah Fisheries Co", "Ajman Furniture Factory LLC", "Nakheel Landscaping Services",
    "Capital Security Services LLC", "Al Rawda Supermarket LLC", "Techwise Solutions FZ-LLC", "Royal Catering Services LLC",
    "Emirates Print House LLC", "Seven Seas Shipping LLC", "Al Mamzar Clinics LLC", "Greenline Waste Management",
    "Dubai Hills Interiors LLC", "Pearl Cleaning Services LLC", "Al Khail Fashion Trading", "Northstar Architects",
]
SUPPLIER_NAMES = [
    ("Dell Technologies FZ-LLC", "IT Hardware"), ("HP Middle East FZ-LLC", "IT Hardware"), ("Lenovo Gulf FZE", "IT Hardware"),
    ("Cisco Systems Gulf", "Networking"), ("Ingram Micro Gulf FZE", "Distribution"), ("Redington Gulf FZE", "Distribution"),
    ("Jumbo Electronics Co LLC", "Electronics"), ("Emirates Office Supplies LLC", "Office Supplies"),
    ("Al Ghurair Printing & Publishing", "Printing"), ("Canon Middle East FZ-LLC", "Printers"),
    ("Samsung Gulf Electronics FZE", "Electronics"), ("Microsoft Gulf FZ-LLC", "Software"),
    ("Schneider Electric FZE", "Power"), ("APC by Schneider Gulf", "Power"), ("Logitech Middle East", "Accessories"),
    ("Aramex Emirates LLC", "Logistics"), ("Emirates Transport", "Logistics"), ("du Telecom (EITC)", "Telecom"),
    ("Etisalat by e&", "Telecom"), ("DEWA", "Utilities"), ("ENOC Fuel", "Fuel"), ("Al Masaood Property Mgmt", "Rent"),
]
# (code, name, category, unit, cost, price)
PRODUCTS = [
    ("LAP-DL5440", "Dell Latitude 5440 Laptop i5 16GB", "Laptops", "PCS", 3150, 3999),
    ("LAP-HP840", "HP EliteBook 840 G10 i7 16GB", "Laptops", "PCS", 4250, 5299),
    ("LAP-TPE14", "Lenovo ThinkPad E14 Gen 5 i5", "Laptops", "PCS", 2650, 3349),
    ("LAP-MBA13", "Apple MacBook Air 13 M3 8GB", "Laptops", "PCS", 3700, 4499),
    ("DSK-OPT7010", "Dell OptiPlex 7010 Desktop i5", "Desktops", "PCS", 2350, 2999),
    ("DSK-HP400", "HP ProDesk 400 G9 Desktop", "Desktops", "PCS", 2150, 2749),
    ("MON-DL24", "Dell 24\" P2423 Monitor", "Monitors", "PCS", 560, 749),
    ("MON-DL27", "Dell 27\" P2723QE 4K Monitor", "Monitors", "PCS", 1180, 1499),
    ("MON-LG32", "LG 32\" UltraFine 4K Monitor", "Monitors", "PCS", 1450, 1849),
    ("PRN-HPM404", "HP LaserJet Pro M404dn Printer", "Printers", "PCS", 980, 1299),
    ("PRN-CNMF", "Canon i-SENSYS MF453dw Printer", "Printers", "PCS", 1350, 1749),
    ("TON-HP59A", "HP 59A Black Toner Cartridge", "Consumables", "PCS", 265, 349),
    ("TON-CN057", "Canon 057 Black Toner", "Consumables", "PCS", 230, 309),
    ("NET-C9200", "Cisco Catalyst 9200 24-port Switch", "Networking", "PCS", 5400, 6899),
    ("NET-C1100", "Cisco ISR 1100 Router", "Networking", "PCS", 2900, 3699),
    ("NET-UAP6", "Ubiquiti UniFi U6 Pro Access Point", "Networking", "PCS", 560, 729),
    ("NET-CAT6", "Cat6 UTP Cable 305m Box", "Networking", "BOX", 310, 429),
    ("UPS-APC1500", "APC Smart-UPS 1500VA", "Power", "PCS", 1850, 2399),
    ("UPS-APC3000", "APC Smart-UPS 3000VA Rack", "Power", "PCS", 4300, 5499),
    ("ACC-LGMX3", "Logitech MX Master 3S Mouse", "Accessories", "PCS", 290, 379),
    ("ACC-LGMK", "Logitech MK270 Keyboard & Mouse", "Accessories", "PCS", 72, 99),
    ("ACC-DOCK", "Dell WD19S USB-C Dock", "Accessories", "PCS", 690, 899),
    ("ACC-HSET", "Jabra Evolve2 40 Headset", "Accessories", "PCS", 260, 349),
    ("STO-SSD1T", "Samsung 990 PRO 1TB NVMe SSD", "Storage", "PCS", 360, 469),
    ("STO-NAS4", "Synology DS923+ 4-Bay NAS", "Storage", "PCS", 2250, 2899),
    ("SFT-M365B", "Microsoft 365 Business Standard (1 yr)", "Software", "LIC", 395, 499),
    ("SFT-WIN11P", "Windows 11 Pro License", "Software", "LIC", 690, 849),
    ("SFT-AVES", "ESET Endpoint Security (1 yr)", "Software", "LIC", 120, 169),
    ("OFF-A4BOX", "A4 Copy Paper 80gsm (5 reams)", "Office Supplies", "BOX", 62, 85),
    ("FUR-CHR-MESH", "Mesh Task Chair with Lumbar Support", "Furniture", "PCS", 420, 599),
    ("SRV-INSTALL", "On-site Installation & Setup", "Services", "HRS", 0, 250),
    ("SRV-AMC", "Annual Maintenance Contract (per device)", "Services", "YEAR", 0, 450),
    ("SRV-NETCFG", "Network Configuration Service", "Services", "HRS", 0, 300),
    ("SRV-SUPPORT", "Remote IT Support (monthly)", "Services", "MONTH", 0, 1200),
]
EXPENSE_TYPES = [
    # (category, vendor, description, (min, max), monthly?)
    ("Rent", "Al Masaood Property Mgmt", "Office & warehouse rent", (22000, 22000), True),
    ("Utilities", "DEWA", "Electricity & water", (2800, 4600), True),
    ("Telecom", "du Telecom (EITC)", "Business internet & phone lines", (1450, 1450), True),
    ("Telecom", "Etisalat by e&", "Mobile plans - staff", (2100, 2600), True),
    ("Fuel", "ENOC Fuel", "Delivery van fuel", (900, 1700), False),
    ("Office Supplies", "Emirates Office Supplies LLC", "Stationery & pantry", (300, 1200), False),
    ("Marketing", "Meraas Events Management", "Trade show / marketing", (2500, 9000), False),
    ("Travel", "Golden Falcon Travel & Tourism", "Client visit travel", (800, 3500), False),
    ("Logistics", "Aramex Emirates LLC", "Courier & delivery", (250, 1100), False),
    ("Repairs & Maintenance", "Pearl Cleaning Services LLC", "Office cleaning", (1800, 1800), True),
    ("Bank Charges", "Emirates NBD", "Bank charges", (90, 260), True),
]


# ── job state ──────────────────────────────────────────────────────────────────

def _save_job(job: dict) -> None:
    job["updated_at"] = datetime.now(UTC).isoformat()
    r = cache._redis()
    if r is not None:
        try:
            r.set(_JOB_KEY.format(id=job["id"]), json.dumps(job), ex=7 * 86400)
            return
        except Exception:
            pass
    _jobs[job["id"]] = dict(job)


def get_job(job_id: str) -> dict | None:
    r = cache._redis()
    if r is not None:
        try:
            raw = r.get(_JOB_KEY.format(id=job_id))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return _jobs.get(job_id)


def start(company_id: str, months: int = 6) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "company_id": company_id, "months": months, "status": "running",
           "step": "Starting", "log": [], "summary": {}, "started_at": datetime.now(UTC).isoformat()}
    _save_job(job)
    threading.Thread(target=_run, args=(job,), name="demo-data", daemon=True).start()
    return job


# ── in-process API client, signed in as the company admin ─────────────────────

class _Api:
    def __init__(self, user_id: str):
        from starlette.testclient import TestClient

        from app.main import app
        from app.security import create_access_token
        self.client = TestClient(app, raise_server_exceptions=False, base_url="http://localhost")
        self.headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
        self.calls = 0

    def _call(self, method: str, path: str, **kw):
        for attempt in range(6):
            time.sleep(0.2)  # well under the 300 requests/minute per login
            self.calls += 1
            r = self.client.request(method, "/api/v1" + path, headers=self.headers, **kw)
            if r.status_code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return r
        return r

    def save(self, collection: str, record: dict):
        return self._call("POST", "/app-data", params={"action": "save"}, json={"collection": collection, "record": record})

    def bulk(self, collection: str, records: list[dict], size: int = 150) -> int:
        saved = 0
        for i in range(0, len(records), size):
            r = self._call("POST", "/app-data", params={"action": "bulk-save"},
                           json={"collection": collection, "records": records[i:i + size]})
            if r.status_code == 200:
                saved += int(r.json().get("saved") or 0)
            else:
                log.warning("demo bulk %s failed: %s %s", collection, r.status_code, r.text[:200])
        return saved

    def post(self, path: str, body: Any = None):
        return self._call("POST", path, json=body)

    def put(self, path: str, body: Any = None):
        return self._call("PUT", path, json=body)

    def get(self, path: str, **params):
        return self._call("GET", path, params=params)


# ── helpers ────────────────────────────────────────────────────────────────────

def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def _existing_keys(db, company_id: str, collection: str) -> set[str]:
    from app.models import AppDataRecord
    return {str(k).strip().lower() for (k,) in db.query(AppDataRecord.record_key).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == collection).all() if k}


def _numbers(prefix: str, count: int, used: set[str], start: int = 1001, width: int = 4) -> list[str]:
    """count document numbers prefix-NNNN not already used (case-insensitive)."""
    out, n = [], start
    while len(out) < count:
        key = f"{prefix}{n:0{width}d}"
        if key.lower() not in used:
            out.append(key)
        n += 1
    return out


def _working_days(first: date, last: date) -> list[date]:
    """Mon-Fri (UAE weekend Sat/Sun), public holidays excluded."""
    hol = {d for d, _ in HOLIDAYS}
    out, d = [], first
    while d <= last:
        if d.weekday() < 5 and d.isoformat() not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def _weighted_date(rng: random.Random, days: list[date]) -> date:
    """Later months a little busier (a growing business)."""
    i = int(len(days) * (rng.random() ** 0.8))
    return days[min(i, len(days) - 1)]


# ── the run ────────────────────────────────────────────────────────────────────

def _run(job: dict) -> None:
    from app.database import SessionLocal
    from app.models import Company, User

    db = SessionLocal()
    try:
        company = db.get(Company, job["company_id"])
        admin = db.query(User).filter(User.company_id == job["company_id"], User.role == "admin",
                                      User.is_active.is_(True)).order_by(User.created_at).first()
        if not company or not admin:
            raise RuntimeError("Company or its admin user not found")
        rng = random.Random(job["company_id"])
        api = _Api(admin.id)
        today = date.today()
        first = (today.replace(day=1) - timedelta(days=31 * (job["months"] - 1))).replace(day=1)
        ctx = {"db": db, "api": api, "rng": rng, "company": company, "today": today, "first": first, "job": job,
               "doc_dates": {}}
        steps = [
            ("HR setup: departments, shifts, holidays, leave policy", _hr_setup),
            ("Employees and ESS logins", _employees),
            ("Rota for every week", _rota),
            ("Leave requests", _leave),
            ("Attendance punches", _attendance),
            ("Overtime, loans, advances", _hr_requests),
            ("Payroll for each finished month", _payroll),
            ("Tasks, announcements, recruitment", _hr_extras),
            ("Customers, suppliers, products, bank accounts", _masters),
            ("Quotations", _quotations),
            ("Sales invoices and receipts", _sales),
            ("Purchases and supplier payments", _purchases),
            ("Expenses", _expenses),
            ("Dating the ledger, VAT and documents to their own dates", _backdate),
        ]
        from app.dependencies import company_allows_module
        off = [m for m in ("hrms", "ess", "sales", "purchase", "expense", "inventory", "accounting")
               if not company_allows_module(company.modules_enabled, m)]
        if off:
            job["log"].append("Note: modules switched off for this company, their data is skipped: " + ", ".join(off))
        for i, (label, fn) in enumerate(steps, 1):
            job["step"] = f"{i}/{len(steps)} {label}"
            _save_job(job)
            started = time.time()
            result = fn(ctx)
            job["summary"][label] = result
            job["log"].append(f"{label}: {result} ({time.time() - started:.0f} s)")
            _save_job(job)
        job["status"] = "done"
        job["step"] = "Finished"
        job["api_calls"] = api.calls
    except Exception as exc:
        log.exception("demo data failed")
        db.rollback()
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        db.close()
        job["finished_at"] = datetime.now(UTC).isoformat()
        _save_job(job)


# ── HR ─────────────────────────────────────────────────────────────────────────

def _hr_setup(ctx) -> str:
    db, api, company = ctx["db"], ctx["api"], ctx["company"]
    from app.models import AppDataRecord
    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == company.id, AppDataRecord.collection == "hr_settings",
                                         AppDataRecord.record_key == "dept-branch-role-config").first()
    current = []
    if row:
        try:
            current = json.loads(row.payload or "{}").get("departments") or []
        except (TypeError, ValueError):
            current = []
    have = {str(d.get("name", "")).lower() for d in current if isinstance(d, dict)}
    for code, name, desc in DEPARTMENTS:
        if name.lower() not in have:
            current.append({"id": f"dept-{code.lower()}", "name": name, "code": code, "head": "", "description": desc, "status": "Active"})
    cfg = {"id": "dept-branch-role-config", "departments": current}
    if row:
        try:
            cfg = {**json.loads(row.payload or "{}"), "departments": current}
        except (TypeError, ValueError):
            pass
    api.save("hr_settings", cfg)
    company.departments = json.dumps(current)
    db.commit()

    shifts = [
        {"code": "G", "name": "General Shift", "start": "09:00", "end": "18:00", "break_minutes": 60, "hours": "8", "grace": "10 minutes", "ot_after": "8 hours", "departments": [], "status": "Active"},
        {"code": "M", "name": "Morning Shift", "start": "07:00", "end": "16:00", "break_minutes": 60, "hours": "8", "grace": "10 minutes", "ot_after": "8 hours", "departments": [], "status": "Active"},
        {"code": "E", "name": "Evening Shift", "start": "13:00", "end": "22:00", "break_minutes": 60, "hours": "8", "grace": "10 minutes", "ot_after": "8 hours", "departments": [], "status": "Active"},
    ]
    used = _existing_keys(db, company.id, "rotaShifts")
    added_shifts = api.bulk("rotaShifts", [s for s in shifts if s["code"].lower() not in used])

    used = _existing_keys(db, company.id, "hrHolidays")
    hol = [{"id": f"hol-{d}", "date": d, "name": n, "location": "All branches", "paid": True, "type": "Public Holiday"}
           for d, n in HOLIDAYS if f"hol-{d}" not in used]
    added_hol = api.bulk("hrHolidays", hol)

    api.save("hr_settings", {"id": "weekend-policy-config", "mode": "sat_sun"})
    api.save("hr_settings", {"id": "late-rules-config", "startTime": "09:00", "graceMinutes": 10})
    api.save("hr_settings", {"id": "ot-rules-config", "standardHours": 8, "otCooloffMinutes": 30, "otRate": 1.25, "weekendRate": 1.5})
    return f"{len(DEPARTMENTS)} departments, {added_shifts} shifts, {added_hol} holidays, weekend Sat/Sun"


def _employees(ctx) -> str:
    db, api, rng, company, first = ctx["db"], ctx["api"], ctx["rng"], ctx["company"], ctx["first"]
    from app.models import Employee
    existing_nos = {e.employee_no for e in db.query(Employee.employee_no).filter(Employee.company_id == company.id).all()}
    n = 1001
    people = list(PEOPLE)
    records, plan = [], []
    p = 0
    for dept, roles in ROLES.items():
        for title, (lo, hi) in roles:
            if p >= len(people):
                break
            fn, ln, gender, nat = people[p]
            p += 1
            while f"EMP-{n}" in existing_nos:
                n += 1
            emp_no = f"EMP-{n}"
            n += 1
            head = title == roles[0][0]
            salary = rng.randrange(lo, hi + 1, 250)
            # most joined before the demo window, a few during it (new joiners)
            joined = first - timedelta(days=rng.randint(60, 1500)) if rng.random() < 0.85 else first + timedelta(days=rng.randint(5, 100))
            dob = date(rng.randint(1978, 2000), rng.randint(1, 12), rng.randint(1, 28))
            visa = ctx["today"] + timedelta(days=rng.choice([rng.randint(15, 60), rng.randint(120, 900)]))
            uname = f"{fn}.{ln.split()[-1]}".lower()
            records.append({
                "id": emp_no, "name": f"{fn} {ln}", "nickname": fn, "department": dept, "designation": title,
                "status": "Active", "contract": "Full-Time", "gender": gender, "nationality": nat,
                "dob": dob.isoformat(), "join_date": joined.isoformat(), "marital_status": rng.choice(["Married", "Single"]),
                "email": f"{uname}@etaxflowdemo.ae", "mobile": f"+9715{rng.choice('0258')}{rng.randint(1000000, 9999999)}",
                "address": rng.choice(["Al Barsha, Dubai", "Al Nahda, Dubai", "Deira, Dubai", "Al Qusais, Dubai",
                                       "Al Taawun, Sharjah", "International City, Dubai", "JVC, Dubai", "Karama, Dubai"]),
                "salary": salary, "housing_allowance": round(salary * 0.25 / 50) * 50,
                "transport_allowance": 500 if salary < 8000 else 1000, "other_allowance": 0 if salary < 6000 else 300,
                "salary_bank": rng.choice(["Emirates NBD", "ADCB", "Mashreq", "FAB", "RAKBANK"]),
                "iban": f"AE{rng.randint(10, 99)}0{rng.randint(10, 99)}{rng.randint(10**15, 10**16 - 1)}",
                "passport_no": f"{nat[:1]}{rng.randint(1000000, 9999999)}", "passport_expiry": (ctx["today"] + timedelta(days=rng.randint(200, 2500))).isoformat(),
                "emirates_id": f"784-{dob.year}-{rng.randint(1000000, 9999999)}-{rng.randint(1, 9)}",
                "eid_expiry": visa.isoformat(), "visa_expiry": visa.isoformat(),
                "labor_card": f"{rng.randint(10**7, 10**8 - 1)}", "labor_card_expiry": visa.isoformat(),
                "work_permit_no": f"WP{rng.randint(10**6, 10**7 - 1)}",
                "insurance_type": rng.choice(["Basic", "Enhanced"]), "insurance_policy": f"DXB-{rng.randint(100000, 999999)}",
                "insurance_expiry": (ctx["today"] + timedelta(days=rng.randint(30, 360))).isoformat(),
                "emergency_contact": rng.choice(["Spouse", "Brother", "Father", "Sister"]),
                "emergency_mobile": f"+9715{rng.randint(10000000, 99999999)}", "location": "Head Office - Dubai",
                "shift_hours": "8", "overtime_rate": "1.25", "leave_policy": "Standard",
            })
            plan.append((emp_no, uname, dept, title, head))
    saved = api.bulk("employees", records, size=50)
    db.expire_all()

    # department heads
    heads = {dept: f"{rec['name']}" for rec, (_, _, dept, _, head) in zip(records, plan) if head}
    from app.models import AppDataRecord
    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == company.id, AppDataRecord.collection == "hr_settings",
                                         AppDataRecord.record_key == "dept-branch-role-config").first()
    if row:
        try:
            cfg = json.loads(row.payload or "{}")
            for d in cfg.get("departments") or []:
                if isinstance(d, dict) and d.get("name") in heads and not d.get("head"):
                    d["head"] = heads[d["name"]]
            api.save("hr_settings", cfg)
        except (TypeError, ValueError):
            pass

    logins = 0
    by_no = {e.employee_no: e for e in db.query(Employee).filter(Employee.company_id == company.id).all()}
    ctx["employees"] = []
    for emp_no, uname, dept, title, head in plan:
        emp = by_no.get(emp_no)
        if not emp:
            continue
        ctx["employees"].append({"no": emp_no, "id": emp.id, "name": emp.full_name, "dept": dept, "title": title,
                                 "join": next(r["join_date"] for r in records if r["id"] == emp_no)})
        if not emp.username:
            # ESS usernames are unique across every company: if the plain name is taken,
            # try the name with a number.
            for candidate in [uname] + [f"{uname}{k}" for k in range(2, 10)]:
                r = api.put(f"/hr/admin/employees/{emp.id}/portal-access",
                            {"username": candidate, "password": ESS_PASSWORD, "is_active": True})
                if r.status_code == 200:
                    logins += 1
                    ctx.setdefault("ess_logins", []).append(candidate)
                    break
                if r.status_code not in (400, 409):
                    break
    sample = ", ".join(ctx.get("ess_logins", [])[:3])
    return f"{saved} employees, {logins} ESS logins (password {ESS_PASSWORD}; e.g. {sample})"


def _shift_for(emp: dict, day: date, rng: random.Random) -> str | None:
    """Rota code for the day, or None for a day off."""
    if emp["dept"] in ("Operations", "Warehouse", "Technical Support"):
        # 6-day rota with one rotating weekday off, alternating morning/evening weeks
        if day.weekday() == 6 or day.weekday() == (hash(emp["no"]) % 5 + day.isocalendar()[1]) % 6:
            return None
        return "M" if (day.isocalendar()[1] + hash(emp["no"])) % 2 else "E"
    return None if day.weekday() >= 5 else "G"


_SHIFT_TIMES = {"G": ("09:00", "18:00"), "M": ("07:00", "16:00"), "E": ("13:00", "22:00")}
_SHIFT_TYPE = {"G": "General", "M": "Morning", "E": "Evening"}


def _rota(ctx) -> str:
    api, rng, today, first = ctx["api"], ctx["rng"], ctx["today"], ctx["first"]
    hol = {d for d, _ in HOLIDAYS}
    end = today + timedelta(days=13)  # two weeks ahead, published
    rows = []
    d = first - timedelta(days=first.weekday())
    while d <= end:
        for emp in ctx.get("employees", []):
            if d.isoformat() < emp["join"]:
                continue
            code = _shift_for(emp, d, rng)
            base = {"id": f"{emp['no']}-{d.isoformat()}", "employee_id": emp["no"], "employee_name": emp["name"],
                    "department": emp["dept"], "role": emp["title"], "date": d.isoformat(),
                    "status": "Published" if d <= today + timedelta(days=6) else "Draft"}
            if d.isoformat() in hol:
                rows.append({**base, "code": "PH", "mark": "Holiday", "type": "Holiday"})
            elif code is None:
                rows.append({**base, "code": "OFF", "mark": "Off", "type": "Off"})
            else:
                s, e = _SHIFT_TIMES[code]
                rows.append({**base, "code": code, "mark": "Shift", "type": _SHIFT_TYPE[code], "start": s, "end": e,
                             "break_minutes": 60})
        d += timedelta(days=1)
    saved = api.bulk("rotaAssignments", rows, size=200)
    ctx["rota"] = {(r["employee_id"], r["date"]): r for r in rows}
    return f"{saved} shifts over {(end - first).days // 7 + 1} weeks"


def _leave(ctx) -> str:
    api, rng, today, first = ctx["api"], ctx["rng"], ctx["today"], ctx["first"]
    ctx["leave_days"] = set()
    made = approved = 0
    for emp in ctx.get("employees", []):
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            kind = rng.choices(["Annual Leave", "Sick Leave", "Casual Leave", "Emergency Leave"], [5, 3, 2, 1])[0]
            length = {"Annual Leave": rng.randint(3, 10), "Sick Leave": rng.randint(1, 2),
                      "Casual Leave": 1, "Emergency Leave": rng.randint(1, 3)}[kind]
            start = first + timedelta(days=rng.randint(0, max(1, (today - first).days + 30)))
            if start.isoformat() < emp["join"]:
                continue
            while start.weekday() >= 5:
                start += timedelta(days=1)
            end = start + timedelta(days=length - 1)
            reason = {"Annual Leave": rng.choice(["Family vacation", "Annual trip home", "Personal travel"]),
                      "Sick Leave": rng.choice(["Fever", "Medical appointment", "Flu"]),
                      "Casual Leave": "Personal errand", "Emergency Leave": "Family emergency"}[kind]
            r = api.post("/leave/requests", {"employee_id": emp["id"], "leave_type": kind, "start_date": start.isoformat(),
                                             "end_date": end.isoformat(), "reason": reason})
            if r.status_code not in (200, 201):
                continue
            made += 1
            rid = r.json().get("id")
            future = start > today
            choice = "pending" if (future and rng.random() < 0.6) else rng.choices(["approve", "reject"], [9, 1])[0]
            if choice == "approve" and api.post(f"/leave/requests/{rid}/approve").status_code == 200:
                approved += 1
                d = start
                while d <= end:
                    ctx["leave_days"].add((emp["no"], d.isoformat()))
                    d += timedelta(days=1)
            elif choice == "reject":
                api.post(f"/leave/requests/{rid}/reject", {"reason": "Busy period - please reschedule"})
    return f"{made} requests, {approved} approved"


def _attendance(ctx) -> str:
    db, rng, company, today, first = ctx["db"], ctx["rng"], ctx["company"], ctx["today"], ctx["first"]
    from app import attendance_store
    from app.routers.attendance import _company_offset
    offset = _company_offset(db, company.id)
    now_local = datetime.now(UTC) + offset
    events = late = ot = 0
    for emp in ctx.get("employees", []):
        late_prone = rng.random() < 0.2
        d = first
        while d <= today:
            iso = d.isoformat()
            rota = ctx.get("rota", {}).get((emp["no"], iso))
            if (iso >= emp["join"] and rota and rota.get("mark") == "Shift"
                    and (emp["no"], iso) not in ctx.get("leave_days", set()) and rng.random() > 0.015):
                s_h, s_m = (int(x) for x in rota["start"].split(":"))
                e_h, e_m = (int(x) for x in rota["end"].split(":"))
                start = datetime(d.year, d.month, d.day, s_h, s_m)
                end = datetime(d.year, d.month, d.day, e_h, e_m)
                if rng.random() < (0.22 if late_prone else 0.05):
                    cin = start + timedelta(minutes=rng.randint(11, 50))
                    late += 1
                else:
                    cin = start - timedelta(minutes=rng.randint(0, 25)) + timedelta(seconds=rng.randint(0, 59))
                extra = 0
                if rng.random() < 0.12:
                    extra = rng.randint(45, 150)
                    ot += 1
                cout = end + timedelta(minutes=extra + rng.randint(-8, 20))
                for when, direction in ((cin, "in"), (cout, "out")):
                    if when > now_local.replace(tzinfo=None):
                        continue
                    attendance_store.upsert_attendance_event(
                        db, company_id=company.id, employee_id=emp["no"],
                        punch_time=(when - offset).replace(tzinfo=UTC), direction=direction, source="device")
                    events += 1
            d += timedelta(days=1)
        db.commit()
    return f"{events} punches, {late} late arrivals, {ot} overtime days"


def _hr_requests(ctx) -> str:
    db, api, rng, today, first = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"]
    used = _existing_keys(db, ctx["company"].id, "overtimeRequests")
    emps = ctx.get("employees", [])
    ot = []
    for i in range(min(40, len(emps) * 2)):
        emp = rng.choice([e for e in emps if e["dept"] in ("Operations", "Warehouse", "Technical Support")] or emps)
        day = first + timedelta(days=rng.randint(0, (today - first).days))
        rid = f"OT-{day:%y%m}-{i + 1:03d}"
        if rid.lower() in used:
            continue
        hours = rng.choice([1, 1.5, 2, 2.5, 3])
        ot.append({"id": rid, "employee_id": emp["no"], "employee": emp["name"], "department": emp["dept"],
                   "date": day.isoformat(), "hours": hours, "rate": 1.25,
                   "reason": rng.choice(["Urgent client installation", "Stock count", "Server migration after hours", "Month-end delivery rush"]),
                   "status": "Pending" if day > today - timedelta(days=7) else rng.choices(["Approved", "Rejected"], [6, 1])[0]})
    n_ot = api.bulk("overtimeRequests", ot)
    loans = []
    for i, emp in enumerate(rng.sample(emps, min(4, len(emps)))):
        amount = rng.choice([5000, 8000, 10000, 15000])
        months = rng.choice([5, 6, 10])
        loans.append({"id": f"LN-{1001 + i}", "employee_id": emp["no"], "employee": emp["name"], "department": emp["dept"],
                      "amount": amount, "installments": months, "monthly_deduction": _money(amount / months),
                      "start_date": (first + timedelta(days=rng.randint(0, 60))).isoformat(),
                      "reason": rng.choice(["Family wedding", "Medical expenses", "School fees", "Car down payment"]),
                      "status": "Active"})
    n_loans = api.bulk("employeeLoans", [l for l in loans if l["id"].lower() not in _existing_keys(db, ctx["company"].id, "employeeLoans")])
    adv = []
    for i, emp in enumerate(rng.sample(emps, min(5, len(emps)))):
        adv.append({"id": f"ADV-{1001 + i}", "employee_id": emp["no"], "employee": emp["name"], "department": emp["dept"],
                    "amount": rng.choice([1000, 1500, 2000, 3000]),
                    "date": (first + timedelta(days=rng.randint(0, (today - first).days))).isoformat(),
                    "reason": "Salary advance", "status": rng.choice(["Approved", "Approved", "Pending"])})
    n_adv = api.bulk("salaryAdvances", [a for a in adv if a["id"].lower() not in _existing_keys(db, ctx["company"].id, "salaryAdvances")])
    return f"{n_ot} overtime requests, {n_loans} loans, {n_adv} salary advances"


def _payroll(ctx) -> str:
    api, today, first = ctx["api"], ctx["today"], ctx["first"]
    runs = approved = 0
    m = first
    while (m.year, m.month) < (today.year, today.month):
        period = f"{m.year:04d}-{m.month:02d}"
        r = api.post("/payroll/generate", {"period": period})
        if r.status_code in (200, 201):
            runs += 1
            run_id = r.json().get("id")
            if run_id and api.post(f"/payroll/runs/{run_id}/approve").status_code == 200:
                approved += 1
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return f"{runs} payroll runs, {approved} approved"


def _hr_extras(ctx) -> str:
    db, api, rng, today, first, company = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"], ctx["company"]
    emps = ctx.get("employees", [])
    titles = ["Prepare Q-end stock count", "Install network at Gulf Medical Centre", "Renew trade license documents",
              "Follow up overdue invoices", "Update price list for laptops", "Visa renewal - 3 staff",
              "Quarterly VAT return preparation", "Onboard new sales executive", "Customer site survey - Palm Vista Schools",
              "Replace UPS batteries at Desert Rose Hotels", "Monthly payroll review", "Warehouse safety inspection",
              "AMC visit - Oasis Dental Clinic", "Configure firewall for Crescent Engineering", "Organise team iftar",
              "Prepare sales report for management", "Reconcile bank statement", "Order toner stock",
              "Train staff on new ERP", "Update emergency contact list"]
    used = _existing_keys(db, company.id, "tasks")
    tasks = []
    for i, title in enumerate(titles):
        emp = rng.choice(emps) if emps else None
        due = first + timedelta(days=rng.randint(20, (today - first).days + 20))
        status = "done" if due < today - timedelta(days=5) and rng.random() < 0.8 else rng.choice(["todo", "in_progress"])
        tid = f"TSK-{1001 + i}"
        if tid.lower() in used:
            continue
        tasks.append({"id": tid, "title": title, "description": title, "assigned_to": emp["id"] if emp else "",
                      "assignee_name": emp["name"] if emp else "", "department": emp["dept"] if emp else "",
                      "priority": rng.choice(["Low", "Medium", "High"]), "due_date": due.isoformat(), "status": status,
                      "created_at": (due - timedelta(days=rng.randint(3, 20))).isoformat()})
    n_tasks = api.bulk("tasks", tasks)
    ann = [
        ("Welcome to our new ESS portal", "Apply for leave, view payslips and check your rota from your phone."),
        ("Eid al-Adha holidays", "The office is closed for Eid al-Adha. Warehouse on-call rota is published."),
        ("Summer working hours", "From June to September the office closes at 5:30 PM on Fridays."),
        ("Health insurance renewal", "Please submit dependent documents to HR by the end of the month."),
        ("Quarterly town hall", "Join the town hall on Thursday at 4 PM in the main meeting room."),
    ]
    used = _existing_keys(db, company.id, "companyAnnouncements")
    announcements = [{"id": f"ANN-{1001 + i}", "title": t, "message": m, "body": m, "audience": "All Employees",
                      "date": (first + timedelta(days=int(i * (today - first).days / len(ann)))).isoformat(),
                      "status": "Published", "author": "HR Department"}
                     for i, (t, m) in enumerate(ann) if f"ANN-{1001 + i}".lower() not in used]
    n_ann = api.bulk("companyAnnouncements", announcements)
    reqs = [("Sales Executive", "Sales", 2), ("Support Engineer", "Technical Support", 1), ("Accountant", "Finance & Admin", 1)]
    used = _existing_keys(db, company.id, "jobRequisitions")
    req_rows = [{"id": f"REQ-{1001 + i}", "title": t, "position": t, "department": d, "openings": n,
                 "status": rng.choice(["Open", "Open", "In Progress"]), "posted_date": (today - timedelta(days=rng.randint(10, 60))).isoformat(),
                 "salary_range": "AED 6,000 - 9,000", "location": "Dubai"}
                for i, (t, d, n) in enumerate(reqs) if f"REQ-{1001 + i}".lower() not in used]
    n_req = api.bulk("jobRequisitions", req_rows)
    cand_names = ["Vikram Shetty", "Ayesha Siddiqui", "Mark Dela Cruz", "Hassan Al Amiri", "Sneha Iyer",
                  "Ramy Fathy", "Jocelyn Tan", "Karim Nasser", "Divya Raj", "Ali Raza"]
    used = _existing_keys(db, company.id, "candidates")
    cands = [{"id": f"CAN-{1001 + i}", "name": n, "position": reqs[i % len(reqs)][0], "requisition_id": f"REQ-{1001 + i % len(reqs)}",
              "email": n.lower().replace(" ", ".") + "@mail.com", "phone": f"+9715{rng.randint(10000000, 99999999)}",
              "stage": rng.choice(["Applied", "Screening", "Interview", "Offer", "Rejected"]),
              "experience": f"{rng.randint(2, 9)} years", "applied_date": (today - timedelta(days=rng.randint(3, 50))).isoformat()}
             for i, n in enumerate(cand_names) if f"CAN-{1001 + i}".lower() not in used]
    n_cand = api.bulk("candidates", cands)
    return f"{n_tasks} tasks, {n_ann} announcements, {n_req} job openings, {n_cand} candidates"


# ── finance ────────────────────────────────────────────────────────────────────

def _trn(rng) -> str:
    return f"100{rng.randint(10**11, 10**12 - 1)}"


def _masters(ctx) -> str:
    db, api, rng, company = ctx["db"], ctx["api"], ctx["rng"], ctx["company"]
    emirates = ["Dubai", "Dubai", "Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah"]
    used = _existing_keys(db, company.id, "customers")
    customers = []
    for name in CUSTOMER_NAMES:
        if name.lower() in used:
            continue
        em = rng.choice(emirates)
        customers.append({"name": name, "trn": _trn(rng), "email": f"accounts@{name.split()[0].lower()}{name.split()[1].lower()[:4]}.ae",
                          "phone": f"+971{rng.choice(['4', '2', '6'])}{rng.randint(2000000, 9999999)}", "emirate": em,
                          "address": f"{rng.choice(['Office', 'Unit', 'Shop'])} {rng.randint(101, 1909)}, {rng.choice(['Business Bay', 'Al Quoz', 'Deira', 'Bur Dubai', 'JLT', 'Mussafah', 'Al Majaz', 'Al Nuaimiya'])}, {em}, UAE",
                          "contact": rng.choice(PEOPLE)[0] + " " + rng.choice(PEOPLE)[1], "category": rng.choice(["Corporate", "SME", "Government", "Healthcare", "Education", "Hospitality"]),
                          "credit_limit": rng.choice([25000, 50000, 75000, 100000, 150000]), "payment_terms": rng.choice(["30 days", "30 days", "45 days", "60 days"]),
                          "status": "Active"})
    n_cust = api.bulk("customers", customers)
    used = _existing_keys(db, company.id, "vendors")
    vendors = [{"name": n, "trn": _trn(rng), "email": f"sales@{n.split()[0].lower()}.ae", "phone": f"+9714{rng.randint(2000000, 9999999)}",
                "category": cat, "emirate": "Dubai", "address": f"{rng.choice(['Jebel Ali Free Zone', 'Dubai Internet City', 'Al Quoz Industrial', 'Dubai Silicon Oasis'])}, Dubai, UAE",
                "contact": rng.choice(PEOPLE)[0] + " " + rng.choice(PEOPLE)[1], "payment_terms": rng.choice(["30 days", "45 days", "Cash"]),
                "status": "Active"} for n, cat in SUPPLIER_NAMES if n.lower() not in used]
    n_vend = api.bulk("vendors", vendors)
    used = _existing_keys(db, company.id, "products")
    supplier_for = {"Laptops": "Dell Technologies FZ-LLC", "Desktops": "HP Middle East FZ-LLC", "Monitors": "Ingram Micro Gulf FZE",
                    "Printers": "Canon Middle East FZ-LLC", "Consumables": "HP Middle East FZ-LLC", "Networking": "Cisco Systems Gulf",
                    "Power": "APC by Schneider Gulf", "Accessories": "Logitech Middle East", "Storage": "Redington Gulf FZE",
                    "Software": "Microsoft Gulf FZ-LLC", "Office Supplies": "Emirates Office Supplies LLC", "Furniture": "Emirates Office Supplies LLC"}
    products = [{"code": c, "name": n, "type": "Service" if cat == "Services" else "Stock Item", "category": cat, "unit": u,
                 "cost": cost, "selling_price": price, "vat": "Standard 5%", "tracking": "None" if cat == "Services" else "Quantity",
                 "supplier_name": supplier_for.get(cat, ""), "barcode": f"62{rng.randint(10**10, 10**11 - 1)}", "status": "Active",
                 "reorder_level": 0 if cat == "Services" else rng.choice([2, 5, 10])}
                for c, n, cat, u, cost, price in PRODUCTS if c.lower() not in used]
    n_prod = api.bulk("products", products)
    used = _existing_keys(db, company.id, "bankAccounts")
    banks = [{"name": "Emirates NBD - Current", "bank": "Emirates NBD", "account_no": f"10{rng.randint(10**10, 10**11 - 1)}",
              "iban": f"AE07026{rng.randint(10**15, 10**16 - 1)}", "currency": "AED", "opening_balance": 185000, "status": "Active"},
             {"name": "ADCB - Collections", "bank": "ADCB", "account_no": f"12{rng.randint(10**10, 10**11 - 1)}",
              "iban": f"AE03003{rng.randint(10**15, 10**16 - 1)}", "currency": "AED", "opening_balance": 64000, "status": "Active"}]
    n_bank = api.bulk("bankAccounts", [b for b in banks if b["iban"].lower() not in used])
    ctx["customers"] = [c["name"] for c in customers] or CUSTOMER_NAMES
    ctx["customer_trn"] = {c["name"]: c["trn"] for c in customers}
    ctx["customer_addr"] = {c["name"]: c["address"] for c in customers}
    return f"{n_cust} customers, {n_vend} suppliers, {n_prod} products, {n_bank} bank accounts"


def _lines(rng, kind: str = "sale", n_max: int = 4) -> tuple[list[dict], float]:
    items = rng.sample(PRODUCTS, rng.randint(1, n_max))
    lines, subtotal = [], 0.0
    for code, name, cat, unit, cost, price in items:
        qty = rng.choice([1, 1, 2, 2, 3, 5, 10]) if cat not in ("Services",) else rng.choice([2, 4, 6, 8, 12])
        if cat in ("Consumables", "Office Supplies", "Accessories"):
            qty *= rng.choice([1, 2, 5])
        unit_price = price if kind == "sale" else cost
        if kind == "sale" and rng.random() < 0.3:
            unit_price = _money(price * rng.choice([0.95, 0.9, 0.92]))   # customer discount
        if unit_price <= 0:
            continue
        amount = _money(qty * unit_price)
        subtotal += amount
        lines.append({"description": name, "product_code": code, "product_name": name, "unit": unit, "qty": qty,
                      "unit_price": unit_price, "price": unit_price, "amount": amount, "tax_rate": 5, "vat_rate": 5})
    return lines, _money(subtotal)


def _quotations(ctx) -> str:
    db, api, rng, today, first = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"]
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    count = 70
    nums = _numbers("QT-", count, _existing_keys(db, ctx["company"].id, "quotations"))
    quotes = []
    for no in nums:
        d = _weighted_date(rng, days)
        lines, sub = _lines(rng)
        if not lines:
            continue
        cust = rng.choice(ctx["customers"])
        vat = _money(sub * 0.05)
        quotes.append({"quote_no": no, "customer": cust, "customer_trn": ctx.get("customer_trn", {}).get(cust, ""),
                       "date": d.isoformat(), "valid_until": (d + timedelta(days=15)).isoformat(),
                       "subtotal": sub, "vat_amount": vat, "total": _money(sub + vat), "owner": "Sales Team",
                       "subject": rng.choice(["IT equipment supply", "Network upgrade", "Office setup", "Annual maintenance", "Laptop refresh"]),
                       "status": "Expired" if d < today - timedelta(days=15) and rng.random() < 0.25 else rng.choice(["Sent", "Accepted", "Accepted", "Draft"]),
                       "lines": lines})
    return f"{api.bulk('quotations', quotes)} quotations"


def _sales(ctx) -> str:
    db, api, rng, today, first, company = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"], ctx["company"]
    days = _working_days(first, today)
    salespeople = [e["name"] for e in ctx.get("employees", []) if e["dept"] == "Sales"] or ["Sales Team"]
    count = 360
    nums = _numbers("INV-", count, _existing_keys(db, company.id, "salesInvoices"), start=2601)
    invoices, receipts = [], []
    rct_used = _existing_keys(db, company.id, "payments")
    rct_nums = iter(_numbers("RCT-", count + 50, rct_used, start=2601))
    for no in nums:
        d = _weighted_date(rng, days)
        lines, sub = _lines(rng)
        if not lines:
            continue
        cust = rng.choice(ctx["customers"])
        terms = rng.choice([30, 30, 45, 60])
        vat = _money(sub * 0.05)
        total = _money(sub + vat)
        due = d + timedelta(days=terms)
        for l in lines:
            key = (f"{d:%Y-%m}", l["product_code"])
            ctx.setdefault("sold", {})[key] = ctx.setdefault("sold", {}).get(key, 0) + l["qty"]
        invoices.append({"invoice_no": no, "document_type": "Sales Invoice", "customer": cust,
                         "customer_trn": ctx.get("customer_trn", {}).get(cust, ""), "customer_address": ctx.get("customer_addr", {}).get(cust, ""),
                         "po_number": f"PO-{rng.randint(10000, 99999)}" if rng.random() < 0.6 else "",
                         "delivery_note_no": f"DN-{rng.randint(1000, 9999)}" if rng.random() < 0.5 else "",
                         "salesperson": rng.choice(salespeople), "date": d.isoformat(), "due_date": due.isoformat(),
                         "subtotal": sub, "vat_amount": vat, "total": total, "status": "Issued", "source": "Manual",
                         "lines": lines, "created_at": datetime(d.year, d.month, d.day, rng.randint(8, 17), rng.randint(0, 59), tzinfo=UTC).isoformat()})
        # Payment behaviour: older invoices mostly paid, recent ones mostly open, a few overdue
        age = (today - d).days
        p_full = 0.92 if age > 75 else 0.7 if age > 40 else 0.35 if age > 15 else 0.08
        roll = rng.random()
        paid = total if roll < p_full else _money(total * rng.choice([0.3, 0.5])) if roll < p_full + 0.08 else 0
        # the status a person would see after recording the receipt
        invoices[-1]["status"] = ("Paid" if paid >= total else "Partially Paid" if paid else
                                  "Overdue" if due < today else "Issued")
        ctx["doc_dates"][no.lower()] = d
        if paid:
            pay_date = min(today, d + timedelta(days=rng.randint(5, terms + 20)))
            rct = next(rct_nums)
            ctx["doc_dates"][rct.lower()] = pay_date
            receipts.append({"type": "Customer Receipt", "ref": rct, "contact": cust, "amount": paid,
                             "method": rng.choice(["Bank Transfer", "Bank Transfer", "Cheque", "Card"]),
                             "date": pay_date.isoformat(), "bank": rng.choice(["Emirates NBD - Current", "ADCB - Collections"]),
                             "comments": f"Payment for {no}", "detail": "", "document_ref": no,
                             "allocations": [{"doc_ref": no, "amount": paid}]})
    # a few returns
    ret_nums = _numbers("SR-", 6, _existing_keys(db, company.id, "salesInvoices"), start=2601)
    for no, base in zip(ret_nums, rng.sample(invoices, min(6, len(invoices)))):
        line = dict(base["lines"][0])
        line["qty"] = 1
        line["amount"] = line["unit_price"]
        sub = _money(line["amount"])
        vat = _money(sub * 0.05)
        invoices.append({**{k: base[k] for k in ("customer", "customer_trn", "customer_address", "salesperson")},
                         "invoice_no": no, "document_type": "Sales Return", "source": "Sales Return",
                         "reference_no": base["invoice_no"], "date": (date.fromisoformat(base["date"]) + timedelta(days=rng.randint(3, 20))).isoformat(),
                         "due_date": "", "subtotal": sub, "vat_amount": vat, "total": _money(sub + vat), "status": "Issued", "lines": [line]})
    n_inv = api.bulk("salesInvoices", invoices, size=100)
    n_rct = api.bulk("payments", receipts, size=150)
    return f"{n_inv} sales documents (6 returns), {n_rct} customer receipts"


def _purchases(ctx) -> str:
    """Restock what was sold: each month, per supplier, a few purchase invoices for that
    month's sold quantities (x0.95-1.15), dated through the month."""
    db, api, rng, today, first, company = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"], ctx["company"]
    product = {c: (n, cat, u, cost) for c, n, cat, u, cost, _price in PRODUCTS}
    supplier_for = {"Laptops": "Dell Technologies FZ-LLC", "Desktops": "HP Middle East FZ-LLC", "Monitors": "Ingram Micro Gulf FZE",
                    "Printers": "Canon Middle East FZ-LLC", "Consumables": "HP Middle East FZ-LLC", "Networking": "Cisco Systems Gulf",
                    "Power": "APC by Schneider Gulf", "Accessories": "Logitech Middle East", "Storage": "Redington Gulf FZE",
                    "Software": "Microsoft Gulf FZ-LLC", "Office Supplies": "Emirates Office Supplies LLC", "Furniture": "Emirates Office Supplies LLC"}
    by_month_supplier: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for (month, code), qty in sorted(ctx.get("sold", {}).items()):
        name, cat, unit, cost = product.get(code, ("", "Services", "", 0))
        if cat == "Services" or cost <= 0:
            continue
        need = max(1, round(qty * rng.uniform(0.95, 1.15)))
        by_month_supplier.setdefault((month, supplier_for.get(cat, "Ingram Micro Gulf FZE")), []).append((code, need))
    used = _existing_keys(db, company.id, "purchaseRecords")
    pay_used = _existing_keys(db, company.id, "payments")
    pur_nums = iter(_numbers("PUR-", 2000, used, start=2601))
    pay_nums = iter(_numbers("PAY-", 2000, pay_used, start=2601))
    purchases, payments = [], []
    for (month, supplier), items in sorted(by_month_supplier.items()):
        y, m = (int(x) for x in month.split("-"))
        month_days = [d for d in _working_days(date(y, m, 1), min(today, (date(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)))]
        if not month_days:
            continue
        rng.shuffle(items)
        orders = max(1, min(len(items), rng.randint(1, 3)))
        for k in range(orders):
            chunk = items[k::orders]
            lines = []
            for code, qty in chunk:
                name, cat, unit, cost = product[code]
                lines.append({"description": name, "product_code": code, "product_name": name, "unit": unit, "qty": qty,
                              "unit_price": cost, "price": cost, "cost": cost, "amount": _money(qty * cost), "tax_rate": 5, "vat_rate": 5})
            sub = _money(sum(l["amount"] for l in lines))
            vat = _money(sub * 0.05)
            total = _money(sub + vat)
            d = month_days[min(len(month_days) - 1, int(len(month_days) * (k + rng.random() * 0.6) / orders))]
            no = next(pur_nums)
            ctx["doc_dates"][no.lower()] = d
            age = (today - d).days
            paid = rng.random() < (0.92 if age > 60 else 0.6 if age > 30 else 0.15)
            purchases.append({"ref": no, "invoice_no": f"{supplier.split()[0][:3].upper()}-{rng.randint(100000, 999999)}",
                              "supplier": supplier, "supplier_trn": _trn(rng), "date": d.isoformat(),
                              "due_date": (d + timedelta(days=45)).isoformat(), "net_amount": sub, "tax_amount": vat, "total": total,
                              "status": "Paid" if paid else ("Overdue" if d + timedelta(days=45) < today else "Pending"),
                              "location": "Main Warehouse", "source": "Manual", "category": "Stock Purchase", "lines": lines})
            if paid:
                pay_date = min(today, d + timedelta(days=rng.randint(10, 50)))
                pay_ref = next(pay_nums)
                ctx["doc_dates"][pay_ref.lower()] = pay_date
                payments.append({"type": "Supplier Payment", "ref": pay_ref, "contact": supplier, "amount": total,
                                 "method": rng.choice(["Bank Transfer", "Bank Transfer", "Cheque"]), "date": pay_date.isoformat(),
                                 "bank": "Emirates NBD - Current", "comments": f"Payment for {no}", "detail": "",
                                 "document_ref": no, "bill_no": no, "allocations": [{"doc_ref": no, "amount": total}]})
    n_pur = api.bulk("purchaseRecords", purchases, size=60)
    n_pay = api.bulk("payments", payments, size=150)
    return f"{n_pur} purchases (restocking what was sold), {n_pay} supplier payments"


def _expenses(ctx) -> str:
    db, api, rng, today, first, company = ctx["db"], ctx["api"], ctx["rng"], ctx["today"], ctx["first"], ctx["company"]
    used = _existing_keys(db, company.id, "expenses")
    days = _working_days(first, today)
    rows = []
    seq = 2601

    def add(d: date, cat, vendor, desc, amount, vatable=True):
        nonlocal seq
        while f"exp-{seq}" in used:
            seq += 1
        vat = _money(amount * 0.05) if vatable else 0
        age = (today - d).days
        ctx["doc_dates"][f"exp-{seq}"] = d
        rows.append({"ref": f"EXP-{seq}", "date": d.isoformat(), "category": cat, "vendor": vendor, "description": desc,
                     "amount": _money(amount), "subtotal": _money(amount), "vat_amount": vat, "total": _money(amount + vat),
                     "status": "Paid" if age > 20 else rng.choice(["Approved", "Pending", "Paid"]), "source": "Manual",
                     "payment_method": rng.choice(["Bank Transfer", "Company Card", "Petty Cash"])})
        seq += 1

    m = first
    while m <= today:
        for cat, vendor, desc, (lo, hi), monthly in EXPENSE_TYPES:
            if monthly:
                d = m.replace(day=min(5 if cat == "Rent" else rng.randint(3, 25), 28))
                if d <= today:
                    add(d, cat, vendor, f"{desc} - {d:%b %Y}", rng.uniform(lo, hi), vatable=cat not in ("Bank Charges",))
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    for _ in range(110):
        cat, vendor, desc, (lo, hi), monthly = rng.choice([e for e in EXPENSE_TYPES if not e[4]])
        add(_weighted_date(rng, days), cat, vendor, desc, rng.uniform(lo, hi))
    return f"{api.bulk('expenses', rows)} expenses"


def _backdate(ctx) -> str:
    """The ledger dates a posting by the day it was saved; a person enters each document on
    its day, so demo documents saved today are moved to their own dates: the invoice row,
    the source transaction, its journal entries and VAT lines, and the app-data row. Then
    the per-month account totals are rebuilt."""
    from sqlalchemy import func as _f

    from app import account_totals
    from app.models import AppDataRecord, Invoice, JournalEntry, PayrollRun, SourceTransaction, TaxLine
    db, company = ctx["db"], ctx["company"]
    dates = ctx.get("doc_dates", {})
    if not dates:
        return "nothing to date"

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, 6, 30, tzinfo=UTC)   # 10:30 in the UAE

    moved = 0
    refs = list(dates)
    for i in range(0, len(refs), 400):
        chunk = refs[i:i + 400]
        for st in db.query(SourceTransaction).filter(SourceTransaction.company_id == company.id,
                                                     _f.lower(SourceTransaction.reference).in_(chunk)).all():
            d = dates.get(st.reference.lower())
            if not d:
                continue
            st.created_at = at(d)
            if st.approved_at:
                st.approved_at = at(d)
            db.query(JournalEntry).filter(JournalEntry.source_id == st.id).update(
                {JournalEntry.entry_date: at(d), JournalEntry.created_at: at(d)}, synchronize_session=False)
            db.query(TaxLine).filter(TaxLine.source_id == st.id).update(
                {TaxLine.period: f"{d:%Y-%m}", TaxLine.created_at: at(d)}, synchronize_session=False)
            moved += 1
        for inv in db.query(Invoice).filter(Invoice.company_id == company.id, _f.lower(Invoice.invoice_number).in_(chunk)).all():
            inv.created_at = at(dates[inv.invoice_number.lower()])
        # A plain UPDATE of the date: loading the rows through the ORM re-derives their stored
        # figures (doc_index) from a payload the row no longer keeps, which zeroed them.
        rows = db.query(AppDataRecord.id, AppDataRecord.record_key).filter(
            AppDataRecord.company_id == company.id, _f.lower(AppDataRecord.record_key).in_(chunk),
            AppDataRecord.collection.in_(["salesInvoices", "purchaseRecords", "payments", "expenses"])).all()
        by_day: dict[date, list[str]] = {}
        for row_id, key in rows:
            by_day.setdefault(dates[str(key).lower()], []).append(row_id)
        for d, ids in by_day.items():
            db.query(AppDataRecord).filter(AppDataRecord.id.in_(ids)).update(
                {AppDataRecord.created_at: at(d)}, synchronize_session=False)
        db.commit()
    for run in db.query(PayrollRun).filter(PayrollRun.company_id == company.id).all():
        try:
            y, m = (int(x) for x in str(run.period)[:7].split("-"))
            month_end = (date(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            run.created_at = at(month_end)
        except (ValueError, AttributeError):
            continue
    db.commit()
    account_totals.rebuild_company(db, company.id)
    db.commit()
    return f"{moved} postings dated to their documents, account totals rebuilt"
