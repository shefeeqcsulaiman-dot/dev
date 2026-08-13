# BioTime Biometric Connection — Architecture Reference

This is the authoritative reference for TaxFlow's ZKTeco BioTime 9.5 integration — the one biometric attendance source that's a real server-to-server pull connection, as opposed to a device pushing to us or a file upload. `docs/architecture.md` §15.1 links here rather than duplicating this detail.

## 1. Where This Fits

`BiometricDevice` (`backend/app/models.py:995-1016`) is the shared table behind every attendance punch source TaxFlow supports:

```text
BiometricDevice.device_type
|-- ZKTeco F/K/iClock/SpeedFace/ProFace/G/UA/IN/MB Series, Anviz  -- TCP/IP,
|                                                                     via an on-premises bridge (zk_bridge.py) that
|                                                                     pushes punches to this server over HTTP
|-- ZKTeco ADMS, Suprema, Hikvision                                -- HTTP Push / Cloud Server / ADMS —
|                                                                     the device itself calls us; we never call it
|-- Manual / CSV                                                   -- file import, no live connection
`-- ZKTeco BioTime Server                                          -- THIS DOCUMENT. A pull connection against
                                                                        the customer's own self-hosted BioTime
                                                                        9.5 install.
```

All four converge on one `attendance_punches` table (`AttendancePunch`), tagged by `source` (`"biotime"` for this integration) and `device_id`. Everything downstream of that table — attendance sessions, late/early/overtime calculation, payroll — is source-agnostic and out of scope for this document; see `docs/hrms-architecture.md` for that side.

## 2. Data Model

```text
BiometricDevice (models.py:995-1016)
  id, company_id, name, device_type, status, last_sync

  BioTime-specific columns (populated only when device_type ==
  "ZKTeco BioTime Server"; NULL for every other device_type):
    biotime_base_url          -- customer's BioTime server URL, plain text
    biotime_username          -- plain text
    biotime_password_enc      -- Fernet-encrypted, never stored/returned plain
    biotime_token             -- cached JWT from the last successful auth
    biotime_token_expires_at  -- assumed expiry (BioTime doesn't advertise a real TTL)
```

**Encryption at rest** (`backend/app/crypto.py`): `encrypt_secret()`/`decrypt_secret()` wrap `cryptography.fernet.Fernet`, keyed by `sha256(SECRET_KEY)` — the same key that signs every JWT in the app. Deliberately not a second, separately-provisioned/rotated secret; the module's own docstring is explicit that this doesn't lower the bar for what a compromised `SECRET_KEY` would already mean. `crypto.py` currently has exactly one caller (this password field) — a second use case should reuse it rather than inventing a parallel scheme.

## 3. REST Client (`backend/app/biotime_client.py`)

Stateless by design — every function takes `base_url`/`token` explicitly, so this module never needs to know which company or device it's serving. That scoping lives entirely in `biotime_sync.py` and the calling endpoint.

```text
get_token(base_url, username, password) -> str
  POST {base_url}/jwt-api-token-auth/
  Returns the bare JWT (no "JWT " prefix — callers add that when building
  the Authorization header, matching BioTime's own documented convention)

list_terminals(base_url, token) -> list[dict]
  GET {base_url}/iclock/api/terminals/
  Follows the response's own "next" pagination link until exhausted

list_transactions(base_url, token, start_time, end_time) -> list[dict]
  GET {base_url}/iclock/api/transactions/
  Time-windowed [start_time, end_time), page_size=200, paginated via "next"

get_valid_token(base_url, username, password, cached_token, cached_expires_at)
  -> (token, expires_at)
  Reuses the cached token unless within 5 minutes of its assumed expiry.
  BioTime's auth response carries no TTL of its own, so a conservative
  fixed 6-hour lifetime is assumed and refreshed PROACTIVELY — not
  reactively retried on a 401 — to keep the sync path linear.
```

Every network call raises `BioTimeError` on failure (unreachable server, non-200 response, missing token in a successful-looking response) — its message is written to be safe to show verbatim in the "Test Connection" UI, not just logged.

## 4. Sync Bridge (`backend/app/biotime_sync.py`)

`sync_biotime_device(db, device) -> int` (returns count of newly-inserted punches) is the **single** code path both the manual and scheduled triggers call — one implementation, so behavior never diverges based on what triggered it.

```text
sync_biotime_device(db, device):
        |
        v
  Decrypt device.biotime_password_enc
        |
        v
  biotime_client.get_valid_token(...) -- reuse cached token or re-authenticate
        |
        v
  start = device.last_sync or (now - 24h)   -- 24h lookback on first run only
        |
        v
  biotime_client.list_transactions(start, now)
        |
        v
  De-dupe against EVERY existing AttendancePunch for this device (not just
  ones >= start — start is last_sync, which advances every run, so a
  transaction BioTime re-returns from an overlapping window, or a
  backfilled older punch, would otherwise fall outside a narrower dedupe
  check and get inserted twice)
        |
        v
  Dedupe key = (employee_id, punch_time.isoformat())
  -- isoformat STRINGS, not raw datetime objects: SQLite round-trips
     DateTime(timezone=True) columns as naive (tzinfo stripped on
     read-back), so comparing a freshly-parsed tz-aware datetime against
     one read from the DB would silently never match and defeat the
     dedupe entirely
        |
        v
  New rows: AttendancePunch(source="biotime", direction=_map_direction(row), ...)
        |
        v
  device.last_sync = now ; commit
```

**Direction mapping** (`_map_direction()`): ZKTeco's `punch_state` convention varies by firmware/config. Common codes (`0`/`"check in"`/`"in"` → `"in"`; `1`/`"check out"`/`"out"` → `"out"`) are covered; anything else maps to `"unknown"` rather than guessing wrong.

**Timezone handling**: BioTime reports local wall-clock time with no timezone info attached — same as every other device source this app supports. A fixed `+4h` UAE offset (`_DEVICE_UTC_OFFSET`, matching the identical constant already used in `attendance.py`) converts device-local time to UTC for storage and back to compute the punch's local date. This is safe specifically because the UAE has no DST. **This integration would need real per-company timezone conversion, not a hardcoded offset, before it could serve a company outside the UAE.**

## 5. API Surface (`backend/app/routers/attendance.py`, `gated_router` — requires `require_module("hrms")` + `get_current_user`)

```text
POST   /api/v1/attendance/devices
  device_type="ZKTeco BioTime Server" branch: requires base_url + username +
  password, encrypts the password, saves the device with status="active".
  No api_key_hash is issued (unlike push-type devices) — this is a pull
  connection, not one we hand a key to.

GET    /api/v1/attendance/devices
  Returns biotime_base_url/biotime_username for BioTime devices; password
  is never included in any response, encrypted or otherwise.

DELETE /api/v1/attendance/devices/{id}
  Soft delete (status="deleted") — the row and its sync history stay.

POST   /api/v1/attendance/devices/{id}/test
  "Test Connection." For a BioTime device: authenticate + list terminals,
  used purely as the connectivity check. Success message names the actual
  terminals found ("Connected — 3 terminal(s): Main Gate, Warehouse, HQ
  Lobby"), not just "ok" — caches the fresh token on the device row.

POST   /api/v1/attendance/devices/{id}/biotime/sync
  "Sync Now" — calls biotime_sync.sync_biotime_device() directly, the
  exact same function the scheduled task uses. Returns the count of newly
  inserted punches.
```

## 6. Scheduled Sync (`backend/app/worker.py`)

```text
Celery beat schedule: "hr-sync-biotime-devices", every 300 seconds
        |
        v
sync_biotime_devices() task:
  SELECT * FROM biometric_devices
  WHERE device_type = "ZKTeco BioTime Server" AND status = "active"
  -- across EVERY company, one task run
        |
        v
  For each device, independently:
    try: biotime_sync.sync_biotime_device(db, device)
    except: log a warning, db.rollback(), continue to the next device
```

One company's BioTime server being unreachable, misconfigured, or slow never blocks another company's sync — each device is wrapped in its own `try/except` inside the loop, deliberately not a single query/commit spanning every company. Requires an actual Celery beat process running `app.worker` as the schedule source; the API process alone does not execute this on a timer.

## 7. Known Risks / Open Items

- **Transaction API field names are inferred, not confirmed against BioTime's own documentation.** `list_transactions()`'s field names (`emp_code`, `punch_time`, `terminal_sn`/`terminal_alias`, `punch_state`) follow the convention documented for BioTime's sibling Terminal/Personnel endpoints — the Transaction API's own doc page wasn't available when this was written. These should be reconciled against a real server's actual response the first time this runs against genuine production data. `_map_direction()`'s fallback to `"unknown"` for an unrecognized `punch_state` is the existing defense against this being wrong in a specific, contained way (a punch with no clear direction) rather than silently mislabeling in/out.
- **Fixed UAE (+4h, no DST) offset** — correct for this app's current target market, a hardcoded assumption that would need to become real per-company timezone handling to serve a company outside the UAE.
- **6-hour assumed token TTL** is a guess, not a value BioTime's auth response actually provides — if a real deployment's token lives for a different duration, the proactive-refresh margin (5 minutes before assumed expiry) may refresh unnecessarily often or, if the real TTL is shorter than assumed, run into occasional reactive 401s (not handled — this path assumes proactive refresh is sufficient, see `get_valid_token()`'s docstring).
- **Device management endpoints use `get_current_user`** (User/admin-only), not the Principal-aware permission system used elsewhere in the app — consistent with `hrms`-gated endpoints generally being admin-facing, but worth confirming against `docs/hrms-architecture.md` §9 if HR-role-based access to device management is ever wanted.
