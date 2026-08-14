"""Bulk synthetic-data seed generator for local/dev TaxFlow databases.

Generates N companies, each with its own branches/employees/customers/
suppliers/products and a realistic volume of sales+purchase transactions
posted through a hand-replicated version of the real accounting pipeline
(see bulk_seed/ledger.py's module docstring for why it's replicated rather
than calling the real service functions in a loop).

    python backend/scripts/run_bulk_seed.py --companies 2 --sales-per-company 50 \\
        --purchases-per-company 50 --years 1 --yes

Never point this at production. The safety guard below refuses to run
against anything other than SQLite or a localhost/127.0.0.1 Postgres unless
--allow-remote-host is also passed, and always requires --yes.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR.parent))  # backend/ -> for `import app...`
sys.path.insert(0, str(_SCRIPTS_DIR))          # backend/scripts/ -> for `import bulk_seed...`

from faker import Faker  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import CorporateTaxRecord, Company  # noqa: E402

from bulk_seed import ledger, tenants, verify  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--companies", type=int, default=100)
    p.add_argument("--start-index", type=int, default=1, help="First company index (for resuming a partial run)")
    p.add_argument("--branches-per-company", type=int, default=10)
    p.add_argument("--employees-per-company", type=int, default=95)
    p.add_argument("--customers-per-company", type=int, default=200)
    p.add_argument("--suppliers-per-company", type=int, default=150)
    p.add_argument("--products-per-company", type=int, default=300)
    p.add_argument("--sales-per-company", type=int, default=5000)
    p.add_argument("--purchases-per-company", type=int, default=5000)
    p.add_argument("--years", type=int, default=5, help="Span of history transactions are dated across")
    p.add_argument("--batch-size", type=int, default=5000, help="Rows per table flushed per bulk insert")
    p.add_argument("--run-tag", default="BULK", help="Prefix embedded in TRNs/employee_no/references; also the resume/verify key")
    p.add_argument("--admin-password", default="Demo@12345", help="Password for every seeded company's admin login")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducible output")
    p.add_argument("--skip-audit-logs", action="store_true", help="Skip AuditLog rows for a faster run")
    p.add_argument("--verify-only", action="store_true", help="Skip generation; verify companies already seeded under --run-tag")
    p.add_argument("--dry-run", action="store_true", help="Print the plan and resolved target DB, then exit without writing")
    p.add_argument("--yes", action="store_true", help="Required to actually write data")
    p.add_argument("--allow-remote-host", action="store_true", help="Required in addition to --yes if DATABASE_URL isn't SQLite/localhost")
    return p.parse_args()


def _check_target_is_safe(allow_remote_host: bool) -> str:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        print(f"Target DB: SQLite -> {url}")
        return url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    print(f"Target DB: {parsed.scheme} -> host={host} db={parsed.path.lstrip('/')}")
    if host not in {"localhost", "127.0.0.1"} and not allow_remote_host:
        print(
            f"\nRefusing to run: DATABASE_URL host '{host}' is not localhost/127.0.0.1 and this could be a "
            "remote (possibly production) database. If this is genuinely a local/staging Postgres instance "
            "you control, re-run with --allow-remote-host to confirm.",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


def main() -> None:
    args = parse_args()

    _check_target_is_safe(args.allow_remote_host)

    total_sales = args.companies * args.sales_per_company
    total_purchases = args.companies * args.purchases_per_company
    print(
        f"Plan: {args.companies} companies (index {args.start_index}..{args.start_index + args.companies - 1}), "
        f"{args.branches_per_company} branches/company, {args.employees_per_company} employees/company, "
        f"{args.sales_per_company} sales + {args.purchases_per_company} purchases per company "
        f"({total_sales:,} + {total_purchases:,} = {total_sales + total_purchases:,} transactions total) "
        f"across {args.years} years. run-tag={args.run_tag!r}"
    )

    if args.dry_run:
        print("Dry run only -- no data written.")
        return
    if not args.verify_only and not args.yes:
        print("\nRefusing to write data without --yes. Pass --dry-run to just see the plan.", file=sys.stderr)
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)
        Faker.seed(args.seed)
    fake = Faker()

    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    start = time.monotonic()
    company_ids: list[str] = []
    try:
        if args.verify_only:
            prefix = tenants.run_tag_trn_prefix(args.run_tag)
            company_ids = [row[0] for row in db.query(Company.id).filter(Company.trn.like(f"{prefix}%")).all()]
            print(f"Found {len(company_ids)} companies for run-tag {args.run_tag!r}.")
        else:
            for i in range(args.start_index, args.start_index + args.companies):
                existing = tenants.existing_company_id(db, args.run_tag, i)
                if existing:
                    print(f"[{i}/{args.start_index + args.companies - 1}] already seeded, skipping")
                    company_ids.append(existing)
                    continue

                bundle = tenants.create_company_bundle(
                    db, fake, i, args.run_tag,
                    args.branches_per_company, args.employees_per_company,
                    args.customers_per_company, args.suppliers_per_company, args.products_per_company,
                    args.admin_password,
                )
                db.commit()

                totals = ledger.generate_company_transactions(
                    db, bundle, args.sales_per_company, args.purchases_per_company,
                    args.years, args.run_tag, args.batch_size, include_audit_logs=not args.skip_audit_logs,
                )
                _write_corporate_tax_record(db, bundle.company_id, totals)
                db.commit()
                company_ids.append(bundle.company_id)

                elapsed = time.monotonic() - start
                done = i - args.start_index + 1
                rate = done / elapsed if elapsed else 0
                remaining = args.companies - done
                eta = remaining / rate if rate else 0
                print(
                    f"[{i}/{args.start_index + args.companies - 1}] done -- "
                    f"{elapsed/60:.1f}m elapsed, {rate*60:.1f} companies/min, ETA {eta/60:.1f}m"
                )

        print("\nVerifying...")
        problems = verify.verify_all(db, company_ids, args.sales_per_company, args.purchases_per_company)
        if problems:
            print(f"\n{len(problems)}/{len(company_ids)} companies FAILED verification:")
            for company_id, issues in list(problems.items())[:10]:
                print(f"  {company_id}: {issues}")
            sys.exit(1)
        print(f"All {len(company_ids)} companies verified clean.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _write_corporate_tax_record(db: Session, company_id: str, totals: ledger.CompanyTotals) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    period = datetime.now(timezone.utc).strftime("%Y")
    profit = totals.sales_subtotal - totals.purchase_subtotal
    taxable_income = max(Decimal("0.00"), profit)
    tax_due = max(Decimal("0.00"), taxable_income - Decimal("375000.00")) * Decimal("0.09")
    record = db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == company_id, CorporateTaxRecord.period == period).first()
    if not record:
        record = CorporateTaxRecord(company_id=company_id, period=period)
        db.add(record)
    record.accounting_profit = profit.quantize(Decimal("0.01"))
    record.tax_adjustments = Decimal("0.00")
    record.taxable_income = taxable_income.quantize(Decimal("0.01"))
    record.tax_due = tax_due.quantize(Decimal("0.01"))
    record.status = "calculated"


if __name__ == "__main__":
    main()
