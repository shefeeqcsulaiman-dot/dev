"""Indexed summary columns for document collections kept as JSON app-data.

Sales invoices, bills, purchase records, quotations, expenses and payments live in
app_data_records as JSON text. Paging, searching and totalling them used to mean
sending every record to the browser and parsing it there. Instead, each save stamps a
few plain columns on the row (party, status, kind, total, salesperson, date) so those
reads run in SQL. SPECS says which payload fields feed them for each collection.

amount_paid is the one column a save can't work out from the document itself: it is
the sum of payment allocations to that document number (customer receipts for sales
invoices, supplier payments for bills and purchases), the same figure the browser used
to rebuild from every loaded payment. It is recomputed whenever a payment row is added,
changed or deleted, and when a payable document is first created (a payment can be
recorded before its document is saved).

Step 1 of moving these records out of JSON altogether (docs/scaling-plan-10k.md).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from sqlalchemy import event, func, inspect, or_
from sqlalchemy.orm import Session

from app.models import AppDataRecord

PAID_TOLERANCE = Decimal("0.01")

_DATE_FORMATS_DAY_FIRST = (
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
    "%Y/%m/%d", "%Y.%m.%d",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y", "%d-%b-%y",
    "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y",
)


def parse_document_date(value: Any) -> _dt.date | None:
    """Invoice dates as AI/CSV give them ("20-02-2023", "20/02/23", "20 Feb 2023", ISO...).
    Day-first (UAE convention) unless only month-first is a valid date, e.g. 02/20/2023."""
    text = re.sub(r"\s+", " ", str(value or "").strip().rstrip("."))
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in _DATE_FORMATS_DAY_FIRST:
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: Any) -> Decimal:
    """Same reading as app.js parseAmount(): drop everything but digits, '.' and '-'."""
    cleaned = re.sub(r"[^0-9.\-]", "", str(value if value is not None else ""))
    match = re.match(r"-?\d*\.?\d+", cleaned)
    if not match:
        return Decimal("0")
    try:
        return Decimal(match.group(0)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0")


def _truthy(value: Any) -> bool:
    """JavaScript truthiness for the values invoice payloads hold (app.js reads `a||b`)."""
    return value not in (None, "", 0, False) and not (isinstance(value, float) and value != value)


def is_sales_return(record: dict[str, Any]) -> bool:
    """app.js isSalesReturn(): the FIRST of document_type/source/status that is set decides."""
    for key in ("document_type", "source", "status"):
        if _truthy(record.get(key)):
            return "return" in str(record[key]).lower()
    return False


def _clip(value: Any, size: int) -> str | None:
    text = str(value or "").strip()
    return text[:size] if text else None


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if _truthy(record.get(key)):
            return record[key]
    return None


def payment_side(record: Any) -> str:
    """'supplier' for a Supplier Payment (settles bills/purchases), else 'customer'
    (a receipt against sales invoices), as app.js isSupplierPaymentType() decides."""
    return "supplier" if isinstance(record, dict) and record.get("type") == "Supplier Payment" else "customer"


def expense_kind(record: dict[str, Any]) -> str:
    """'direct' for a Direct Expense category (cost of sales on the dashboard), else 'other'.
    'Indirect ...' categories are not direct (the old browser check matched them too)."""
    category = str(record.get("category") or "").lower()
    return "direct" if "direct" in category and "indirect" not in category else "other"


@dataclass(frozen=True)
class DocSpec:
    party: tuple[str, ...]
    dates: tuple[str, ...]
    amount: tuple[str, ...]
    kind: Callable[[dict[str, Any]], str]
    default_status: str = "draft"
    # Which payments settle this document: "customer" receipts, "supplier" payments, or none.
    paid_by: str | None = None
    # Payload field copied into the salesperson column, if any.
    person: str | None = None


SPECS: dict[str, DocSpec] = {
    "salesInvoices": DocSpec(
        party=("customer", "customer_name"), dates=("date", "invoice_date"), amount=("total", "subtotal"),
        kind=lambda r: "return" if is_sales_return(r) else "invoice", paid_by="customer", person="salesperson",
    ),
    "bills": DocSpec(
        party=("vendor", "supplier", "vendor_name"), dates=("date", "bill_date"),
        amount=("total", "grand_total", "subtotal"), kind=lambda r: "bill",
        default_status="awaiting payment", paid_by="supplier",
    ),
    "purchaseRecords": DocSpec(
        party=("supplier", "vendor", "supplier_name"), dates=("date", "invoice_date", "purchase_date"),
        amount=("total", "grand_total", "net_amount", "subtotal"), kind=lambda r: "purchase",
        default_status="pending", paid_by="supplier",
    ),
    "quotations": DocSpec(
        party=("customer", "customer_name"), dates=("date",), amount=("total", "subtotal"),
        kind=lambda r: "quotation", person="owner",
    ),
    "expenses": DocSpec(
        party=("vendor", "supplier", "employee"), dates=("date", "expense_date"), amount=("total", "amount"),
        kind=lambda r: expense_kind(r), default_status="pending",
    ),
    "payments": DocSpec(
        party=("contact", "customer", "supplier", "vendor"), dates=("date",), amount=("amount", "total"),
        kind=payment_side, default_status="posted",
    ),
}
INDEXED_DOC_COLLECTIONS = frozenset(SPECS)
# Payment side -> the collections whose amount_paid it feeds.
PAID_COLLECTIONS = {
    side: tuple(c for c, spec in SPECS.items() if spec.paid_by == side) for side in ("customer", "supplier")
}


def doc_columns(record: dict[str, Any], collection: str = "salesInvoices") -> dict[str, Any]:
    spec = SPECS[collection]
    day = parse_document_date(_first(record, spec.dates))
    return {
        "party": _clip(_first(record, spec.party), 160),
        "doc_status": (_clip(record.get("status"), 40) or spec.default_status).lower(),
        "doc_kind": spec.kind(record),
        "salesperson": _clip(record.get(spec.person), 120) if spec.person else None,
        "amount": parse_amount(_first(record, spec.amount)),
        "record_date": day.isoformat() if day else None,
    }


def stamp_doc_columns(target: AppDataRecord) -> None:
    try:
        record = json.loads(target.payload or "{}")
    except (TypeError, ValueError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    for column, value in doc_columns(record, target.collection).items():
        setattr(target, column, value)


def _allocations(record: dict[str, Any]) -> list[tuple[str, Decimal]]:
    """(lower-cased document no, amount) pairs a payment applies, as app.js
    renderPaymentRecord() reads them: explicit allocations, else the whole amount
    against document_ref/invoice_no/bill_no."""
    allocations = record.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        allocations = [{
            "doc_ref": record.get("document_ref") or record.get("invoice_no") or record.get("bill_no") or "",
            "amount": record.get("amount"),
        }]
    out = []
    for alloc in allocations:
        if not isinstance(alloc, dict):
            continue
        ref = str(alloc.get("doc_ref") or "").strip().lower()
        amount = parse_amount(alloc.get("amount"))
        if ref and ref != "-" and amount:
            out.append((ref, amount))
    return out


def payment_allocations(record: dict[str, Any]) -> list[tuple[str, Decimal]]:
    """Customer-receipt allocations only (what sales invoices are settled by).
    Kept unchanged for migration 0002; new code uses side_allocations()."""
    if not isinstance(record, dict) or payment_side(record) != "customer":
        return []
    return _allocations(record)


def side_allocations(record: Any) -> tuple[str, list[tuple[str, Decimal]]]:
    if not isinstance(record, dict):
        return "customer", []
    return payment_side(record), _allocations(record)


def _payment_refs(payload: str | None) -> tuple[str, set[str]]:
    try:
        side, allocs = side_allocations(json.loads(payload or "{}"))
    except (TypeError, ValueError):
        return "customer", set()
    return side, {ref for ref, _ in allocs}


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def recompute_paid(db: Session, company_id: str, side: str, refs: Iterable[str]) -> None:
    """Set amount_paid on the documents numbered `refs` (any case) that `side` payments
    settle (sales invoices for customer receipts; bills and purchases for supplier
    payments), from every such payment that allocates to them."""
    wanted = {str(r).strip().lower() for r in refs if str(r or "").strip()}
    if not wanted:
        return
    paid = {ref: Decimal("0") for ref in wanted}
    payments: dict[str, str] = {}
    ordered = sorted(wanted)
    with db.no_autoflush:
        for i in range(0, len(ordered), 40):
            chunk = ordered[i:i + 40]
            # Narrow in SQL to payments whose JSON mentions one of the numbers; the
            # allocations themselves are read in Python below.
            needles = [_like_escape(json.dumps(ref, ensure_ascii=False)[1:-1]) for ref in chunk]
            rows = db.query(AppDataRecord.id, AppDataRecord.payload).filter(
                AppDataRecord.company_id == company_id,
                AppDataRecord.collection == "payments",
                or_(*[func.lower(AppDataRecord.payload).like(f"%{n}%", escape="\\") for n in needles]),
            ).all()
            payments.update(rows)
        for payload in payments.values():
            try:
                pay_side, allocs = side_allocations(json.loads(payload or "{}"))
            except (TypeError, ValueError):
                continue
            if pay_side != side:
                continue
            for ref, amount in allocs:
                if ref in paid:
                    paid[ref] += amount
        doc_rows = db.query(AppDataRecord.id, AppDataRecord.record_key).filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection.in_(PAID_COLLECTIONS[side]),
            func.lower(func.trim(AppDataRecord.record_key)).in_(ordered),
        ).all()
    by_amount: dict[Decimal, list[str]] = {}
    for row_id, key in doc_rows:
        by_amount.setdefault(paid.get(str(key or "").strip().lower(), Decimal("0")), []).append(row_id)
    for amount, ids in by_amount.items():
        db.query(AppDataRecord).filter(AppDataRecord.id.in_(ids)).update(
            {AppDataRecord.amount_paid: amount}, synchronize_session=False
        )


def recompute_sales_paid(db: Session, company_id: str, refs: Iterable[str]) -> None:
    recompute_paid(db, company_id, "customer", refs)


def recompute_payment_targets(db: Session, company_id: str, payloads: Iterable[Any]) -> None:
    """After payments are removed without the ORM hooks (bulk DELETE): recompute every
    document they were allocated to."""
    by_side: dict[str, set[str]] = {"customer": set(), "supplier": set()}
    for record in payloads:
        side, allocs = side_allocations(record)
        by_side[side].update(ref for ref, _ in allocs)
    for side, refs in by_side.items():
        recompute_paid(db, company_id, side, refs)


def effective_status(status: Any, amount: Any, amount_paid: Any) -> str:
    """Document status as the registers show it: Paid/Partial once payments are allocated
    (what app.js markPaymentDocumentPaid() used to set row by row), else the saved status."""
    total = Decimal(str(amount or 0))
    paid = Decimal(str(amount_paid or 0))
    if paid > 0:
        return "Paid" if total - paid <= PAID_TOLERANCE else "Partial"
    return str(status or "Draft")


# ── keep amount_paid current on every write path ──────────────────────────────
_PENDING_KEY = "_doc_index_paid_refs"


@event.listens_for(Session, "after_flush")
def _collect_paid_refs(session: Session, _ctx) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        if not isinstance(obj, AppDataRecord):
            continue
        found: list[tuple[str, set[str]]] = []
        if obj.collection == "payments":
            found.append(_payment_refs(obj.payload))
            # Documents the payment pointed at before this edit lose its money too.
            for old in inspect(obj).attrs.payload.history.deleted or ():
                found.append(_payment_refs(old))
        elif obj in session.new and obj.collection in SPECS and SPECS[obj.collection].paid_by:
            found.append((SPECS[obj.collection].paid_by, {str(obj.record_key or "").strip().lower()}))
        for side, refs in found:
            if refs:
                pending = session.info.setdefault(_PENDING_KEY, {})
                pending.setdefault((obj.company_id, side), set()).update(refs)


@event.listens_for(Session, "after_flush_postexec")
def _apply_paid_refs(session: Session, _ctx) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    for (company_id, side), refs in pending.items():
        recompute_paid(session, company_id, side, refs)
