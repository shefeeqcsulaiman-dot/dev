"""Simulated users against a local server. Office users browse the dashboard, registers and
reports and save invoices/receipts; employees use the phone app. Stages step up the number of
simultaneous users and report throughput, response times and errors per stage.

    cd backend && DATABASE_URL=... SECRET_KEY=<same as server> python ../load-tests/postgres/loadgen.py BASE_URL STAGES(comma) STAGE_SECONDS OFFICE_SHARE [OUT.json]
"""
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid

sys.path.insert(0, os.getcwd())
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.security import create_access_token  # noqa: E402

BASE = sys.argv[1]
STAGES = [int(x) for x in sys.argv[2].split(",")]
STAGE_SECONDS = int(sys.argv[3])
OFFICE_SHARE = float(sys.argv[4])
OUT = sys.argv[5] if len(sys.argv) > 5 else None
# Release gate (CI): exit 1 if any stage's 95th percentile or server-error rate is above these.
MAX_P95_MS = float(os.environ.get("LT_MAX_P95_MS", "0") or 0)
MAX_ERROR_PCT = float(os.environ.get("LT_MAX_ERROR_PCT", "0") or 0)
OFFICE_THINK, EMP_THINK = 8.0, 30.0   # average seconds between one user's actions

db = SessionLocal()
office = [create_access_token(uid) for (uid,) in db.execute(text(
    "SELECT u.id FROM users u WHERE u.role = 'admin' AND EXISTS "
    "(SELECT 1 FROM employees e WHERE e.company_id = u.company_id AND e.employee_no LIKE 'BULK-%')"))]
emps = [create_access_token("emp:" + eid) for (eid,) in db.execute(text("SELECT id FROM employees WHERE employee_no LIKE 'BULK-%'"))]
db.close()
print(f"tokens: {len(office)} office users, {len(emps)} employees", flush=True)

samples: list[tuple[float, str, float, int]] = []   # (time, label, ms, status)


async def call(client, tok, method, path, label, body=None):
    t = time.perf_counter()
    try:
        r = await client.request(method, BASE + path, headers={"Authorization": "Bearer " + tok}, json=body, timeout=60)
        code = r.status_code
    except Exception:
        code = 599
    samples.append((time.time(), label, (time.perf_counter() - t) * 1000, code))


def invoice():
    n = uuid.uuid4().hex[:10]
    return {"collection": "salesInvoices", "create_only": True, "record": {
        "invoice_no": f"LT-{n}", "customer": "Customer 007", "date": "2026-10-03", "status": "Issued",
        "subtotal": 300, "vat_amount": 15, "total": 315, "lines": [{"description": "Item", "qty": 2, "unit_price": 150, "vat_rate": 5}]}}


def receipt():
    n = uuid.uuid4().hex[:10]
    return {"collection": "payments", "create_only": True, "record": {
        "type": "Customer Receipt", "ref": f"RCT-LT-{n}", "contact": "Customer 007", "amount": 100, "date": "2026-10-03",
        "method": "Cash", "document_ref": "-", "allocations": []}}


BODIES = {"invoice": invoice, "receipt": receipt}
OFFICE_ACTIONS = [  # (weight, method, path, label, body)
    (18, "GET", "/api/v1/reports/dashboard", "dashboard figures", None),
    (10, "GET", "/api/v1/app-data?scope=main", "open/refresh main app", None),
    (8, "GET", "/api/v1/reports/summary", "reports (P&L, VAT)", None),
    (9, "POST", "/api/v1/app-data?action=save", "save sales invoice", "invoice"),
    (9, "GET", "/api/v1/app-data/sales-invoices?limit=50", "sales register page", None),
    (6, "GET", "/api/v1/app-data/sales-invoices/summary", "sales KPIs", None),
    (3, "GET", "/api/v1/app-data/sales-invoices?limit=50&q=customer%2001", "sales search", None),
    (4, "GET", "/api/v1/app-data/registers/bills?limit=50", "bills page", None),
    (3, "GET", "/api/v1/app-data/registers/payments?kind=customer&limit=50", "receipts page", None),
    (3, "GET", "/api/v1/app-data/registers/payments/summary", "payment totals", None),
    (3, "GET", "/api/v1/app-data/payables/open?side=customer", "receipt: open invoices", None),
    (3, "POST", "/api/v1/app-data?action=save", "record receipt", "receipt"),
    (4, "GET", "/api/v1/reports/trial-balance", "trial balance", None),
    (6, "GET", "/api/v1/app-data?scope=hrms", "open HRMS", None),
    (4, "GET", "/api/v1/attendance/monthly-report?period=2026-09", "attendance report", None),
    (5, "GET", "/api/v1/app-data/records/rotaAssignments/range?from=2026-09-28&to=2026-10-04", "rota week", None),
    (4, "GET", "/api/v1/attendance/today", "today's attendance", None),
]
EMP_ACTIONS = [
    (25, "GET", "/api/v1/hr/dashboard", "app: home", None),
    (20, "GET", "/api/v1/ess/attendance", "app: attendance", None),
    (20, "GET", "/api/v1/ess/rota", "app: rota", None),
    (15, "GET", "/api/v1/ess/leave-balance", "app: leave", None),
    (10, "GET", "/api/v1/ess/requests", "app: requests", None),
    (10, "GET", "/api/v1/ess/me", "app: profile", None),
]


async def user(client, kind, stop):
    tok = random.choice(office if kind == "office" else emps)
    actions, think = (OFFICE_ACTIONS, OFFICE_THINK) if kind == "office" else (EMP_ACTIONS, EMP_THINK)
    weights = [a[0] for a in actions]
    await asyncio.sleep(random.uniform(0, think))  # spread starts
    while not stop.is_set():
        _, method, path, label, body = random.choices(actions, weights)[0]
        await call(client, tok, method, path, label, BODIES[body]() if body else None)
        try:
            await asyncio.wait_for(stop.wait(), timeout=random.expovariate(1 / think))
        except asyncio.TimeoutError:
            pass


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))] if s else 0


async def main():
    limits = httpx.Limits(max_connections=3000, max_keepalive_connections=3000)
    results = []
    async with httpx.AsyncClient(limits=limits) as client:
        stop = asyncio.Event()
        tasks = []
        print("| Users at once | Office / employees | Requests/s | Median ms | 95% under ms | Slowest action (95%) | Errors |", flush=True)
        print("|---|---|---|---|---|---|---|", flush=True)
        for target in STAGES:
            n_office = round(target * OFFICE_SHARE)
            have_office = sum(1 for k, _ in tasks if k == "office")
            for _ in range(n_office - have_office):
                tasks.append(("office", asyncio.create_task(user(client, "office", stop))))
            have_emp = sum(1 for k, _ in tasks if k == "emp")
            for _ in range(target - n_office - have_emp):
                tasks.append(("emp", asyncio.create_task(user(client, "emp", stop))))
            await asyncio.sleep(max(OFFICE_THINK, 15))       # let new users settle
            start = time.time()
            await asyncio.sleep(STAGE_SECONDS)
            win = [s for s in samples if s[0] >= start]
            ms = [s[2] for s in win]
            errs = sum(1 for s in win if s[3] >= 500)
            by_label: dict[str, list[float]] = {}
            for s in win:
                by_label.setdefault(s[1], []).append(s[2])
            worst = max(by_label.items(), key=lambda kv: pct(kv[1], 0.95)) if by_label else ("-", [0])
            codes: dict[int, int] = {}
            for s in win:
                if s[3] >= 400:
                    codes[s[3]] = codes.get(s[3], 0) + 1
            stage = {
                "users": target, "office": n_office, "rps": len(win) / STAGE_SECONDS,
                "median_ms": statistics.median(ms) if ms else 0, "p95_ms": pct(ms, 0.95),
                "error_pct": 100 * errs / max(1, len(win)), "codes": codes,
                "by_action": {k: {"n": len(v), "median_ms": statistics.median(v), "p95_ms": pct(v, 0.95)} for k, v in by_label.items()},
            }
            results.append(stage)
            print(f"| {target} | {n_office} / {target - n_office} | {stage['rps']:.1f} | {stage['median_ms']:.0f} | "
                  f"{stage['p95_ms']:.0f} | {worst[0]} {pct(worst[1], 0.95):.0f} ms | {stage['error_pct']:.1f}% |", flush=True)
            if codes:
                print(f"    error codes: {codes}", flush=True)
            if OUT:
                with open(OUT, "w") as f:
                    json.dump(results, f, indent=1)
            if pct(ms, 0.95) > 5000 or errs / max(1, len(win)) > 0.05:
                print("stopping: responses too slow or too many errors", flush=True)
                break
        stop.set()
        await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)
    return results


results = asyncio.run(main())
failed = [s for s in results
          if (MAX_P95_MS and s["p95_ms"] > MAX_P95_MS) or (MAX_ERROR_PCT and s["error_pct"] > MAX_ERROR_PCT)]
if failed or (MAX_P95_MS and len(results) < len(STAGES)):
    print(f"LOAD TEST FAILED: limits p95 <= {MAX_P95_MS:.0f} ms, errors <= {MAX_ERROR_PCT}% "
          f"(stages over the limit: {[s['users'] for s in failed]}, stages run: {len(results)}/{len(STAGES)})", flush=True)
    sys.exit(1)
