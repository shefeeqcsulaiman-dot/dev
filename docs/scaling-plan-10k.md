# Scaling plan: 10,000 companies

Saved 2026-10-06. Working notes on the road from today's setup to 10,000 companies.

## What 10,000 companies means

- **People:** about 290,000 users (10,000 × 25 employees + about 4 office staff each). UAE businesses share working hours, so expect 20,000–40,000 people active at the same moment at peak.
- **Load:** about 3,000–4,000 requests per second at peak. Today's setup comfortably handles about 7 per server copy.
- **Data:** roughly 15 million invoices, 90 million rota rows and 60 million attendance rows per year, all in one database.

## Code changes (most important first)

1. **Move core data out of the JSON `app-data` store into proper tables.** Invoices, purchases, rota, attendance-related records, tasks and requests are stored as JSON text, and many reports read and parse them on every request. Use real columns plus indexes so the database does the work.
2. **Pre-calculated totals.** Update monthly totals per company (sales, VAT, purchases, cost of goods) whenever a record is saved. The dashboard and reports then read a few rows instead of a year of history.
3. **Load pages on demand.** Stop sending up to 1,500 invoices on every page open. Each list loads 50–100 rows, with search done on the server.
4. **Background jobs (Celery is already set up):** AI invoice reading, bulk uploads, report and PDF exports, payroll runs, backups and biometric sync.
5. **Proper database migrations (Alembic)** instead of schema changes at startup. The load test showed startup changes clashing when several server processes start at once.
6. **Split the frontend.** `app.js` is one 1.5 MB file. Load each page's code only when it's opened, and serve static files from a CDN.
7. **Limits per company, not per IP address.** Rate limits, caches and job queues keyed by company.
8. **Monitoring:** error tracking (Sentry), response-time dashboards per endpoint, slow-query logs, alerts.
9. **Load testing on every release**, against PostgreSQL at target size, automated.

## Server changes

| Area | Today | For 10,000 companies |
|---|---|---|
| App servers | 3–6 copies, 2 CPUs each | About 30–60 copies, autoscaled behind a load balancer (Kubernetes or AWS ECS) |
| Database | One managed PostgreSQL, about 95 connections | Large primary, read replicas for reports, PgBouncer, big tables partitioned by date |
| Cache | One Redis | Managed Redis cluster for cache, sessions and rate limits |
| Files | On the app server and database | Object storage (S3 or Spaces) plus a CDN |
| Background work | 1 worker | Separate worker pools: AI, reports, payroll, sync |
| Backups | Database dump | Point-in-time recovery, a tested restore, a second region for disaster recovery |
| Location | DigitalOcean New York | A UAE region (AWS me-central-1 or Azure UAE North) |

Rough infrastructure cost at that size: a few thousand US dollars a month (revenue at 10,000 × AED 300 ≈ AED 3 million a month).

## Suggested order

**Up to about 500 companies** (keep the current setup, fix the weak spots):
- [x] Alembic migrations (multi-process lock still to be proven on PostgreSQL)
- [~] PgBouncer and a bigger database: code ready (migrations bypass the pooler via DATABASE_DIRECT_URL). To do in DigitalOcean: create a Transaction-mode connection pool, point DATABASE_URL at it, set DATABASE_DIRECT_URL. Today's pool settings can open up to 225–450 connections against a ~95-connection plan (the pool is per worker, not per instance)
- [x] Paginated lists and server-side Sales totals (sales invoices, bills, payments, quotations, expenses, purchase documents and rota assignments paged; legacy ledger lines dropped from bootstrap. HR lists such as employees, leave and tasks still come in the capped bootstrap: they grow with headcount, not sales volume, so they come later)
- [~] Monitoring: per-endpoint timings, slow-request/slow-query logs, Server-Timing header, superadmin "Slowest Endpoints" panel and optional Sentry done. Still to do: create a Sentry project and set SENTRY_DSN, then set up alerts (Sentry + DigitalOcean)
- [x] PostgreSQL load test (load-tests/postgres/README.md): one instance (3 workers) holds about 400-500 people online at once; the app CPU saturates first, not PostgreSQL. Fixed on the way: startup race across workers, missing posting-table indexes (migration 0005), per-IP rate limits (now per login). Automated: CI runs the full test suite on PostgreSQL on every push, and `.github/workflows/load-test.yml` runs the load test on every `v*` tag / on demand with p95 and error-rate limits.

**Up to about 2,000 companies:**
- [~] Move invoices, purchases, rota and attendance out of JSON into tables. Step 2 done (2026-10-08): report figures are real columns (`fig_*`, migration 0009), so the dashboard, summary, VAT and branch-performance figures for sales invoices, purchases, bills and expenses are SQL sums; nothing in reports.py decodes a whole collection any more. Still JSON: the records themselves (the UI saves and reads them through app-data), products, customers, employees and rota
- [x] Pre-calculated totals: reports read posted debit/credit per account and month from `account_period_totals` (migration 0006, `app/account_totals.py`), kept current on every journal write including bulk deletes; `REPORT_TOTALS_SOURCE=live` switches back to summing journal lines; `python -m app.account_totals [--rebuild]` checks/repairs; the test suite verifies every company at the end of each run. Dashboard: sales invoices already posted as real Invoices are skipped in SQL instead of parsed, and monthly revenue/VAT reads only the columns it needs (dashboard 138 -> 96 ms on a 1-year company, identical output). Still parsed per request: purchase/bill app-data documents for the purchase cards
- [ ] Read replica
- [~] Background jobs for heavy work: AI invoice reading (purchases, expense receipts, sales import, batch .zip uploads) runs as a background job (2026-10-08, see below). Still in the request: payroll generate, exports
- [ ] Split frontend

**Up to 10,000 companies:**
- [ ] AWS or Azure UAE with Kubernetes
- [ ] Partitioning
- [ ] Multi-region disaster recovery
- [ ] Security certification (ISO 27001 / SOC 2)
- [ ] Accredited e-invoicing provider status

## Background jobs: how they work now (2026-10-08)

- `POST /app-data?action=documents.extract|invoices.import&background=1` saves a `Job` row and returns its id at once; `GET /app-data/jobs/{id}` gives `status` (queued/running/completed/failed), `result` once completed, `error` once failed. Without `background=1` the actions answer in the request, as before. The browser (`runExtractionJob()` in app.js) uses the job path for purchase uploads, expense receipts and sales import, asking every 2 s for up to 15 minutes, so a long file or .zip batch no longer ends in a 504.
- `app/background.py` runs jobs on a 4-thread pool inside the API process, each in its own database session with the caller's Principal rebuilt from their login token (`auth_principal.principal_from_token()`). Threads rather than Celery because the work mostly waits on the AI provider and production has no Redis broker; move to Celery once Redis exists and jobs need to outlive a restart.
- A running job touches its row every minute (heartbeat), however long it takes; one cut off by a restart (deploy, worker recycling) stops beating and shows as failed after 30 minutes, with a "please try again" message. With `CELERY_TASK_ALWAYS_EAGER` (tests, local dev) jobs run inline.

## Report figures: how they work now (2026-10-08)

- Each sales invoice, purchase record, bill and expense carries its report figures as columns, stamped on every save by `doc_index.doc_figures()` (the same rules reports.py applied to the decoded JSON, now shared from doc_index): `fig_status` (status as reports compare it), `fig_ref` (the reference it posts its Invoice / input TaxLine under), `fig_gross`, `fig_net`, `fig_vat`, `fig_taxable`, `fig_paid`.
- `_purchase_summary()`, `_build_branch_performance()` and the app-data expense total are grouped SQL sums of them. `app_purchase_records()` skips purchases already posted as input TaxLines in SQL (`fig_ref`, index `ix_app_data_company_collection_fig_ref`) and only decodes the unposted ones.
- Checked against the previous code on the 200-company load-test data (every company, with and without a branch filter: purchase summary, unposted purchases, branch performance, dashboard, summary): identical output. Branch performance 6.6x faster, purchase summary 3.8x.
- An amount that can't be read (e.g. "AED 100" in a total field) used to fail the whole report with a 500; that record now adds nothing to the total.
- Migration 0009 fills the columns for existing records: 385,000 records took 4.4 minutes. Migrations run at startup, before the server answers health checks, so a migration that long on a big production database would fail the deploy: run `python -m app.migrate` as a DigitalOcean pre-deploy job first (and set `RUN_MIGRATIONS_ON_STARTUP=false`) once production holds real data.

## Super Admin, scheduled jobs and per-server memory: how they work now (2026-10-07)

- **Startup:** the role, ledger and user backfills already run once each (a `schema_flags` row) and cost about 2 queries per start; measured on 202 companies: 5-17 ms each.
- **Super Admin lists are paged in the database.** `GET /superadmin/companies` (`q` = name/TRN/country/user email, `status` = active/expiring/expired/inactive/no_users/no_expiry/new_month, `sort`, `dir`, `limit`, `offset`) returns `{items, total}` in 2 queries; users, branches and employees come from `GET /superadmin/companies/{id}` when Details or Edit opens. `GET /companies/overview` gives the Overview counts and insight samples; `GET /superadmin/users` pages users; `/companies/export.csv` and `/users/export.csv` stream every matching row. 202 companies: 23-67 ms per page.
- **"Download All Backups" (one zip of every company) is gone.** The nightly job (`backup.nightly_all_companies`) queues `backup.company_batch` tasks of 50 companies; each writes `platform-backups/<date>/<company id>.sql.gz`, and one failing company is logged and audited without stopping the rest. Old files are pruned after 30 days (recursive, 1,000 keys per delete request). One company's backup is still downloadable from its row menu.
- **5-minute jobs:** the stale check-out sweep finds stale sessions in one query (it used to run one query per open session); the BioTime sync queues one task per active BioTime server, so a slow server doesn't hold up the others. Migration `0008_scheduled_job_indexes` adds the indexes they use (sessions by status and check-in, location pings by session, users by company and last login).
- **Usage Analytics** counts with 2 grouped queries instead of 4 and is cached for 10 minutes (6.3 s -> 2.6 s, then 21 ms, at 2.4M records).
- **Per-server memory is bounded.** `cache.LocalTTLCache` (size-limited, expiring) is the fallback when Redis isn't connected, via `cache.remember()`; used for usage analytics and the voice name list. Report rebuild locks are a fixed set of 256 (was one per company and report, forever). Revoked impersonation tokens are pruned when they expire. `cache.delete_prefix()` uses SCAN instead of KEYS, which blocked Redis on every company write.
- **Production needs Redis as Celery's broker.** Production has no `REDIS_URL` today, so the worker can't share a queue with the API servers or spread batches across processes; check its logs that the nightly backup and 5-minute jobs actually run. Moving AI extraction, payroll runs and exports to background jobs (item 7) depends on this.

## Pre-calculated account totals: how they work now

- `account_period_totals`: one row per company, branch, account and month (UTC) with posted debit/credit. The trial balance, balance sheet and cost of sales read it instead of every journal line, so their cost no longer grows with years of history.
- Kept current automatically (`app/account_totals.py`): a save that adds/edits/deletes journal entries or lines recomputes the touched months right after the flush; bulk `query().delete()/update()` and batch inserts on journal tables are caught wherever they run and the affected companies are rebuilt at commit.
- Safety: `REPORT_TOTALS_SOURCE=live` (env) makes reports sum journal lines directly, no deploy needed. `python -m app.account_totals` lists companies whose totals differ from their journals; `--rebuild` repairs them. `tests/conftest.py` checks every company after each test run.
- Migration 0007 adds indexes on foreign keys that journal deletes make PostgreSQL check (deleting one invoice's lines took 6.4 s at 1.8M GL rows).

## Database migrations (Alembic): how it works now

- Schema changes live in `backend/app/migrations/versions/`. `app.main.ensure_schema_updates()` is **frozen**: never add to it again.
- `app/migrate.py` `run_migrations()` runs at startup (`RUN_MIGRATIONS_ON_STARTUP=true`, the default). On PostgreSQL it takes an advisory lock so only one worker migrates; the others wait and then find nothing to do.
- First run on an existing database: it creates missing tables, applies the legacy patches once, stamps `0001_baseline`, then upgrades to head. On an empty database: it creates from the models and stamps head.
- New change: `cd backend && alembic revision -m "add x"`, fill in `upgrade()`/`downgrade()`, then `alembic upgrade head` (or just restart the app).
- Optional later: run `python -m app.migrate` as a DigitalOcean pre-deploy job and set `RUN_MIGRATIONS_ON_STARTUP=false`.

## Paged registers: how they work now

- Sales invoices are no longer in the bootstrap (`GET /app-data`). The register loads 50 rows at a time from `GET /app-data/sales-invoices` (`kind`, `q`, `customer`, `product`, `limit`, `offset`), searched on the server.
- Each `salesInvoices` row in `app_data_records` carries summary columns stamped on every save (`party`, `doc_status`, `doc_kind`, `salesperson`, `amount`, `record_date`), see `backend/app/doc_index.py`. Migration `0002_sales_invoice_index` added and backfilled them.
- `amount_paid` is recomputed from customer-payment allocations whenever a payment is saved, edited or deleted (session hooks in `doc_index.py`; bulk deletes call `recompute_sales_paid()` directly). Paid/Partial status is now worked out on the server, not patched onto table rows in the browser.
- Other reads that used to scan the whole on-screen table: `/sales-invoices/summary` (KPI cards), `/open` (receipt allocation), `/by-number`, `/salespeople`, `/stock-movements`.
- Bills, payments and quotations follow the same pattern (`backend/app/routers/registers.py`, migration `0003_more_doc_indexes`): `GET /app-data/registers/{bills|payments|quotations|salesInvoices}` pages and searches (`kind`, `q`, `party`, `contains`, `product`, `sort`, `running`), plus `/summary`, `/by-key`, `/parties`, `/registers/bills/vendor-balances`, `/registers/payments/next-ref` and `/payables/open?side=customer|supplier`.
- Supplier payments now settle bills and purchase records on the server too (`amount_paid`), so bill status, the Bills cards, vendor balances and the payment allocation list all agree without loading every payment.
- Frontend: `defineRegister()` / `loadRegisterPage()` in `app.js` drive the bills table, receipts table, supplier payment cards, bank transactions (with running balance) and quotations; new numbers are saved create-only so a number used on another page is refused.
- Expenses (migration `0004_expense_index`): `/registers/expenses` with `status=`, summary cards and the dashboard direct-expense figure (Direct categories only; "Indirect" no longer counts).
- Purchase documents (uploaded invoice files, often 300+ KB each) load on the Purchases page from `GET /app-data/purchase-documents` (file stripped); `GET /app-data/purchase-documents/{id}` returns one with its file.
- Rota assignments come only from `GET /app-data/records/rotaAssignments/range` (now with `employee_id=`) and `/records/rotaAssignments/by-ids`: the boards load the dates on screen, Copy Previous/Repeat load their source week first, swap requests load their two shifts, and the swap form loads an employee's next year of shifts.
- Employees stay in the bootstrap (every HR screen needs the full staff list, and headcount is small), but their photos no longer do: lists carry `/api/v1/app-data/employee-photo/<record id>?v=<hash>` instead of the base64 image, served as an immutable-cached image. The hash makes the URL unguessable; saving the URL back keeps the stored photo.
- `SPECS` in `doc_index.py` says which payload fields feed the columns for each collection. These columns are the first step of "move these records out of JSON into tables".

## Monitoring: how it works now

- `backend/app/monitoring.py`. Every `/api/` request is timed under its route template, e.g. `GET /api/v1/app-data/registers/{collection}`: count, 5xx errors, p50/p95, database time and query count. Counters build up in memory and are added to Redis every 15 s, so the numbers cover every server copy. Without Redis they cover one process only, and the panel says so.
- Superadmin → System Health → **Slowest Endpoints**: ranked by total server time, for the last 1, 6 or 24 hours.
- Logs: `slow request ...` at or above `SLOW_REQUEST_MS` (1000) and `slow query ...` at or above `SLOW_QUERY_MS` (500). Query logs hold the SQL text only, never values.
- Every API response carries `Server-Timing: app;dur=..., db;dur=...;desc="N queries"`, visible in the browser's Network tab.
- Sentry: install is already in requirements. Set `SENTRY_DSN` (and optionally `SENTRY_TRACES_SAMPLE_RATE`). It doesn't send personal data (`send_default_pii=False`).

## People

Today one developer knows the whole system. At 10,000 companies: 3–5 developers, a DevOps / infrastructure engineer, a QA tester, and customer support.

## Will these changes slow the app down?

No. Done properly they make it faster. Biggest wins: UAE region (≈200–250 ms → 5–20 ms per request), pre-calculated totals, paginated lists, CDN and split frontend. Small costs: a few ms per save, a brief first load of each page's code, heavy actions finishing in the background with a "done" notice. The real risk is the change-over: migrate one module at a time, test on a copy first, keep a fallback.
