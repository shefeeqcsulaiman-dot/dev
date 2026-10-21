"""Download every company's full backup from the live site to this computer.

Until the live server has backup storage (DigitalOcean Spaces), its own nightly backups land
on a disk that is wiped on every deploy. This keeps a copy here instead: each run signs in as
the Super Admin, downloads each company's restorable SQL backup (the same file as Super Admin >
Company > Download Backup, which Super Admin > Restore from backup reads back in), gzips it into
<folder>/<YYYY-MM-DD>/, and deletes day-folders older than --keep-days.

Sign-in: asked when run by hand (the password is not shown or saved), or from environment
variables for an unattended run -- never from this file:
    ETAXFLOW_SA_EMAIL      the Super Admin email
    ETAXFLOW_SA_PASSWORD   its password
Optional: ETAXFLOW_URL (default https://app.etaxflow.com).

    python download_live_backups.py --folder "E:\\etaxflow-backups"

Read-only on the live site. Downloads are spaced out to stay under its limit of 10 a minute.
Standard library only, so it runs with any Python 3.10+.
"""
import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

PAUSE_SECONDS = 7          # the live db-dump endpoint allows 10 a minute
TIMEOUT_SECONDS = 600      # a big company's backup can take a while to build


def _request(url: str, token: str | None = None, body: dict | None = None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=TIMEOUT_SECONDS)


def _json(url: str, token: str | None = None, body: dict | None = None):
    with _request(url, token, body) as resp:
        return json.loads(resp.read().decode())


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:60] or "company"


def _companies(api: str, token: str) -> list[dict]:
    out, offset = [], 0
    while True:
        page = _json(f"{api}/superadmin/companies?limit=200&offset={offset}&sort=name&dir=asc", token)
        items = page.get("items", [])
        out += items
        offset += len(items)
        if not items or offset >= page.get("total", 0):
            break
    # Companies in the recycle bin are still restorable, so back them up too.
    seen = {c["id"] for c in out}
    for c in _json(f"{api}/superadmin/recycle-bin", token):
        if c["id"] not in seen:
            out.append({"id": c["id"], "name": c["name"] + " (recycle bin)"})
    return out


def _prune(folder: Path, keep_days: int, log) -> None:
    cutoff = date.today() - timedelta(days=keep_days)
    for day_dir in folder.iterdir():
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day_dir.is_dir() and day < cutoff:
            shutil.rmtree(day_dir, ignore_errors=True)
            log(f"Removed old backups {day_dir.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", required=True, help="where to keep the backups")
    parser.add_argument("--keep-days", type=int, default=30)
    args = parser.parse_args()

    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    day_dir = folder / date.today().isoformat()
    day_dir.mkdir(exist_ok=True)
    log_file = (folder / "backup-log.txt").open("a", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    email, password = os.environ.get("ETAXFLOW_SA_EMAIL"), os.environ.get("ETAXFLOW_SA_PASSWORD")
    if (not email or not password) and sys.stdin.isatty():
        # Run by hand: ask, and keep nothing (the password is not shown or saved).
        import getpass
        try:
            email = email or input("Super Admin email: ").strip()
            password = password or getpass.getpass("Super Admin password (hidden): ")
        except (EOFError, KeyboardInterrupt):
            email = password = None
    if not email or not password:
        log("No Super Admin email/password given, nothing downloaded.")
        return 2
    api = os.environ.get("ETAXFLOW_URL", "https://app.etaxflow.com").rstrip("/") + "/api/v1"

    try:
        token = _json(f"{api}/auth/login", body={"email": email, "password": password})["access_token"]
        companies = _companies(api, token)
    except urllib.error.HTTPError as exc:
        log(f"Sign-in or company list failed: HTTP {exc.code}")
        return 1
    except Exception as exc:  # network down, DNS...
        log(f"Could not reach the live site: {exc}")
        return 1

    log(f"Backing up {len(companies)} companies to {day_dir}")
    failed = 0
    for i, company in enumerate(companies):
        target = day_dir / f"{_safe(company['name'])}-{company['id'][:8]}.sql.gz"
        if i:
            time.sleep(PAUSE_SECONDS)
        tmp = target.with_suffix(".part")
        try:
            with _request(f"{api}/superadmin/companies/{company['id']}/db-dump", token) as resp, \
                    gzip.open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, 1 << 20)
            tmp.replace(target)
            log(f"  ok    {company['name']}  ({target.stat().st_size // 1024} KB)")
        except Exception as exc:
            failed += 1
            tmp.unlink(missing_ok=True)
            code = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
            log(f"  FAIL  {company['name']}: {code}")

    _prune(folder, args.keep_days, log)
    log(f"Done: {len(companies) - failed} saved, {failed} failed")
    log_file.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
