# Biometric Attendance Connection — Architecture Reference

This is the authoritative reference for how TaxFlow gets attendance punches out of a customer's biometric hardware and into `AttendancePunch`. `docs/architecture.md` §15.1 links here rather than duplicating this detail.

Five connection methods are **shipped and live**; a sixth — a **TaxFlow Biometric Agent** — is being added alongside them, additive, nothing replaced. Its first mode (Agent → local BioTime) is **shipped as a v1 script** (`backend/biotime_agent.py`); its second mode (Agent → direct device) and all of the requested operational polish (Windows service packaging, auto-update, remote diagnostics, LAN discovery) are still **proposed, not yet built**. The Agent gives customers who can't expose a device or a BioTime server to the internet a way in, using the same downstream pipeline every other method already uses.

**ADMS vs. ADMS Classic** — "ADMS / HTTP Push" (shipped earlier) is a generic webhook: it works for any device firmware that lets you type an arbitrary server URL plus a custom header or key. **ADMS Classic** (added 2026-08-26) is the *other* case: a device whose own menu has only a fixed Server IP + Port field — the real ZKTeco "ADMS Cloud Server Mode" wire protocol (`GET/POST /iclock/cdata?SN=...`, `GET /iclock/getrequest?SN=...`), identified by hardware serial number instead of a bearer key. These routes are mounted at the true server root (`app.include_router(attendance.iclock_router)`, no `/api/v1` prefix) since the device's firmware hardcodes that exact path and can't be pointed anywhere else. See `attendance.py`'s `iclock_router`, `_get_device_by_serial()`, and `_ingest_device_punch()` (the dedupe/insert core shared with the regular webhook path). Known limitation: many ADMS-Classic-only terminals speak plain HTTP, not HTTPS/TLS — a device without an SSL toggle on that menu screen cannot reach a TLS-only host like `app.e4cs.com` regardless of what's configured; those customers still need `zk_bridge.py`.

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
 (shipped)  (shipped)   (shipped)   (Mode 2 shipped v1,
                                     Mode 1 proposed)
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
| ZKTeco ADMS / HTTP Push | **Shipped** | Device calls us directly (custom URL + key/header) | Devices with configurable cloud-push support |
| ZKTeco ADMS Classic | **Shipped** | Device calls `/iclock/cdata` directly (fixed Server IP + Port, serial-number identity, no key) | Devices whose menu offers only a Server IP + Port field |
| BioTime Server (pull) | **Shipped** | We call the customer's BioTime server | Customers already running BioTime 9.5, reachable from the internet |
| Manual / CSV | **Shipped** | File import, no live connection | Backup, or hardware none of the above cover |
| **TaxFlow Agent — Mode 2** (`backend/biotime_agent.py`) | **Shipped (v1 script)** | Runs on the customer's LAN, pulls from a LAN-local BioTime server, pushes to us | Customers with a BioTime server that **cannot** be exposed to the internet |
| **TaxFlow Agent — Mode 1** | **Proposed** | Same Agent, direct-to-device instead of via BioTime | Customers with ZKTeco/Anviz devices who also can't run `zk_bridge.py` reachably (functionally covered by `zk_bridge.py` already — lower priority) |

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

## 3. TaxFlow Biometric Agent

### Why

The four shipped methods all require the customer's network to be reachable in one direction or the other: `zk_bridge.py` needs someone to keep a script running and reachable enough to hit our API outbound (usually fine — outbound is rarely blocked); BioTime pull requires TaxFlow's servers to reach the customer's BioTime install *inbound*, which many customers' IT/security policy won't allow. There's currently no answer for "customer has a BioTime server, but won't or can't open inbound access to it." The Agent closes that gap by always initiating outbound, like `zk_bridge.py` already does — it's a delivery/packaging problem, not a new protocol problem.

### Mode 2 — Agent → local BioTime server (shipped, v1 script)

`backend/biotime_agent.py` — a standalone script, sibling to `zk_bridge.py` in every way that matters: same config-file-then-env-var-then-default convention (`biotime_agent.conf`), same logging style, same "run manually or under PM2/systemd" usage, same per-device API key auth.

```text
biotime_agent.py, running ON the customer's LAN:
        |
        v
  _get_token() / BioTime auth  -- duplicates biotime_client.py's exact
        |                          get_token() request shape (kept in sync
        v                          manually — this script can't import the
  _list_transactions()             backend package, it runs standalone on
        |                          a machine that doesn't have it installed)
        v
  Sort pulled transactions oldest-first, map fields (emp_code -> employee_id,
  punch_state -> direction via the same in/out/unknown mapping as
  biotime_sync._map_direction()), apply the fixed UTC offset
        |
        v
  POST each punch to {API_BASE_URL}/api/v1/punch  (X-Device-Key header) --
  the exact same short-alias endpoint and header zk_bridge.py already uses;
  an Agent-pushed punch and a zk_bridge-pushed punch are indistinguishable
  to the backend, both land with source="device"
        |
        v
  Watermark (biotime_agent_state.json) only advances through a CONTIGUOUS
  run of successful posts -- stops the whole cycle at the first failed
  POST rather than skipping past it, so a transient failure can never
  silently lose a punch. The backend's own idempotency guard (company +
  employee + punch_time + device) makes re-sending an already-received
  punch on the next cycle harmless, so this trades a little redundant
  network traffic for a hard guarantee against data loss.
```

Verified locally end-to-end against a mock BioTime server and a real backend: punches sync correctly with the right direction mapping (including an unrecognized `punch_state` correctly falling through to `"unknown"` rather than being guessed), a second sync cycle sends zero duplicates, and a script-level unit test confirmed the failure-safety behavior specifically — a mid-batch POST failure stops that cycle's watermark advance at the last success, and the next cycle correctly retries the failed punch and everything after it, in order, without re-sending what already succeeded.

**Setup** (`HRMS → Attendance → Devices → Add Device`): pick any `device_type` other than `"ZKTeco BioTime Server"` (that type is reserved for the built-in pull connection and issues no API key) — `"BioTime via Agent"` is a reasonable label. Copy the issued API key into `biotime_agent.conf` alongside the local BioTime server's own URL/username/password, then run the script on a machine that can reach that BioTime server.

**Not yet done for this mode**: auto-update, remote diagnostics, LAN discovery — see below. Windows service packaging (the first of the four requested operational qualities) is now partially done — see the update below — applied directly to both `zk_bridge.py` and `biotime_agent.py` rather than requiring a separate "Mode 1"/"Mode 2" Agent build first.

### Mode 1 — Agent → Device (proposed, not yet built)

```text
Same protocol zk_bridge.py already speaks (ZKTeco TCP/IP, pyzk-compatible;
Anviz where supported). Difference would be packaging and operational
quality, not the wire protocol — functionally, zk_bridge.py already covers
this case, so building a second implementation of it is lower priority
than the operational-polish items below, which would benefit BOTH modes
once built.
```

### Windows packaging (shipped, CI-verified — 2026-08-17 update)

Rather than building a separate "Agent" binary before either script had any
packaging at all, the operational-polish work below was applied directly to
`zk_bridge.py` and `biotime_agent.py`:

- Both scripts gained an `--install-startup` flag: registers a **per-user**
  Windows Scheduled Task (`schtasks /create /sc onlogon ...`, no
  Administrator elevation needed) so the script survives a reboot without a
  terminal window staying open. Deliberately duplicated in both files
  rather than shared via an import — each script is still meant to be
  downloaded and run standalone, with no other project files alongside it.
- `zk_bridge.py`'s previous behavior — log a warning and keep running
  against the placeholder `192.168.1.201` IP when nothing was configured —
  is now a hard, clear `sys.exit()` instead, matching the stricter
  validation `biotime_agent.py` already had. An unconfigured install now
  fails loudly and immediately rather than looping forever, silently
  failing every cycle.
- `backend/agent/` (new directory): PyInstaller specs package each script
  into a single-file Windows `.exe` (`TaxFlowZkBridge.exe`,
  `TaxFlowBioTimeAgent.exe`) — no separate Python install needed on the
  customer's machine. See `backend/agent/README.md` for build steps and
  two explicit caveats: the binaries are **unsigned** (will very likely
  trigger a Windows SmartScreen warning on first run — not a bug, a known
  PyInstaller heuristic trigger; "More info → Run anyway"), and
  **auto-update is not implemented** — re-running the build and
  re-downloading is the only update path today.
- `.github/workflows/build-biometric-agents.yml` (new, and the first CI
  workflow in this repo): a `windows-latest` runner builds both
  executables and verifies the build succeeds, each exe fails fast and
  clearly (not a hang or a crash) when unconfigured, and `--install-startup`
  actually creates a working Scheduled Task. **What this does not verify**:
  real protocol behavior against actual ZKTeco/BioTime hardware, or that
  the Scheduled Task survives a real reboot on a real customer PC — both
  need on-site validation before this is relied on operationally. (Locally
  testing `--install-startup` during development hit `schtasks`
  "Access is denied" in that specific sandboxed shell even for a per-user
  task — the code's failure-handling path was confirmed correct there
  [clean error, no crash], but the actual success path could only be
  confirmed via the CI runner, which has normal Task Scheduler permissions.)

**Still not done**: auto-update, remote diagnostics, LAN device discovery — unchanged from below, all three remain deliberately out of scope (see "Open questions" below for why).

### Requested operational qualities (partially shipped — see above; auto-update/diagnostics/discovery not yet built)

- ~~**Windows-based**~~ — packaging shipped 2026-08-17, see above. CI-verified; hardware/reboot behavior still needs on-site validation.
- **Auto-updating** — `zk_bridge.py`, `biotime_agent.py`, and now the packaged `.exe`s all still require a customer or reseller to manually pull updates. Not attempted this pass — see "Open questions" below for why.
- **Remotely diagnosable** — TaxFlow support should be able to see Agent health/last-sync/errors without a site visit or asking the customer to read logs over the phone (a natural extension of `BiometricDevice.last_sync` and the existing "Test Connection" pattern, surfaced per-Agent instead of per-device). Not attempted this pass.
- **LAN device discovery** — instead of a customer hand-typing a device IP (today's `ip_address`/`port` fields on `BiometricDevice`), the Agent scans the LAN and lets the customer pick from what it finds. Meaningfully lowers setup friction versus the current manual-IP/manual-URL config file approach — but deliberately not attempted this pass, see "Open questions" below.

### What does *not* change

- `BiometricDevice`, `AttendancePunch`, and every existing API endpoint (`/api/v1/attendance/devices`, `.../{id}/test`, `.../{id}/biotime/sync`, `/api/v1/punch`) stay exactly as they are — confirmed by shipping Mode 2 without touching any of them. The Agent is a new *client* of this existing surface, not a reason to redesign it.
- The four shipped methods are not deprecated by this. A customer who's already running `zk_bridge.py` successfully has no reason to migrate; the Agent is offered alongside as the option for customers the current four don't serve.
- BioTime pull (§4) stays the right choice for a customer whose BioTime server genuinely is reachable from the internet — Agent Mode 2 exists specifically for the case where it isn't, not as a universal replacement for the simpler pull path.

### Open questions before building the operational-polish layer

- Auto-update mechanism and integrity (signed releases? a TaxFlow-hosted update channel?) — a compromised auto-updater on a customer's LAN is a real blast-radius question worth deciding deliberately, not defaulting on.
- LAN discovery's own security posture — scanning a customer's network is more intrusive than polling one known IP; needs explicit customer opt-in/scope, not silent broad scanning.
- Whether Agent-pushed punches keep the exact `zk_bridge.py`-style per-device API key model v1 shipped with, or need their own auth scheme once one Agent instance might proxy for *multiple* devices/BioTime servers at once, unlike one script instance per connection today.

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
