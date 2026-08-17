# Packaging the biometric bridge scripts

`zk_bridge.py` and `biotime_agent.py` (both in `backend/`) are standalone
scripts a customer runs on their own PC to bridge a biometric device or a
local BioTime server to TaxFlow. Historically that meant: install Python,
`pip install pyzk requests`, hand-create a config file, and keep a terminal
window open indefinitely (see `docs/biometric-architecture.md` §3). This
directory packages each script into a single-file Windows executable so
none of that is required, plus a `--install-startup` flag (already in both
scripts) that registers a per-user Windows Scheduled Task so the exe starts
automatically on login — no terminal window, no Administrator elevation.

## Building

On a Windows machine:

```
cd backend/agent
pip install -r requirements-agent.txt
pyinstaller --clean zk_bridge.spec
pyinstaller --clean biotime_agent.spec
```

Output: `dist/TaxFlowZkBridge.exe` and `dist/TaxFlowBioTimeAgent.exe`.
`build/` and `dist/` are build artifacts — don't commit them.

## What CI verifies vs. what still needs manual validation

A GitHub Actions workflow (`.github/workflows/build-biometric-agents.yml`,
`windows-latest` runner) builds both executables on every push that touches
`backend/zk_bridge.py`, `backend/biotime_agent.py`, or this directory, and
checks:
- both `pyinstaller` builds succeed and produce a `.exe`
- each exe launches and exits with a clear, non-crashing error when
  `DEVICE_API_KEY`/config is missing, instead of hanging or stack-tracing
- `--install-startup` either succeeds (verifiable — the CI runner has full
  Task Scheduler permissions) or fails with the intended clear error message,
  not a silent no-op or an unhandled exception

**What this does NOT verify, and needs real-world validation before relying
on it operationally:** actual protocol behavior against a real ZKTeco
device or a real BioTime server, and that the Scheduled Task genuinely
survives a real reboot on a real customer PC (CI checks the `schtasks`
command succeeds, not multi-day persistence across restarts).

## Known limitation: unsigned binary

Neither `.exe` is code-signed. Windows SmartScreen and some antivirus
software will very likely flag a freshly-downloaded, unsigned, unknown-
publisher executable on first run — this is a well-known heuristic trigger
for PyInstaller-built binaries generally, not a bug in this build. When it
happens: **"More info" → "Run anyway"** in the SmartScreen dialog. A real
fix (a code-signing certificate) removes this prompt but is a paid,
ongoing business decision, not something this build process solves —
flagged here rather than silently left for someone to discover.

## Explicitly out of scope

- **Auto-update.** Re-running the build and re-downloading the exe is the
  only update path today. `docs/biometric-architecture.md` §3 flags
  updater integrity/signing as a deliberately undecided risk — don't build
  a self-updater without deciding that first.
- **LAN device auto-discovery.** The device's IP is still entered manually
  in `zk_bridge.conf` / the HRMS setup guide. The same doc section flags
  network scanning as needing an explicit opt-in design, not a silent
  broadcast — also not solved here.
