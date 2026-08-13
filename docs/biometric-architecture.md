# Biometric Attendance Connection — Architecture Reference

This is the authoritative reference for how TaxFlow gets attendance punches out of a customer's biometric hardware and into `AttendancePunch`. `docs/architecture.md` §15.1 links here rather than duplicating this detail.

Four connection methods are **shipped and live** today; a fifth — a **TaxFlow Biometric Agent** — is a **proposed, not-yet-built** addition, documented here so the design is settled before implementation starts. This is additive: nothing shipped is being replaced. The Agent gives customers who can't expose a device or a BioTime server to the internet a way in, using the same downstream pipeline every other method already uses.

## 1. Connection Methods

```text
                         TaxFlow HRMS
                              │
                     Attendance Engine
                              │
                       AttendancePunch
                              │
              ┌───────────────┴────────────────┐
              │                                 │
      Biometric Gateway                   Manual / CSV  (shipped)
              │
   ┌──────────┼───────────┬──────────────┐
   │          │           │              │
   ▼          ▼           ▼              ▼
zk_bridge   ADMS/Push   BioTime      TaxFlow Agent
 (shipped)  (shipped)   (shipped)     (proposed)
   │          │           │              │
   ▼          ▼           ▼              ▼
ZKTeco     ZKTeco ADMS  Customer's   ZKTeco/Anviz device
TCP/IP     Suprema      BioTime 9.5  on customer LAN, OR
devices    Hikvision    server       the customer's BioTime
                                      server, when neither
                                      is reachable from the
                                      internet directly
```

| Method | Status | Direction | Best for |
| --- | --- | --- | --- |
| `zk_bridge.py` → TaxFlow | **Shipped** | On-prem script polls the device, pushes punches to us via API key | Existing direct-TCP/IP ZKTeco/Anviz customers |
| ZKTeco ADMS / HTTP Push | **Shipped** | Device calls us directly | Devices with native cloud-push support |
| BioTime Server (pull) | **Shipped** | We call the customer's BioTime server | Customers already running BioTime 9.5, reachable from the internet |
| Manual / CSV | **Shipped** | File import, no live connection | Backup, or hardware none of the above cover |
| **TaxFlow Biometric Agent** | **Proposed** | Runs on the customer's LAN; talks to a device directly OR to a LAN-local BioTime server, then pushes to us | Customers who **cannot** expose a device or BioTime to the internet — the gap none of the four shipped methods cover |

`BiometricDevice` (`backend/app/models.py:995-1016`) is the shared table behind every method above; all converge on one `attendance_punches` table (`AttendancePunch`), tagged by `source` and `device_id`. Everything downstream — attendance sessions, late/early/overtime, payroll — is source-agnostic; see `docs/hrms-architecture.md` for that side. None of the existing API endpoints change to add the Agent — see §3.

**Correction to a common assumption**: TaxFlow already has a proto-Agent. `zk_bridge.py` (repo root `backend/`) is a standalone Python script a customer runs on-premises — it polls a ZKTeco/Anviz device over TCP/IP via `pyzk` and forwards punches to `POST /api/v1/attendance/punch` using a per-device API key (`HRMS → Attendance → Devices → Add Device` issues the key). The proposed Agent (§3) isn't a new idea from zero — it's this same concept, productized: packaged as a Windows service instead of a manually-run script, auto-updating, remotely diagnosable, capable of discovering devices on the LAN instead of requiring a hand-typed IP, and — the actual new capability — able to talk to a **local BioTime server** on the customer's behalf, which `zk_bridge.py` cannot do today (it only speaks the raw ZKTeco TCP/IP protocol, not BioTime's REST API).

## 2. Normalization Principle

Every method should reduce to the same shape before it reaches `AttendancePunch`, regardless of source:

```text
Device
   |
   v
Connection Adapter        (per-source: zk_bridge poll loop, ADMS push handler,
   |                        biotime_sync.py, the proposed Agent's own client)
   v
Normalized Punch           { employee_id, punch_time (UTC), direction, device_id, source }
   |
   v
AttendancePunch            (one table, one shape, source-agnostic downstream)
   |
   v
Attendance Calculation  ->  Payroll / WPS
```

**Current state, honestly**: this normalization already happens in *effect* — every source ends up writing the same `AttendancePunch` shape — but there isn't one shared adapter interface today. Each source has its own bespoke ingestion path (`biotime_sync.sync_biotime_device()` for BioTime, the punch-in endpoint `zk_bridge.py`/ADMS devices call, the CSV import handler) rather than implementing a common `Adapter.pull() -> list[NormalizedPunch]` contract. That's not a defect — four independent paths converging on one well-defined output table is a reasonable design at this scale — but if the Agent (§3) is built as a genuinely new adapter, it's worth deciding then whether to formalize a shared interface across all methods or keep adding sources as independent converging paths, which has worked fine so far.

## 3. TaxFlow Biometric Agent (proposed — not yet built)

### Why

The four shipped methods all require the customer's network to be reachable in one direction or the other: `zk_bridge.py` needs someone to keep a script running and reachable enough to hit our API outbound (usually fine — outbound is rarely blocked); BioTime pull requires TaxFlow's servers to reach the customer's BioTime install *inbound*, which many customers' IT/security policy won't allow. There's currently no answer for "customer has ZKTeco devices or a BioTime server, but won't or can't open inbound access." The Agent closes that gap by always initiating outbound, like `zk_bridge.py` already does — it's a delivery/packaging problem, not a new protocol problem.

### What it does

Two modes, matching the two things a customer might have:

```text
Mode 1 — Agent -> Device (direct)
  Same protocol zk_bridge.py already speaks (ZKTeco TCP/IP, pyzk-compatible;
  Anviz where supported). Difference is packaging and operational quality,
  not the wire protocol.

Mode 2 — Agent -> local BioTime server
  Agent runs biotime_client.py's exact same calls (get_token,
  list_transactions) but FROM the customer's LAN, against a BioTime server
  that isn't reachable from the internet — then forwards the pulled
  transactions to TaxFlow's existing punch-ingestion API, the same way
  zk_bridge.py forwards direct-device punches today. TaxFlow's own backend
  never talks to this BioTime server directly in this mode; the Agent is
  the only thing that needs network access to it.
```

Either mode ends at the same place: normalized punches pushed to `POST /api/v1/attendance/punch` (or a new Agent-specific ingestion endpoint, if per-Agent auth/versioning needs turn out to want one — an implementation decision, not an architecture one) using a per-device API key, identical in spirit to how `zk_bridge.py` authenticates today.

### Requested operational qualities (from this design discussion)

- **Windows-based** — matches the environment most customer sites already run other on-prem software on.
- **Auto-updating** — `zk_bridge.py` today requires a customer or reseller to manually pull updates; the Agent should update itself.
- **Remotely diagnosable** — TaxFlow support should be able to see Agent health/last-sync/errors without a site visit or asking the customer to read logs over the phone (a natural extension of `BiometricDevice.last_sync` and the existing "Test Connection" pattern, surfaced per-Agent instead of per-device).
- **LAN device discovery** — instead of a customer hand-typing a device IP (today's `ip_address`/`port` fields on `BiometricDevice`), the Agent scans the LAN and lets the customer pick from what it finds. Meaningfully lowers setup friction versus `zk_bridge.py`'s current manual-IP config file.

### What does *not* change

- `BiometricDevice`, `AttendancePunch`, and every existing API endpoint (`/api/v1/attendance/devices`, `.../{id}/test`, `.../{id}/biotime/sync`, the punch-ingestion endpoint) stay exactly as they are. The Agent is a new *client* of this existing surface, not a reason to redesign it.
- The four shipped methods are not deprecated by this. A customer who's already running `zk_bridge.py` successfully has no reason to migrate; the Agent is offered alongside as the option for customers the current four don't serve.
- BioTime pull (§4) stays the right choice for a customer whose BioTime server genuinely is reachable from the internet — Agent Mode 2 exists specifically for the case where it isn't, not as a universal replacement for the simpler pull path.

### Open questions before building

- Auto-update mechanism and integrity (signed releases? a TaxFlow-hosted update channel?) — a compromised auto-updater on a customer's LAN is a real blast-radius question worth deciding deliberately, not defaulting on.
- LAN discovery's own security posture — scanning a customer's network is more intrusive than polling one known IP; needs explicit customer opt-in/scope, not silent broad scanning.
- Whether Agent-pushed punches reuse the exact `zk_bridge.py`-style per-device API key model as-is, or need their own auth scheme given an Agent can proxy for *multiple* devices/a BioTime server at once, unlike one `zk_bridge.py` instance per device today.

## 4. BioTime Server Connection (shipped) — Deep Dive

The rest of this document covers the one shipped method that's a real server-to-server pull connection rather than a device pushing to us or a file upload: `BiometricDevice.device_type == "ZKTeco BioTime Server"`, TaxFlow calling out to the customer's self-hosted ZKTeco BioTime 9.5 install.

### 4.1 Data Model

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

### 4.2 REST Client (`backend/app/biotime_client.py`)

Stateless by design — every function takes `base_url`/`token` explicitly, so this module never needs to know which company or device it's serving. That scoping lives entirely in `biotime_sync.py` and the calling endpoint. **This is also the exact client the proposed Agent's Mode 2 (§3) would reuse from the customer's LAN instead of from TaxFlow's own servers.**

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

### 4.3 Sync Bridge (`backend/app/biotime_sync.py`)

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

### 4.4 API Surface (`backend/app/routers/attendance.py`, `gated_router` — requires `require_module("hrms")` + `get_current_user`)

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

### 4.5 Scheduled Sync (`backend/app/worker.py`)

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

### 4.6 Known Risks / Open Items (shipped BioTime path)

- **Transaction API field names are inferred, not confirmed against BioTime's own documentation.** `list_transactions()`'s field names (`emp_code`, `punch_time`, `terminal_sn`/`terminal_alias`, `punch_state`) follow the convention documented for BioTime's sibling Terminal/Personnel endpoints — the Transaction API's own doc page wasn't available when this was written. These should be reconciled against a real server's actual response the first time this runs against genuine production data. `_map_direction()`'s fallback to `"unknown"` for an unrecognized `punch_state` is the existing defense against this being wrong in a specific, contained way (a punch with no clear direction) rather than silently mislabeling in/out.
- **Fixed UAE (+4h, no DST) offset** — correct for this app's current target market, a hardcoded assumption that would need to become real per-company timezone handling to serve a company outside the UAE.
- **6-hour assumed token TTL** is a guess, not a value BioTime's auth response actually provides — if a real deployment's token lives for a different duration, the proactive-refresh margin (5 minutes before assumed expiry) may refresh unnecessarily often or, if the real TTL is shorter than assumed, run into occasional reactive 401s (not handled — this path assumes proactive refresh is sufficient, see `get_valid_token()`'s docstring).
- **Device management endpoints use `get_current_user`** (User/admin-only), not the Principal-aware permission system used elsewhere in the app — consistent with `hrms`-gated endpoints generally being admin-facing, but worth confirming against `docs/hrms-architecture.md` §9 if HR-role-based access to device management is ever wanted.
