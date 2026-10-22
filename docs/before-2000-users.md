# Before onboarding 2,000 users (many companies)

Written 2026-10-22. Three settings only the DigitalOcean account owner can change. The code for
each is already in place; nothing works differently until the values are set.

## 1. Backup and upload storage (most important)

Today live has no storage keys, so nightly backups and uploaded files (receipts, sick notes,
documents) sit on the server's own disk and are lost on every deploy.

1. DigitalOcean → **Spaces Object Storage** → **Create Bucket**: region **NYC3** (same as the
   app), name `etaxflow-documents`, **Restrict File Listing** (private).
2. **API** → **Spaces Keys** → **Generate New Key**. Copy the key and the secret (the secret is
   shown once).
3. **Apps** → `etaxflow` → **Settings** → for **both** components, `api` and `worker` →
   **Environment Variables** → set:

   | Key | Value | Encrypt |
   |---|---|---|
   | `S3_ENDPOINT_URL` | `https://nyc3.digitaloceanspaces.com` | no |
   | `S3_BUCKET` | `etaxflow-documents` | no |
   | `S3_ACCESS_KEY_ID` | the key from step 2 | yes |
   | `S3_SECRET_ACCESS_KEY` | the secret from step 2 | yes |
   | `AWS_REGION` | `nyc3` | no |

4. Save. The app redeploys by itself (a few minutes). Then tell Claude, which checks that a
   backup and an upload land in the bucket.

## 2. Database connection pool

The 503 at 10:16 on 2026-10-22 was the app waiting more than 10 s for a connection through
the pool (`etaxflow-pool`, port 25061). At 2,000 users the pool must not be the limit.

1. **Databases** → `etaxflow-pgsql` → **Overview**: note the plan's RAM. DigitalOcean allows
   about 25 connections per GB, minus 3 it keeps: 1 GB → 22, 2 GB → 47, **4 GB → 97**, 8 GB → 197.
2. For 2,000 users, use at least the **4 GB** plan (**Resize**; a few seconds of downtime, do it
   out of hours).
3. **Connection Pools** → `etaxflow-pool` → **Edit**: mode **Transaction**, **pool size = the
   plan's limit minus about 7** (90 on a 4 GB plan). Keep the database `defaultdb` and the
   user as they are.

The app's own settings (3 processes per server, 10 + 15 connections each) stay as they are:
they talk to the pool, which is cheap, not to the database directly.

## 3. Error alerts (Sentry)

Error tracking is off on live (`/health` says `"sentry":"off"`), so errors are only seen when
someone opens Super Admin → Errors.

1. sentry.io → create a free account → **Create Project** → platform **FastAPI** → copy the
   **DSN** (`https://…@….ingest.sentry.io/…`).
2. App Platform → `etaxflow` → both `api` and `worker` → add `SENTRY_DSN` = the DSN, **encrypted**.
3. In Sentry → **Alerts** → **Create Alert** → "a new issue is created" → email to you.

After the redeploy, `/health` shows `"sentry":"on"`.

## 4. App servers (optional, cost)

Live runs 3 servers (2 CPUs each) and adds up to 6 when CPU stays above 70%, but adding a
server takes several minutes: too slow for a shift-start rush (most staff opening ESS around
9:00). Load tests put one server at about 500–600 people online at once. If the first weeks
show slow mornings in Super Admin → System Health, raise **Apps** → `etaxflow` → `api` →
**minimum instances** from 3 to 4.
