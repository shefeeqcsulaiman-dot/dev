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
- [~] Alembic migrations (in progress)
- [ ] PgBouncer and a bigger database
- [ ] Paginated lists and server-side Sales totals
- [ ] Monitoring
- [ ] PostgreSQL load test

**Up to about 2,000 companies:**
- [ ] Move invoices, purchases, rota and attendance out of JSON into tables
- [ ] Pre-calculated totals
- [ ] Read replica
- [ ] Background jobs for heavy work
- [ ] Split frontend

**Up to 10,000 companies:**
- [ ] AWS or Azure UAE with Kubernetes
- [ ] Partitioning
- [ ] Multi-region disaster recovery
- [ ] Security certification (ISO 27001 / SOC 2)
- [ ] Accredited e-invoicing provider status

## Database migrations (Alembic): how it works now

- Schema changes live in `backend/app/migrations/versions/`. `app.main.ensure_schema_updates()` is **frozen**: never add to it again.
- `app/migrate.py` `run_migrations()` runs at startup (`RUN_MIGRATIONS_ON_STARTUP=true`, the default). On PostgreSQL it takes an advisory lock so only one worker migrates; the others wait and then find nothing to do.
- First run on an existing database: it creates missing tables, applies the legacy patches once, stamps `0001_baseline`, then upgrades to head. On an empty database: it creates from the models and stamps head.
- New change: `cd backend && alembic revision -m "add x"`, fill in `upgrade()`/`downgrade()`, then `alembic upgrade head` (or just restart the app).
- Optional later: run `python -m app.migrate` as a DigitalOcean pre-deploy job and set `RUN_MIGRATIONS_ON_STARTUP=false`.

## People

Today one developer knows the whole system. At 10,000 companies: 3–5 developers, a DevOps / infrastructure engineer, a QA tester, and customer support.

## Will these changes slow the app down?

No. Done properly they make it faster. Biggest wins: UAE region (≈200–250 ms → 5–20 ms per request), pre-calculated totals, paginated lists, CDN and split frontend. Small costs: a few ms per save, a brief first load of each page's code, heavy actions finishing in the background with a "done" notice. The real risk is the change-over: migrate one module at a time, test on a copy first, keep a fallback.
