"""Bulk generation of sales/purchase transactions that replicate the real
posting pipeline's output (SourceTransaction -> PostingJob -> JournalEntry
-> GeneralLedgerEntry -> TaxLine), without calling that pipeline per-row.

Why not just call sync_sales_invoice_accounting()/sync_purchase_accounting()
in a loop: accounting_posting.py's create_gl_entries_from_journal() runs an
aggregate SUM(debit-credit) query over every prior GeneralLedgerEntry for an
account, per journal line, to compute a running balance. That's fine for one
invoice at a time in a live request, but it's an O(n) query per line as the
ledger grows -- calling it a million times would be super-linear, not just
slow.

Instead this module builds the same rows build_journal()/
create_gl_entries_from_journal()/ensure_tax_line() would have produced, but
computes the GeneralLedgerEntry running balance in memory as a plain
dict[account_id] -> Decimal, incremented once per line as transactions are
processed in ascending date order per company. Since every seeded Account
has opening_balance=0 (company_defaults.seed_accounts() never sets one) and
every transaction here is company-wide (branch_id=None, see the note in
_build_transaction_rows below), this in-memory running total is
mathematically identical to what the real per-line SQL aggregate would have
returned at the moment each entry was posted -- see verify.py for the check
that confirms this against a fresh SQL recomputation.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from faker import Faker
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.accounting_posting import money
from app.models import (
    AppDataRecord,
    AuditLog,
    GeneralLedgerEntry,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    PostingJob,
    SourceTransaction,
    SourceTransactionLine,
    TaxLine,
    uuid,
)

from .tenants import CompanyBundle


VAT_RATE = Decimal("5.00")


@dataclass
class CompanyTotals:
    sales_subtotal: Decimal = Decimal("0.00")
    purchase_subtotal: Decimal = Decimal("0.00")
    sales_count: int = 0
    purchase_count: int = 0


@dataclass
class _Shape:
    module: str  # "sales" | "purchase" | "purchase_bill"
    reference: str
    party_name: str
    tx_date: datetime
    lines: list[dict[str, Any]]  # description, quantity, unit_price, amount
    subtotal: Decimal
    vat: Decimal
    total: Decimal


def _random_date(years: int) -> datetime:
    now = datetime.now(timezone.utc)
    days_back = random.randrange(0, max(1, years * 365))
    return now - timedelta(days=days_back, hours=random.randrange(0, 24))


def _build_shapes(bundle: CompanyBundle, sales_count: int, purchase_count: int, years: int, run_tag: str) -> list[_Shape]:
    shapes: list[_Shape] = []
    for n in range(1, sales_count + 1):
        shapes.append(_build_one(bundle, "sales", f"{run_tag}-{bundle.index:03d}-SINV-{n:06d}", bundle.customers, years, run_tag))
    for n in range(1, purchase_count + 1):
        module = "purchase" if n % 2 == 0 else "purchase_bill"
        prefix = "PREC" if module == "purchase" else "BILL"
        shapes.append(_build_one(bundle, module, f"{run_tag}-{bundle.index:03d}-{prefix}-{n:06d}", bundle.suppliers, years, run_tag))
    shapes.sort(key=lambda s: s.tx_date)
    return shapes


def _build_one(bundle: CompanyBundle, module: str, reference: str, parties: list[str], years: int, run_tag: str) -> _Shape:
    party = random.choice(parties)
    line_count = random.randint(1, 5)
    lines = []
    subtotal = Decimal("0.00")
    for _ in range(line_count):
        sku, name, unit_price = random.choice(bundle.products)
        qty = Decimal(random.randint(1, 20))
        line_amount = money(qty * unit_price)
        subtotal += line_amount
        lines.append({"description": name, "quantity": qty, "unit_price": unit_price, "amount": line_amount})
    subtotal = money(subtotal)
    vat = money(subtotal * VAT_RATE / Decimal("100"))
    total = subtotal + vat
    return _Shape(
        module=module,
        reference=reference,
        party_name=party,
        tx_date=_random_date(years),
        lines=lines,
        subtotal=subtotal,
        vat=vat,
        total=total,
    )


def _flush_batches(db: Session, batches: dict[type, list[dict]], batch_size: int, force: bool = False) -> None:
    for model, rows in batches.items():
        while len(rows) >= batch_size or (force and rows):
            chunk, rows[:] = rows[:batch_size], rows[batch_size:]
            if chunk:
                db.execute(insert(model), chunk)
            if not force:
                break


def generate_company_transactions(
    db: Session,
    bundle: CompanyBundle,
    sales_count: int,
    purchase_count: int,
    years: int,
    run_tag: str,
    batch_size: int,
    include_audit_logs: bool,
) -> CompanyTotals:
    if not bundle.products:
        raise ValueError(f"Company {bundle.index} has no products to build transactions from")

    shapes = _build_shapes(bundle, sales_count, purchase_count, years, run_tag)
    running_balance: dict[str, Decimal] = {}
    totals = CompanyTotals()

    batches: dict[type, list[dict]] = {
        Invoice: [], InvoiceLine: [], AppDataRecord: [],
        SourceTransaction: [], SourceTransactionLine: [], PostingJob: [],
        JournalEntry: [], JournalLine: [], GeneralLedgerEntry: [], TaxLine: [],
    }
    if include_audit_logs:
        batches[AuditLog] = []

    for shape in shapes:
        _add_transaction_rows(bundle, shape, running_balance, batches, include_audit_logs)
        if shape.module == "sales":
            totals.sales_subtotal += shape.subtotal
            totals.sales_count += 1
        else:
            totals.purchase_subtotal += shape.subtotal
            totals.purchase_count += 1
        _flush_batches(db, batches, batch_size)

    _flush_batches(db, batches, batch_size, force=True)
    return totals


def _add_transaction_rows(
    bundle: CompanyBundle,
    shape: _Shape,
    running_balance: dict[str, Decimal],
    batches: dict[type, list[dict]],
    include_audit_logs: bool,
) -> None:
    # branch_id is deliberately left None (company-wide) for every generated
    # transaction: the real create_gl_entries_from_journal() partitions its
    # running-balance query by branch when a journal HAS a branch_id (an
    # entry's "prior" sum then only includes same-branch + branch-less
    # rows). Replicating that per-branch partition in the in-memory running
    # total below is straightforward, but keeping every bulk-seeded
    # transaction branch-less sidesteps it entirely while still giving
    # every seeded company real branches/employees for HR and
    # branch-isolation testing -- the transactional volume itself is
    # deliberately company-level only.
    source_id = uuid()
    journal_id = uuid()
    entry_number = f"AUTO-{shape.reference}"

    source_line_rows = []
    journal_lines: list[dict] = []

    if shape.module == "sales":
        invoice_id = uuid()
        batches[Invoice].append({
            "id": invoice_id,
            "company_id": bundle.company_id,
            "branch_id": None,
            "customer_name": shape.party_name,
            "invoice_number": shape.reference,
            "status": random.choice(["paid", "paid", "issued", "issued", "draft"]),
            "subtotal": shape.subtotal,
            "vat": shape.vat,
            "total": shape.total,
            "created_at": shape.tx_date,
        })
        for line in shape.lines:
            batches[InvoiceLine].append({
                "id": uuid(),
                "invoice_id": invoice_id,
                "description": line["description"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "vat_rate": VAT_RATE,
            })
            source_line_rows.append({
                "description": line["description"], "account_code": "3000",
                "quantity": line["quantity"], "unit_price": line["unit_price"],
                "vat_rate": VAT_RATE, "amount": line["amount"],
                "vat_amount": money(line["amount"] * VAT_RATE / Decimal("100")),
            })
        # build_journal(): Dr 1100 (receivable) total / Cr 3000 per line / Cr 2200 VAT
        journal_lines.append(_jl(bundle.accounts["1100"], "Customer receivable", debit=shape.total))
        for line in shape.lines:
            journal_lines.append(_jl(bundle.accounts["3000"], line["description"], credit=line["amount"]))
        if shape.vat:
            journal_lines.append(_jl(bundle.accounts["2200"], "Output VAT", credit=shape.vat))
        tax_direction = "output"
    else:
        collection = "purchaseRecords" if shape.module == "purchase" else "bills"
        record_payload = {
            "supplier" if collection == "purchaseRecords" else "vendor": shape.party_name,
            "date": shape.tx_date.strftime("%Y-%m-%d"),
            "subtotal": str(shape.subtotal), "vat": str(shape.vat), "total": str(shape.total),
            "status": random.choice(["Paid", "Pending Payment", "Awaiting Payment"]),
        }
        key_field = "ref" if collection == "purchaseRecords" else "bill_no"
        record_payload[key_field] = shape.reference
        batches[AppDataRecord].append({
            "id": uuid(), "company_id": bundle.company_id, "branch_id": None,
            "collection": collection, "record_key": shape.reference,
            "payload": json.dumps(record_payload), "created_at": shape.tx_date,
        })
        for line in shape.lines:
            source_line_rows.append({
                "description": line["description"], "account_code": "4000",
                "quantity": line["quantity"], "unit_price": line["unit_price"],
                "vat_rate": VAT_RATE, "amount": line["amount"],
                "vat_amount": money(line["amount"] * VAT_RATE / Decimal("100")),
            })
        # build_journal(): Dr 4000 per line / Dr 2210 VAT / Cr 2100 (payable) total
        for line in shape.lines:
            journal_lines.append(_jl(bundle.accounts["4000"], line["description"], debit=line["amount"]))
        if shape.vat:
            journal_lines.append(_jl(bundle.accounts["2210"], "Input VAT", debit=shape.vat))
        journal_lines.append(_jl(bundle.accounts["2100"], "Supplier payable", credit=shape.total))
        tax_direction = "input"

    batches[SourceTransaction].append({
        "id": source_id, "company_id": bundle.company_id, "branch_id": None,
        "module": shape.module, "reference": shape.reference, "party_name": shape.party_name,
        "status": "posted", "subtotal": shape.subtotal, "vat": shape.vat, "total": shape.total,
        "approved_by": bundle.admin_user_id, "approved_at": shape.tx_date, "created_at": shape.tx_date,
    })
    for row in source_line_rows:
        batches[SourceTransactionLine].append({"id": uuid(), "source_id": source_id, **row})

    batches[PostingJob].append({
        "id": uuid(), "company_id": bundle.company_id, "source_id": source_id,
        "status": "posted", "retry_count": 0, "error_message": None,
    })
    batches[JournalEntry].append({
        "id": journal_id, "company_id": bundle.company_id, "branch_id": None,
        "entry_number": entry_number, "source_module": shape.module, "source_id": source_id,
        "entry_date": shape.tx_date, "description": f"Auto-posted {shape.module} {shape.reference}",
        "status": "posted", "created_at": shape.tx_date,
    })
    for jl in journal_lines:
        line_id = uuid()
        batches[JournalLine].append({"id": line_id, "journal_id": journal_id, **jl})
        net = money(Decimal(jl["debit"]) - Decimal(jl["credit"]))
        account_id = jl["account_id"]
        running_balance[account_id] = running_balance.get(account_id, Decimal("0.00")) + net
        batches[GeneralLedgerEntry].append({
            "id": uuid(), "company_id": bundle.company_id, "branch_id": None,
            "entry_date": shape.tx_date, "voucher_no": shape.reference, "voucher_type": shape.module,
            "account_id": account_id, "journal_entry_id": journal_id, "journal_line_id": line_id,
            "debit": jl["debit"], "credit": jl["credit"], "balance": running_balance[account_id],
            "party": shape.party_name, "narration": jl["description"], "created_at": shape.tx_date,
        })

    tax_code_id = bundle.tax_codes.get("VAT5" if shape.vat else "ZERO") or bundle.tax_codes.get("VAT5")
    batches[TaxLine].append({
        "id": uuid(), "company_id": bundle.company_id, "source_id": source_id,
        "tax_code_id": tax_code_id, "direction": tax_direction,
        "taxable_amount": shape.subtotal, "tax_amount": shape.vat,
        "period": shape.tx_date.strftime("%Y-%m"), "created_at": shape.tx_date,
    })

    if include_audit_logs:
        batches[AuditLog].append({
            "id": uuid(), "company_id": bundle.company_id, "user_id": bundle.admin_user_id,
            "module": shape.module, "action": "posted_to_ledger", "record_id": source_id,
            "detail": entry_number, "created_at": shape.tx_date,
        })


def _jl(account_id: str, description: str, debit: Decimal = Decimal("0.00"), credit: Decimal = Decimal("0.00")) -> dict:
    return {"account_id": account_id, "description": description, "debit": money(debit), "credit": money(credit)}
