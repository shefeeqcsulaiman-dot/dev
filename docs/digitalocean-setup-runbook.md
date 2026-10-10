# DigitalOcean setup runbook (scaling, phase 1)

Everything the code needs is already deployed with `main`; each step below only switches on
something in the DigitalOcean (DO) dashboard (https://cloud.digitalocean.com). Do them in
this order, one at a time. After each one, open https://e4cs.com/health — it must keep
showing `"status":"ok"` and `"db":"ok"`.

**Where env vars go:** Apps → (your app) → **Settings** → **App-Level Environment Variables**
→ **Edit**. Tick **Encrypt** for anything containing a password. **Save** triggers a
redeploy (~5 min); watch it under the app's **Activity** tab until it says *Deployed*.
App-level variables reach both components (the web service and the `worker`).

**Never paste a password or connection string into a chat, ticket or document** — only into
the DO env-var screen. If one leaks, reset it (step 0).

---

## 0. Database passwords (security)

**Done 2026-10-09** for the two clusters pasted earlier. A third cluster
(`app-ee93cc45-…h.db`, users `doadmin` and `dev-db-045074`) was pasted the same day — reset
both of its users too. To reset:

1. **Databases** → click the cluster → **Users & Databases** tab.
2. Next to the user: **⋯** → **Reset password** → confirm.
3. If an app uses that cluster (its `DATABASE_URL` host matches): copy the new connection
   string from the cluster's **Overview → Connection details** (choose *Connection string*),
   paste it into `DATABASE_URL` (and `DATABASE_DIRECT_URL` if set), Save.
4. Check `/health` now and again ~10 min later (open connections survive a password
   change; new ones fail if `DATABASE_URL` still has the old password).

   **Lesson (2026-10-09):** resetting `doadmin` on the live `etaxflow-pgsql` without updating
   `DATABASE_URL` in the same minute took the site down for ~28 min (20:17–20:45). Copy the new
   strings and Save straight after the reset. `/health` now names the cause
   (`"db":"error: wrong password in DATABASE_URL"`, trusted sources, name not found…).

## 1. Redis (biggest single win, ~10 min)

**Done 2026-10-09:** Valkey cluster `valkeyetax` (eviction policy `volatile-lru`, so queued
jobs are never evicted); `/health` shows `"redis":"ok"` on every instance. The live
database is `etaxflow-pgsql` — the pool (step 2) and replica (step 5) go on that cluster.
If `/health` shows `error: …`, it names the likely mistake in `REDIS_URL`.

Turns on report caching (dashboard / summary / trial balance), shared rate limits, cluster-wide
monitoring numbers, and the Celery queue for scheduled jobs.

1. **Databases** → **Create Database** (top right).
2. Engine: **Redis** — if DO no longer lists Redis, pick **Valkey** (Redis-compatible; works
   the same).
3. Datacenter: the **same region as the app** (see Apps → your app → Settings → *Region*).
4. Plan: the smallest (1 GB) is enough to start. Name it e.g. `etaxflow-redis`. **Create**.
   Wait until the status is *Online* (~5 min).
5. In the new cluster: **Settings → Trusted sources → Edit** → add your **app** → Save.
6. **Overview → Connection details** → switch the dropdown to **Connection string** →
   **Copy**. It looks like `rediss://default:…@…ondigitalocean.com:25061`.
7. App env vars → **Add Variable**: key `REDIS_URL`, value = that string, tick **Encrypt**
   → Save. (Paste it as-is; the app adds the TLS setting Celery needs.)
8. After the redeploy: `/health` shows `"redis":"ok"` (today it says
   `disabled (no REDIS_URL)`). Open the app and load the dashboard twice — the second load
   should be faster.

## 2. PgBouncer connection pool (~10 min)

**Done 2026-10-09:** pool `etaxflow-pool` (Transaction mode) on `etaxflow-pgsql`; the site has
been healthy on it since.

**Pool size (2026-10-10): 10 on the 22-connection plan.** It was created at 5 (too few for
real use). 19 was tried and is too many: PgBouncer keeps its pool connections open, DO reserves
~3, so nothing was left for the direct connection each new instance opens at start-up
(`DATABASE_DIRECT_URL`) and deploys hung. Rule: pool size = plan limit − 3 reserved − ~8 for
start-up/migrations. App vars `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5` keep each process's own pool
small behind PgBouncer.

Today's settings can open 225–450 database connections against a ~95-connection plan
(`.do/app.yaml` explains the maths); the pool removes that ceiling.

1. **Databases** → your PostgreSQL cluster (the one in today's `DATABASE_URL`) →
   **Connection Pools** tab → **Create a Connection Pool**.
2. Name: `etaxflow-pool`. Database: the one in `DATABASE_URL` (usually `defaultdb`). User:
   the one in `DATABASE_URL`. Mode: **Transaction**. Size: the plan's connection limit minus
   5 (the screen shows the limit; e.g. 22 → 17, 97 → 92). **Create**.
3. Click the new pool → **Connection details** → **Connection string** → Copy. It ends in
   `:25061/etaxflow-pool?sslmode=require` (port **25061**, database = pool name).
4. App env vars, **both in one Save**:
   - `DATABASE_DIRECT_URL` = the value `DATABASE_URL` has **today** (port **25060**). Encrypt.
     Migrations need this direct connection; never leave it as a placeholder.
   - `DATABASE_URL` = the **pool** string from 3. Encrypt.
5. After the redeploy: `/health` → `"db":"ok"`; log in and save something (an invoice
   draft, a customer) to prove writes work.
6. If anything fails: set `DATABASE_URL` back to the direct string, Save — that undoes it.

## 3. Pre-deploy migrations job (~10 min)

**Postponed 2026-10-09.** Tried and removed: the job ran without `DATABASE_URL` because the
variables are set on the `dev` web component, not at app level, so it migrated a throwaway
SQLite file (`Context impl SQLiteImpl`) and reported success. `python -m app.migrate` now exits
with an error in that case, which fails the deploy (the old version keeps running). Migrations
keep running at start-up, which is fine while the database is small. When adding the job again:
put `DATABASE_URL` and `DATABASE_DIRECT_URL` at **app level** first, check its *Deploy logs* show
`Context impl PostgresqlImpl`, and only then set `RUN_MIGRATIONS_ON_STARTUP=false`. The one-line
App Spec entry that worked:
`jobs: [{name: migrate, kind: PRE_DEPLOY, source_dir: /, dockerfile_path: backend/Dockerfile, run_command: python -m app.migrate, instance_size_slug: apps-s-1vcpu-0.5gb, github: {repo: shefeeqcsulaiman-dot/dev, branch: main, deploy_on_push: true}}]`

Large migrations can take minutes; running them before the new version starts avoids failing
the deploy's health check.

1. Apps → your app → **Create** (or **Add components**) → **Create Resources From Source
   Code** → same GitHub repo `shefeeqcsulaiman-dot/dev`, branch `main`.
2. Resource type: **Job**; *When to run*: **Before every deploy** (pre-deploy).
3. Dockerfile path `backend/Dockerfile`, source dir `/`. **Run command**:
   `python -m app.migrate`. Smallest size is fine.
4. The job uses the app-level env vars (`DATABASE_URL`, `DATABASE_DIRECT_URL`,
   `SECRET_KEY`) automatically; if DO asks, keep them.
5. Then add app env var `RUN_MIGRATIONS_ON_STARTUP` = `false`, Save.
6. Check: in **Activity**, open the deploy → the job's log ends with
   `Database migrated … -> …` or says there is nothing to do, and the deploy finishes.

## 4. Sentry error tracking + alerts (~15 min)

**Postponed 2026-10-09** until there are regular customers (the code is ready; it only needs
`SENTRY_DSN`; `/health` shows `"sentry":"on"/"off"`, and Super Admin → System Health has a
*Send test error to Sentry* button). Meanwhile: a free uptime monitor (e.g. UptimeRobot) on
`https://e4cs.com/health` with keyword `"db":"ok"`, so an outage still emails you.

1. Sign up at https://sentry.io (free plan) → **Create project** → platform **FastAPI** →
   name `etaxflow-api` → Create.
2. Copy the **DSN** it shows (`https://…@o….ingest.sentry.io/…`; also under Project
   Settings → Client Keys).
3. App env vars: `SENTRY_DSN` = the DSN (Encrypt), `SENTRY_ENVIRONMENT` = `production`,
   `SENTRY_TRACES_SAMPLE_RATE` = `0.05`. Save.
4. In Sentry: **Alerts → Create Alert** → *Issues*: "A new issue is created" → email you.
   Add a second: *Number of errors* > 50 in 1 hour → email.
5. In DO: Apps → your app → **Insights** → **Create alert policy**: CPU > 80 % for 5 min,
   Memory > 85 %, and *Restart count* > 0 → your email.
6. Check: Sentry shows the project as receiving events after the next error (or use
   Sentry's "Send test event" button if offered).

## 5. Read replica (~10 min, costs a second database node)

Reports (dashboard, summary, trial balance, branch performance) then read from the replica,
taking load off the main database.

1. **Databases** → your PostgreSQL cluster → **Overview** → **Add a read-only node**
   (or *Settings → Read-only nodes*) → same region → **Create**. Wait for *Online*.
2. Choose the read-only node in **Connection details** → **Connection string** → Copy.
3. App env var `DATABASE_READ_URL` = that string (Encrypt), Save. Never set a placeholder —
   leave it unset until the node exists.
4. Check: dashboard and summary load as before; Super Admin → System Health → *Slowest
   Endpoints* still lists the report endpoints with normal times.

## 6. Optional: www.e4cs.com

1. Apps → your app → **Settings** → **Domains** → **Add Domain** → `www.e4cs.com` →
   "You manage your domain in DigitalOcean" → Add.
2. Wait for the certificate (status *Active*, ~5–15 min), then open https://www.e4cs.com.
   `app.e4cs.com` can be dropped; `e4cs.com` is the main address.

## After all of the above

Tell Claude; it will re-run the PostgreSQL load test (`load-tests/postgres/README.md`)
against a production-like setup and pick the next code work from what it shows. Status of
every item: `docs/scaling-plan-10k.md`.

## Lessons from 2026-10-10 (deploys failing, hangs)

- **Every code deploy failed from 2026-10-09 22:15 to 2026-10-10 07:50** ("container did not
  respond to health checks") and DO rolled back silently each time, so nothing new went live.
  Cause: start-up waited on `pg_advisory_lock()` with no limit. Fixed in `04a7aa4` (45 s
  bounded wait, then serve without the idempotent start-up tasks; server keepalives on the lock
  session). Database connects now give up after 10 s (`3542068`).
- **`/health` shows `"build"`** (constant `BUILD` in `app/main.py`, bump it with deploy-relevant
  changes): the quickest way to see whether a deploy actually went live.
- **Redis had no read timeout:** about 1 in 7 `/health` calls hung 15-20 s after idle periods.
  Fixed in `b32556c` (`socket_timeout`, keepalive, idle health checks; never together with
  `retry_on_timeout`, which retries forever) and `77c95e4` (rate limiter falls back to memory).
- **Only the DEPLOY logs show start-up** (Activity → failed deployment → *View deploy logs*);
  build logs only show the image build.
- **Read-only public load test** (`/health` + front page, from one home connection): ~75
  requests/s with no errors after the fixes; the same with 2 or 4 instances while app CPU stayed
  ~4 % and database CPU ~10 %, so the limit is probably the test client, not the server.
- **Instance size:** 1 GB / 1 shared vCPU × 2 is enough today (memory ~35-40 %). Upgrade when
  memory stays above ~75 % or restarts appear; prefer more vCPUs over more RAM.

