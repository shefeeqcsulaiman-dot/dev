"""Adds what run_bulk_seed.py doesn't, for every BULK-* company in the local load-test
Postgres: employee portal logins, 3 office users, HRMS employee records, a year of
rota/attendance/leave, and app-data sales invoices, receipts, quotations and expenses.
Fills the app_data_records summary columns (app/doc_index.py) the way the app's own
saves would, including on the bills/purchase records run_bulk_seed.py bulk-inserted.

    cd backend && DATABASE_URL=... python ../load-tests/postgres/lt_seed_extra.py
"""
import json
import os
import random
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.getcwd())
from sqlalchemy import insert, text, update  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.doc_index import doc_columns  # noqa: E402
from app.models import AppDataRecord, AttendanceDetail, Employee, LeaveRequest, User, uuid  # noqa: E402
from app.routers.hr_access import _ensure_default_roles  # noqa: E402
from app.security import hash_password  # noqa: E402

assert "127.0.0.1" in os.environ["DATABASE_URL"] or "localhost" in os.environ["DATABASE_URL"]
random.seed(11)
END = date(2026, 10, 5)
START = END - timedelta(days=364)
PW = hash_password("LoadPass123")
SALES, RECEIPT_SHARE, QUOTES, EXPENSES = 1200, 0.6, 60, 120


def row(cid, collection, key, rec, **cols):
    r = {"id": uuid(), "company_id": cid, "collection": collection, "record_key": key, "payload": json.dumps(rec)}
    r.update(doc_columns(rec, collection) if collection in ("salesInvoices", "payments", "quotations", "expenses") else {})
    r.update(cols)
    return r


def day(i):
    return (START + timedelta(days=i % 365)).isoformat()


db = SessionLocal()
emps_by_co: dict[str, list[Employee]] = {}
for e in db.query(Employee).filter(Employee.employee_no.like("BULK-%")).all():
    emps_by_co.setdefault(e.company_id, []).append(e)
print(f"{len(emps_by_co)} companies", flush=True)

t0 = time.time()
for n, (cid, emps) in enumerate(sorted(emps_by_co.items()), 1):
    roles = _ensure_default_roles(db, cid)
    emp_role = roles["Employee"].id
    rows = []
    for e in emps:
        e.username, e.password_hash, e.role_id, e.is_active = e.employee_no.lower(), PW, emp_role, True
        rows.append(row(cid, "employees", e.employee_no, {"id": e.employee_no, "name": e.full_name, "department": e.department,
                                                          "designation": e.designation, "status": "Active", "join_date": "2024-01-15"}))
    for role in ("accountant", "sales", "viewer"):
        db.add(User(company_id=cid, email=f"{role}.{cid[:8]}@load.example", full_name=role.title(), password_hash=PW, role=role))
    # rota: every day, weekend off
    for e in emps:
        for i in range(365):
            d = START + timedelta(days=i)
            off = d.weekday() >= 5
            rec = {"id": f"{e.employee_no}-{d.isoformat()}", "employee_id": e.employee_no, "employee_name": e.full_name,
                   "department": e.department, "date": d.isoformat(), "type": "Off" if off else "Morning", "code": "OFF" if off else "M",
                   "start": "" if off else "09:00", "end": "" if off else "18:00", "status": "Published", "tasks": []}
            rows.append(row(cid, "rotaAssignments", rec["id"], rec, record_date=rec["date"]))
    # sales invoices (+ receipts for ~60%), quotations, expenses
    customers = [f"Customer {k:03d}" for k in range(60)]
    for i in range(SALES):
        sub = Decimal(random.randint(200, 20000))
        vat = (sub * Decimal("0.05")).quantize(Decimal("0.01"))
        no = f"INV-{cid[:6]}-{i:05d}"
        rec = {"invoice_no": no, "customer": random.choice(customers), "date": day(i * 365 // SALES), "status": "Issued",
               "subtotal": float(sub), "vat_amount": float(vat), "total": float(sub + vat), "salesperson": random.choice(["Ali", "Sara", ""]),
               "lines": [{"description": f"Item {random.randint(1, 80)}", "qty": random.randint(1, 5), "unit_price": float(sub), "vat_rate": 5}]}
        paid = Decimal(0)
        if random.random() < RECEIPT_SHARE:
            paid = sub + vat if random.random() < 0.8 else ((sub + vat) / 2).quantize(Decimal("0.01"))
            pay = {"type": "Customer Receipt", "ref": f"RCT-{cid[:6]}-{i:05d}", "contact": rec["customer"], "amount": float(paid),
                   "date": rec["date"], "method": "Bank Transfer", "document_ref": no, "invoice_no": no,
                   "allocations": [{"doc_ref": no, "amount": float(paid)}]}
            rows.append(row(cid, "payments", pay["ref"], pay))
        rows.append(row(cid, "salesInvoices", no, rec, amount_paid=paid))
    for i in range(QUOTES):
        q = {"quote_no": f"QTN-{cid[:6]}-{i:04d}", "customer": random.choice(customers), "date": day(i * 6), "total": random.randint(500, 9000),
             "status": random.choice(["Sent", "Draft", "Converted"]), "owner": "Sales Team"}
        rows.append(row(cid, "quotations", q["quote_no"], q))
    for i in range(EXPENSES):
        amt = random.randint(50, 3000)
        x = {"ref": f"EXP-{cid[:6]}-{i:04d}", "date": day(i * 3), "category": random.choice(["Direct Expense", "Indirect Expense", "Supplies"]),
             "description": "Expense", "amount": amt, "vat_amount": 0, "total": amt, "status": random.choice(["Pending", "Approved", "Approved"])}
        rows.append(row(cid, "expenses", x["ref"], x))
    for k in range(0, len(rows), 2000):
        db.execute(insert(AppDataRecord), rows[k:k + 2000])
    # attendance: weekdays, ~95% present
    att = []
    for e in emps:
        for i in range(365):
            d = START + timedelta(days=i)
            if d.weekday() < 5 and random.random() < 0.95:
                cin = datetime(d.year, d.month, d.day, 5, 0, tzinfo=UTC) + timedelta(minutes=random.randint(-20, 30))
                cout = cin + timedelta(hours=9, minutes=random.randint(-10, 80))
                secs = int((cout - cin).total_seconds())
                att.append({"id": uuid(), "company_id": cid, "employee_id": e.employee_no, "employee_name": e.full_name,
                            "work_date": d.isoformat(), "clock_in_1": cin, "clock_out_1": cout, "work_seconds_1": secs,
                            "total_seconds": secs, "ot_seconds": max(0, secs - 8 * 3600), "under_seconds": max(0, 8 * 3600 - secs),
                            "session_count": 1, "raw_events": "[]"})
    for k in range(0, len(att), 2000):
        db.execute(insert(AttendanceDetail), att[k:k + 2000])
    leaves = []
    for e in emps:
        for _ in range(4):
            s = START + timedelta(days=random.randint(0, 350))
            days = random.randint(1, 5)
            leaves.append({"id": uuid(), "company_id": cid, "employee_id": e.id, "leave_type": random.choice(["Annual Leave", "Sick Leave"]),
                           "start_date": s.isoformat(), "end_date": (s + timedelta(days=days - 1)).isoformat(), "days": days,
                           "reason": "Planned", "status": random.choice(["approved", "approved", "pending"])})
    db.execute(insert(LeaveRequest), leaves)
    db.commit()
    if n % 20 == 0:
        print(f"  {n} companies, {time.time() - t0:.0f}s", flush=True)

# Summary columns on the bills / purchase records run_bulk_seed.py bulk-inserted (no ORM hooks ran).
for collection in ("bills", "purchaseRecords"):
    ids = [r for (r,) in db.execute(text("SELECT id FROM app_data_records WHERE collection = :c AND doc_kind IS NULL"), {"c": collection})]
    for k in range(0, len(ids), 1000):
        for rid, payload in db.execute(text("SELECT id, payload FROM app_data_records WHERE id = ANY(:ids)"), {"ids": ids[k:k + 1000]}):
            cols = doc_columns(json.loads(payload or "{}"), collection)
            cols["amount_paid"] = Decimal(0)
            db.execute(update(AppDataRecord).where(AppDataRecord.id == rid).values(**cols))
        db.commit()
    print(f"stamped {len(ids)} {collection}", flush=True)
db.execute(text("ANALYZE"))
db.commit()
print("done", f"{time.time() - t0:.0f}s", flush=True)
