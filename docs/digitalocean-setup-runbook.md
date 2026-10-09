# DigitalOcean setup runbook (scaling, phase 1)

Everything the code needs is already deployed with `main`; each step below only switches on
something in the DigitalOcean (DO) dashboard. Do them in this order. After each one, check
https://e4cs.com/health — it must keep showing `"status":"ok","db":"ok"`.

Where env vars go: **Apps → (your app) → Settings → App-Level Environment Variables**
(tick **Encrypt** for anything with a password). Saving triggers a redeploy (~5 min).

---

## 0. Database passwords (security — do first)

**Done 2026-10-09:** both database passwords that had been pasted into a chat were reset;
the site stayed healthy afterwards. Steps kept for any future reset:

| Host | User |
|---|---|
| `e4cs-do-user-38425833-0.g.db.ondigitalocean.com` | `doadmin` |
| `app-483499f0-…-do-user-38425833-0.h.db.ondigitalocean.com` | `db` |

1. Find which one the app uses: app env var `DATABASE_URL` (host part).
2. **Databases → cluster → Users & Databases → user → ⋯ → Reset password.**
3. If the app uses it: paste the new connection string into `DATABASE_URL` (and
   `DATABASE_DIRECT_URL` if set) and save.
4. Check `/health` → `"db":"ok"`, and again ~10 minutes later (open connections survive a
   password change; new ones would fail if `DATABASE_URL` still had the old password).

## 1. Redis (biggest single win)

Turns on: report caching (dashboard/summary/trial balance), shared rate limits, cluster-wide
monitoring numbers, and the Celery broker for scheduled jobs.

1. **Databases → Create Database Cluster → Redis** (same region as the app; smallest plan is
   fine to start).
2. **Trusted sources:** add the app.
3. Copy the **connection string** (`rediss://default:…@…:25061`).
4. App env var `REDIS_URL` = that string (encrypted). Save.
5. Check `/health` → `"redis":"ok"` (it says `disabled (no REDIS_URL)` today).

## 2. PgBouncer (connection pooling)

Today's pool settings can open 225–450 database connections against a ~95-connection plan
(`.do/app.yaml` explains the maths); PgBouncer removes that ceiling.

1. **Databases → your PostgreSQL cluster → Connection Pools → Create**: mode **Transaction**,
   database `defaultdb`, user `doadmin` (or the app's user), size ≈ the plan's connection
   limit minus 5.
2. App env vars, **both at once**:
   - `DATABASE_URL` = the **pool's** URI (port **25061**, database = pool name)
   - `DATABASE_DIRECT_URL` = the **direct** URI you use today (port **25060**). Migrations
     need it; never leave it as a placeholder.
3. Check `/health`, then open the app and save something.

## 3. Pre-deploy migrations

Large migrations can take minutes; run them before the new version starts instead of during
start-up (which can fail the deploy's health check).

1. **Apps → your app → Create → Job → Pre-deploy job**, same repo/Dockerfile, run command:
   `python -m app.migrate`, same env vars as the web service (`DATABASE_URL`,
   `DATABASE_DIRECT_URL`, `SECRET_KEY`).
2. App env var `RUN_MIGRATIONS_ON_STARTUP` = `false`.
3. Check the next deploy's job log ends with no error (`Database migrated … -> …` or nothing
   to do).

## 4. Sentry (error tracking + alerts)

1. Create a free project at sentry.io (platform: Python / FastAPI).
2. App env vars: `SENTRY_DSN` = the project's DSN; optional `SENTRY_ENVIRONMENT=production`,
   `SENTRY_TRACES_SAMPLE_RATE=0.05`.
3. In Sentry: **Alerts → Create alert** for new issues and for error-rate spikes (email/Slack).
4. Also in DO: **Apps → your app → Insights → Alerts** for CPU > 80 % and restarts.

## 5. Read replica (reports off the main database)

1. **Databases → your PostgreSQL cluster → Add read-only node** (same region).
2. App env var `DATABASE_READ_URL` = the replica's connection string (encrypted).
   Never a placeholder — leave it unset until the node exists.
3. Check: Super Admin → System Health → Slowest Endpoints still shows reports working; the
   dashboard and summary load as before.

## 6. Optional

- **www.e4cs.com:** Apps → Settings → Domains → Add domain `www.e4cs.com` (DO manages it).
  `app.e4cs.com` can be dropped; `e4cs.com` is the main address.
- **Local PostgreSQL for testing:** the PostgreSQL 16 service on the dev machine — share its
  `postgres` password (not a production one) so migrations can be tested on PostgreSQL locally.

## After all of the above

Run the PostgreSQL load test (`load-tests/postgres/README.md`) against a production-like
setup, then pick the next code work (splitting `app.js`, tables as the source of truth) from
what it shows. Current status of every item: `docs/scaling-plan-10k.md`.
