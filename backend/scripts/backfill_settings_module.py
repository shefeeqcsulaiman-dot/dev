"""One-time backfill: add "settings" to Company.modules_enabled for any
company whose stored module list predates the fix in auth.py's register()
(commit b315569, 2026-08-18) — that endpoint used to write its own stale
_ALL_MODULES list (16 items) instead of the canonical app.module_catalog.
ALL_MODULES (17 items, includes "settings"), so every self-serve signup
before that fix silently lost access to its own Settings page (the
sidebar's Settings nav is gated by data-module="settings", same as any
other module — see _moduleNavAllowed() in app.js).

Only touches companies where modules_enabled is a real JSON array that's
missing "settings". Leaves alone:
  - companies with modules_enabled = NULL (unrestricted — already fine,
    _moduleNavAllowed() short-circuits to true when there's no list at all)
  - companies whose list already includes "settings" (nothing to do)

Usage:
    # Dry run (default) — reports what WOULD change, writes nothing:
    python -m scripts.backfill_settings_module

    # Apply for real:
    python -m scripts.backfill_settings_module --apply

Run with DATABASE_URL pointed at whichever database you intend to fix
(defaults to whatever backend/.env / the environment already has set —
same convention as every other script in this directory). Safe to run
more than once; it's a no-op the second time.
"""
from __future__ import annotations

import argparse
import json

from app.database import SessionLocal
from app.models import Company


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(Company.modules_enabled.isnot(None)).all()
        affected = []
        for company in companies:
            try:
                modules = json.loads(company.modules_enabled)
            except (TypeError, ValueError):
                continue
            if not isinstance(modules, list):
                continue
            if "settings" in modules:
                continue
            affected.append((company, modules))

        if not affected:
            print("Nothing to do - no companies found with a restricted module list missing 'settings'.")
            return

        print(f"{'Would fix' if not args.apply else 'Fixing'} {len(affected)} compan{'y' if len(affected) == 1 else 'ies'}:")
        for company, modules in affected:
            print(f"  - {company.id}  {company.name!r}  ({len(modules)} modules -> {len(modules) + 1})")

        if not args.apply:
            print("\nDry run only - re-run with --apply to write these changes.")
            return

        for company, modules in affected:
            company.modules_enabled = json.dumps([*modules, "settings"])
            db.add(company)
        db.commit()
        print(f"\nDone. Updated {len(affected)} compan{'y' if len(affected) == 1 else 'ies'}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
