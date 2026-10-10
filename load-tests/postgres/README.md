# PostgreSQL load test

Simulated office users and employees against the real app on PostgreSQL, at a realistic
data size. Run it before releases that touch hot paths (saves, dashboard, reports,
registers, HRMS) and compare with the results below.

## 1. A local PostgreSQL

Any PostgreSQL 14+ works. On Windows without an installer, the `pgserver` wheel ships the
server binaries (`pip install --target <dir> pgserver`; binaries in
`<dir>/pgserver/pginstall/bin`):

```bash
initdb -D pgdata -U postgres -A trust -E UTF8 --locale=C
# add to pgdata/postgresql.conf: port = 55432, max_connections = 100 (about the DO plan),
# shared_buffers = 512MB, log_min_duration_statement = 500
pg_ctl -D pgdata -l pg.log start
createdb -h 127.0.0.1 -p 55432 -U postgres taxflow_lt
```

## 2. Seed (about 20 minutes, 5.4 GB)

```bash
cd backend
export DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55432/taxflow_lt SECRET_KEY=load-test-secret-local-only
python -m app.migrate
python scripts/run_bulk_seed.py --companies 200 --branches-per-company 2 --employees-per-company 25 \
  --customers-per-company 60 --suppliers-per-company 30 --products-per-company 80 \
  --sales-per-company 1200 --purchases-per-company 600 --years 1 --yes
python ../load-tests/postgres/lt_seed_extra.py   # logins, rota, attendance, leave, app-data invoices/receipts/expenses
```

## 3. Run

```bash
python load-tests/postgres/lt_server.py 8071 3 10 15        # one production instance: 3 workers, pool 10+15
cd backend && python ../load-tests/postgres/loadgen.py http://127.0.0.1:8071 100,200,300,400,500 60 0.3 run.json
```

`loadgen.py` adds simulated users stage by stage (30% office users acting every ~8 s, 70%
employees on the phone app every ~30 s) and prints requests/s, median and 95th-percentile
times, the slowest action and errors per stage; `run.json` has per-action detail. It stops
when 95% of responses take over 5 s or errors pass 5%.

## In CI

- `.github/workflows/backend-tests.yml` runs the whole test suite on PostgreSQL 16 as well
  as SQLite on every push (`TEST_DATABASE_URL` points `tests/conftest.py` at a throwaway
  database; its schema is reset at the start).
- `.github/workflows/load-test.yml` runs this load test on every `v*` tag and on demand
  (Actions -> Load test -> Run workflow, with the number of companies and user stages as
  inputs). It seeds, starts one 3-worker instance and fails when a stage's 95th percentile
  passes `LT_MAX_P95_MS` (2000) or server errors pass `LT_MAX_ERROR_PCT` (1%). The GitHub
  runner is a shared 4-vCPU VM, so its numbers are lower than a production instance's;
  compare runs with each other, not with the table below.

## Results, 2026-10-06

Data: 200 companies, 5,000 employees, 240k accounting invoices, 1.8M journal lines, 1.24M
attendance rows, 2.4M app-data rows. One "instance" = 3 uvicorn workers, pool 10+15, on an
8-core laptop that also ran the load generator; no Redis (worst case: dashboard and report
figures recomputed every request).

| Users at once | Requests/s | Median | 95% under | Errors |
|---|---|---|---|---|
| 100 | 6.3 | 27 ms | 246 ms | 0% |
| 200 | 12.4 | 38 ms | 271 ms | 0% |
| 300 | 20.3 | 43 ms | 390 ms | 0% |
| 400 | 25.4 | 86 ms | 685 ms | 0% |
| 500 | 28.6 | 181 ms | 1.1 s | 0.2% (client timeouts) |
| 600+ | ~29 | queues build up; 95% over 5 s | | 503s start |

What it showed:

- **The app processes run out of CPU first, not PostgreSQL.** At saturation PostgreSQL had
  at most 5 queries active at once; requests waited for a free Python worker. One instance
  holds about 400-500 simultaneous users (about 25-29 requests/s).
- **Over half of the server time is the dashboard figures and the P&L/VAT summary**
  (about 180 ms and 470 ms each, 32 and 48 queries). Production caches both in Redis, so
  real capacity is higher than above; pre-calculated totals (stage 2 of the plan) are the
  lasting fix.
- **Connections, not queries, are the database risk.** One instance opened up to 76
  connections (3 workers x 25 pool) while using 5. Three instances would pass a
  100-connection plan, so PgBouncer (transaction mode) is needed before scaling out.
- Fixed during the test:
  - startup tasks (initial data, role/ledger backfills) raced when several workers started
    together on PostgreSQL (3 of 4 failed); they now run one worker at a time under an
    advisory lock (`app.migrate.startup_lock()`);
  - missing indexes on the posting tables (migration `0005_posting_indexes`): saving an
    invoice went from about 450 ms to 170 ms, and 400 users from 2.7 s to 0.7 s at the 95th
    percentile;
  - rate limits were per IP, so a whole office behind one IP got 429s; they are now per
    signed-in login (`app.limiter.rate_limit_key()`), still per IP for sign-in;
  - `scripts/bulk_seed` wrote child rows before their parents, which PostgreSQL rejects.

Follow-up the same day: the summary/dashboard added up every posted journal line four
times per request (cost of sales, balance sheet x2, trial balance). They now share one
per-request result (`reports.posted_account_totals()`); output is identical (checked
company by company against the old code) and `/reports/summary` went from 390 ms to
160 ms. Re-run, one instance:

| Users at once | Requests/s | Median | 95% under | Errors |
|---|---|---|---|---|
| 400 | 25.2 | 67 ms | 597 ms | 0% |
| 500 | 30.0 | 144 ms | 819 ms | 0% |
| 600 | 33.2 | 366 ms | 1.8 s | 0% |
| 700 | ~30 | queues build up | | |

Rough sizing from this: 500 companies at peak (about 1,000-2,000 people online at once)
need 3-4 such instances (about 500-600 people each) behind PgBouncer.

## 2026-10-10: towards 2,000 users at once

Same seed (200 companies) and one 3-worker instance, old code (commit 0e8644c) and new code
run back to back on the same laptop and data (no Redis, so dashboard and summary are rebuilt
on every request). The laptop was also running a browser and a preview server, so both runs
saturate earlier than the table above; compare them with each other.

| Users at once | Before: req/s, median, 95% | After: req/s, median, 95% |
|---|---|---|
| 300 | 18.1, 175 ms, 1.9 s | 20.1, 71 ms, 363 ms |
| 500 | 23.1, 2.4 s, 8.8 s | 27.2, 1.1 s, 5.1 s |

What changed (each measured on its own first):

- **Fixed cost of every request 4.4 ms -> 2.0 ms.** Five `@app.middleware("http")` functions
  (each a Starlette BaseHTTPMiddleware with its own task and stream per request) are one plain
  ASGI middleware, `app/http_middleware.py`. The live-load counter made three blocking Redis
  calls on the event loop per API request; it now counts in memory and a thread flushes every
  2 s (`app/request_metrics.py`).
- **Saving an invoice 138 ms -> 79 ms, a receipt 90 ms -> 43 ms.** The corporate-tax refresh
  on every save added up the company's whole journal (35 ms at 9k lines, growing with
  history); it reads the stored account totals (2 ms, identical figures for all 200 companies).
- **`/reports/summary` 640 KB -> 49 KB.** The General Ledger listing (up to 2,000 rows) was 99%
  of it, and of its Redis entry; it is `GET /reports/general-ledger`, fetched when the tab opens.
- **Cache invalidation after a write no longer scans Redis.** Trial balance and VAT return keys
  are listed in a per-company set (`cache.set_in_group`); a write costs one or two Redis round
  trips however many keys Redis holds (SCAN cost grew with every company). The company of the
  writing user is remembered per process instead of queried on every write.
- Branch logins wrote `last_activity` (UPDATE + COMMIT) on every request; now at most once a
  minute, like employees.

Still open: FastAPI 0.142 matches routes by scanning every route of each included router
(~1.3 ms per request here, a one-route app takes 0.05 ms).
