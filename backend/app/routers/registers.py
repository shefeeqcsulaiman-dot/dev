"""Paged registers for document collections kept as JSON app-data.

Sales invoices, bills, quotations and payments are no longer part of the bootstrap
blob (GET /app-data), which used to ship up to 1,500 of each on every page open. Each
register asks for one page at a time, and the screens that used to scan a whole
on-screen table (KPI cards, payment allocation, vendor balances, bank transactions,
stock movements, delete guards) use the reads below. They rely on the summary columns
app/doc_index.py stamps on each row (party, doc_status, doc_kind, amount, amount_paid,
salesperson, record_date).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Principal, get_current_principal
from app.doc_index import PAID_TOLERANCE, SPECS, effective_status, parse_document_date
from app.doc_lines import LINE_COLLECTIONS
from app.models import AppDataRecord, DocumentLine
from app.routers.app_data import (
    _collection_read_filters,
    assert_collection_module_enabled,
    assert_collection_read_permission,
    resolve_principal_company,
    serialize,
)

router = APIRouter(prefix="/app-data", tags=["registers"])

# Collections served by GET /registers/{collection}.
REGISTERS = frozenset({"salesInvoices", "bills", "quotations", "payments", "expenses"})
# isPendingDocumentStatus() in app.js: statuses that mean nothing is left to collect/pay.
_SETTLED_STATUS_WORDS = ("paid", "posted", "settled", "allocated", "closed", "reconciled", "complete")
# _refreshBillPageStats() in app.js: bill statuses that are no longer open.
_BILL_CLOSED_STATUSES = ("paid", "complete", "completed", "posted", "settled", "received")
_SORTS = {
    "newest": lambda: (AppDataRecord.created_at.desc(), AppDataRecord.record_key.desc(), AppDataRecord.id.desc()),
    "date-desc": lambda: (AppDataRecord.record_date.desc().nulls_last(), AppDataRecord.record_key.desc(), AppDataRecord.id.desc()),
    "date-asc": lambda: (AppDataRecord.record_date.asc().nulls_last(), AppDataRecord.record_key.asc(), AppDataRecord.id.asc()),
    "amount-desc": lambda: (AppDataRecord.amount.desc().nulls_last(), AppDataRecord.created_at.desc(), AppDataRecord.id.desc()),
    "amount-asc": lambda: (AppDataRecord.amount.asc().nulls_last(), AppDataRecord.created_at.desc(), AppDataRecord.id.desc()),
}


def _filters(db: Session, principal: Principal, collection: str, branch_id: str | None) -> list[Any]:
    company = resolve_principal_company(principal, db)
    assert_collection_module_enabled(db, principal, company, collection)
    assert_collection_read_permission(principal, collection)
    return _collection_read_filters(principal, collection, branch_id)


def _register(collection: str) -> str:
    if collection not in REGISTERS:
        raise HTTPException(status_code=404, detail="Unknown register")
    return collection


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(text: str) -> str:
    return f"%{_like_escape(text.strip().lower())}%"


def _paid(row_paid: Any) -> Decimal:
    return Decimal(str(row_paid or 0))


def register_record(row: AppDataRecord) -> dict[str, Any]:
    """The saved record; for documents settled by payments (invoices, bills, purchases)
    status/amount_paid/balance_due reflect the payments allocated to it (previously
    patched onto each on-screen row by the browser)."""
    data = serialize(row)
    spec = SPECS.get(row.collection)
    paid = _paid(row.amount_paid)
    if spec and spec.paid_by and paid > 0:
        total = row.amount or Decimal("0")
        status = effective_status(data.get("status"), total, paid)
        data["status"] = status
        data["amount_paid"] = float(total if status == "Paid" else paid)
        data["balance_due"] = 0.0 if status == "Paid" else float(total - paid)
    return data


def _open_filters() -> list[Any]:
    """Documents with money still outstanding: not in a settled status and not fully paid."""
    return [
        *[~AppDataRecord.doc_status.like(f"%{word}%") for word in _SETTLED_STATUS_WORDS],
        func.coalesce(AppDataRecord.amount, 0) - func.coalesce(AppDataRecord.amount_paid, 0) > PAID_TOLERANCE,
    ]


# ── generic register reads ───────────────────────────────────────────────────

def list_register(
    db: Session,
    principal: Principal,
    collection: str,
    *,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
    contains: str | None = None,
    party: str | None = None,
    product: str | None = None,
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    branch_id: str | None = None,
    running: bool = False,
) -> dict[str, object]:
    filters = _filters(db, principal, collection, branch_id)
    if kind and kind != "all":
        filters.append(AppDataRecord.doc_kind == kind)
    if status and status.strip():
        filters.append(AppDataRecord.doc_status == status.strip().lower())
    if q and q.strip():
        pattern = _like(q)
        filters.append(or_(
            func.lower(AppDataRecord.record_key).like(pattern, escape="\\"),
            func.lower(AppDataRecord.party).like(pattern, escape="\\"),
            AppDataRecord.doc_status.like(pattern, escape="\\"),
            func.lower(AppDataRecord.salesperson).like(pattern, escape="\\"),
            AppDataRecord.record_date.like(pattern, escape="\\"),
        ))
    if contains and contains.strip():
        filters.append(func.lower(AppDataRecord.payload).like(_like(contains), escape="\\"))
    if party and party.strip():
        filters.append(func.lower(AppDataRecord.party) == party.strip().lower())
    if product and product.strip() and collection in LINE_COLLECTIONS:
        # A line whose description, product name or product is exactly this (any case),
        # from document_lines (app/doc_lines.py).
        key = product.strip().lower()
        filters.append(exists().where(
            DocumentLine.record_id == AppDataRecord.id,
            or_(DocumentLine.description_key == key, DocumentLine.product_name_key == key, DocumentLine.product_key == key),
        ))
    elif product and product.strip():
        # Payloads are json.dumps() output, so a line field reads exactly `"description": "<name>"`.
        name = json.dumps(product.strip().lower(), ensure_ascii=False)
        needles = [_like_escape(f'"{field}": {name}') for field in ("description", "product_name", "product")]
        filters.append(or_(*(func.lower(AppDataRecord.payload).like(f"%{n}%", escape="\\") for n in needles)))
    order = _SORTS.get(sort, _SORTS["newest"])()
    total = db.query(func.count(AppDataRecord.id)).filter(*filters).scalar() or 0
    rows = db.query(AppDataRecord).filter(*filters).order_by(*order).offset(offset).limit(limit).all()
    result: dict[str, object] = {
        "ok": True,
        "records": [register_record(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }
    if running:
        # Net of every row before this page in the same order (receipts in, supplier
        # payments out), so a page can carry on a running balance.
        before = (
            select(AppDataRecord.amount.label("amount"), AppDataRecord.doc_kind.label("kind"))
            .where(*filters).order_by(*order).limit(offset).subquery()
        )
        signed = case((before.c.kind == "supplier", -before.c.amount), else_=before.c.amount)
        net = db.execute(select(func.coalesce(func.sum(signed), 0))).scalar() if offset else 0
        result["running_before"] = float(net or 0)
    return result


@router.get("/registers/{collection}")
def get_register(
    collection: str,
    kind: str | None = Query(default=None, max_length=20),
    status: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=120),
    contains: str | None = Query(default=None, max_length=160),
    party: str | None = Query(default=None, max_length=160),
    product: str | None = Query(default=None, max_length=160),
    sort: str = Query(default="newest", pattern="^(newest|date-desc|date-asc|amount-desc|amount-asc)$"),
    running: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One page of a register. q: number, party, status, salesperson/owner or date.
    contains: anywhere in the saved record. party: exact customer/vendor/contact (any
    case). product: a line item named exactly this. kind: invoice/return, customer/
    supplier (payments), direct/other (expenses). status: exact status (any case).
    running: also return running_before for a running balance."""
    return list_register(
        db, principal, _register(collection), kind=kind, status=status, q=q, contains=contains, party=party, product=product,
        sort=sort, limit=limit, offset=offset, branch_id=branch_id, running=running,
    )


@router.get("/registers/{collection}/by-key")
def get_register_record(
    collection: str,
    key: str = Query(min_length=1, max_length=160),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    filters = _filters(db, principal, _register(collection), branch_id)
    row = (
        db.query(AppDataRecord)
        .filter(*filters, func.lower(func.trim(AppDataRecord.record_key)) == key.strip().lower())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True, "record": register_record(row)}


@router.get("/registers/{collection}/parties")
def get_register_parties(
    collection: str,
    kind: str | None = Query(default=None, max_length=20),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Distinct customer/vendor/contact names in a register, A-Z."""
    filters = _filters(db, principal, _register(collection), branch_id)
    if kind and kind != "all":
        filters.append(AppDataRecord.doc_kind == kind)
    rows = (
        db.query(AppDataRecord.party).filter(*filters, AppDataRecord.party.isnot(None))
        .distinct().order_by(AppDataRecord.party).limit(2000).all()
    )
    return {"ok": True, "parties": [name for (name,) in rows if name]}


@router.get("/registers/{collection}/summary")
def get_register_summary(
    collection: str,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    collection = _register(collection)
    filters = _filters(db, principal, collection, branch_id)
    if collection == "salesInvoices":
        return _sales_summary(db, filters)
    if collection == "bills":
        return _bills_summary(db, filters)
    if collection == "payments":
        amount = func.coalesce(AppDataRecord.amount, 0)
        is_out = AppDataRecord.doc_kind == "supplier"
        count, count_out, inflow, outflow = db.query(
            func.count(AppDataRecord.id),
            func.coalesce(func.sum(case((is_out, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_out, 0), else_=amount)), 0),
            func.coalesce(func.sum(case((is_out, amount), else_=0)), 0),
        ).filter(*filters).one()
        return {
            "ok": True, "count": int(count or 0), "count_in": int((count or 0) - (count_out or 0)),
            "count_out": int(count_out or 0), "inflow": float(inflow or 0), "outflow": float(outflow or 0),
        }
    if collection == "expenses":
        return _expenses_summary(db, filters)
    count, total = db.query(
        func.count(AppDataRecord.id), func.coalesce(func.sum(AppDataRecord.amount), 0)
    ).filter(*filters).one()
    return {"ok": True, "count": int(count or 0), "total": float(total or 0)}


def _expenses_summary(db: Session, filters: list[Any]) -> dict[str, object]:
    """Expenses page cards (by status, as updateExpenseStats() bucketed the rows) and
    the dashboard's direct-expense figure."""
    amount = func.coalesce(AppDataRecord.amount, 0)

    def total_where(condition: Any) -> Any:
        return func.coalesce(func.sum(case((condition, amount), else_=0)), 0)

    count, total, pending, approved, rejected, direct = db.query(
        func.count(AppDataRecord.id),
        func.coalesce(func.sum(amount), 0),
        total_where(AppDataRecord.doc_status.like("%pending%")),
        total_where(AppDataRecord.doc_status.like("%approved%")),
        total_where(AppDataRecord.doc_status.like("%rejected%")),
        total_where(AppDataRecord.doc_kind == "direct"),
    ).filter(*filters).one()
    return {
        "ok": True, "count": int(count or 0), "total": float(total or 0), "pending": float(pending or 0),
        "approved": float(approved or 0), "rejected": float(rejected or 0), "direct": float(direct or 0),
    }


def _sales_summary(db: Session, filters: list[Any]) -> dict[str, object]:
    """Sales register KPIs over every invoice (returns excluded), in the buckets the
    cards always used: collected = paid, overdue = marked overdue with nothing received
    yet, pending = everything else (Partial included)."""
    amount = func.coalesce(AppDataRecord.amount, 0)
    paid = func.coalesce(AppDataRecord.amount_paid, 0)
    has_receipts = paid > 0
    collected = or_(
        and_(has_receipts, amount - paid <= PAID_TOLERANCE),
        and_(~has_receipts, AppDataRecord.doc_status == "paid"),
    )
    overdue = and_(~has_receipts, AppDataRecord.doc_status.like("%overdue%"))
    count, total, collected_total, overdue_total = (
        db.query(
            func.count(AppDataRecord.id),
            func.coalesce(func.sum(amount), 0),
            func.coalesce(func.sum(case((collected, amount), else_=0)), 0),
            func.coalesce(func.sum(case((overdue, amount), else_=0)), 0),
        )
        .filter(*filters, AppDataRecord.doc_kind == "invoice")
        .one()
    )
    total, collected_total, overdue_total = (Decimal(str(v or 0)) for v in (total, collected_total, overdue_total))
    return {
        "ok": True,
        "count": int(count or 0),
        "total": float(total),
        "collected": float(collected_total),
        "overdue": float(overdue_total),
        "pending": float(total - collected_total - overdue_total),
    }


def _bills_open_filters() -> list[Any]:
    return [
        AppDataRecord.doc_status.notin_(_BILL_CLOSED_STATUSES),
        func.coalesce(AppDataRecord.amount, 0) - func.coalesce(AppDataRecord.amount_paid, 0) > PAID_TOLERANCE,
    ]


def _bills_summary(db: Session, filters: list[Any]) -> dict[str, object]:
    """Bills page cards: what is still owed on open bills, and how much of it falls due
    in the next 7 days."""
    count, total = db.query(
        func.count(AppDataRecord.id), func.coalesce(func.sum(AppDataRecord.amount), 0)
    ).filter(*filters).one()
    open_rows = (
        db.query(AppDataRecord.amount, AppDataRecord.amount_paid, AppDataRecord.payload)
        .filter(*filters, *_bills_open_filters())
        .all()
    )
    today = _dt.date.today()
    week_out = today + _dt.timedelta(days=7)
    open_total = due_total = Decimal("0")
    due_count = 0
    for amount, paid, payload in open_rows:
        owed = Decimal(str(amount or 0)) - _paid(paid)
        open_total += owed
        try:
            due = parse_document_date(json.loads(payload or "{}").get("due"))
        except (TypeError, ValueError, AttributeError):
            due = None
        if due and today <= due <= week_out:
            due_total += owed
            due_count += 1
    return {
        "ok": True, "count": int(count or 0), "total": float(total or 0),
        "open_total": float(open_total), "open_count": len(open_rows),
        "due_week_total": float(due_total), "due_week_count": due_count,
    }


@router.get("/registers/bills/vendor-balances")
def bill_vendor_balances(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Still owed per vendor (lower-cased name) on open bills."""
    filters = _filters(db, principal, "bills", None)
    owed = func.coalesce(func.sum(func.coalesce(AppDataRecord.amount, 0) - func.coalesce(AppDataRecord.amount_paid, 0)), 0)
    name = func.lower(AppDataRecord.party)
    rows = db.query(name, owed).filter(*filters, *_bills_open_filters(), AppDataRecord.party.isnot(None)).group_by(name).all()
    return {"ok": True, "balances": {key: float(value or 0) for key, value in rows if key}}


@router.get("/registers/payments/next-ref")
def next_payment_ref(
    kind: str = Query(pattern="^(customer|supplier)$"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Next receipt (RCT-) / payment (PAY-) number: one past the highest trailing number
    on any saved one of that kind, as app.js nextPaymentReference() did over its table."""
    filters = _filters(db, principal, "payments", None)
    highest = 0
    for (key,) in db.query(AppDataRecord.record_key).filter(*filters, AppDataRecord.doc_kind == kind).yield_per(1000):
        match = re.search(r"(\d+)$", str(key or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    prefix = "PAY" if kind == "supplier" else "RCT"
    return {"ok": True, "ref": f"{prefix}-{_dt.date.today().year}-{highest + 1:04d}"}


@router.get("/payables/open")
def open_payables(
    side: str = Query(pattern="^(customer|supplier)$"),
    party: str | None = Query(default=None, max_length=160),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Documents with money still to collect (customer: sales invoices) or pay (supplier:
    bills and purchase records), oldest first: the payment screen's allocation list.
    amount is what is left after payments already allocated."""
    sources = {"salesInvoices": "Invoice"} if side == "customer" else {"bills": "Bill", "purchaseRecords": "Purchase Invoice"}
    docs = []
    for collection, label in sources.items():
        filters = _filters(db, principal, collection, branch_id)
        filters += _open_filters()
        if collection == "salesInvoices":
            filters.append(AppDataRecord.doc_kind == "invoice")
        else:
            filters.append(AppDataRecord.party.isnot(None))
        if party and party.strip():
            filters.append(func.lower(AppDataRecord.party) == party.strip().lower())
        rows = (
            db.query(
                AppDataRecord.record_key, AppDataRecord.party, AppDataRecord.record_date,
                AppDataRecord.amount, AppDataRecord.amount_paid, AppDataRecord.doc_status,
            )
            .filter(*filters)
            .order_by(AppDataRecord.record_date.is_(None), AppDataRecord.record_date, AppDataRecord.created_at)
            .limit(2000)
            .all()
        )
        for key, name, day, total, paid, status in rows:
            total = total or Decimal("0")
            paid = _paid(paid)
            docs.append({
                "ref": key or "",
                "contact": name or "",
                "date": day or "",
                "amount": float(total - paid),
                "original_amount": float(total),
                "status": effective_status((status or "").title(), total, paid),
                "source": label,
            })
    # One FIFO list across sources: undated documents last (app.js _sortPaymentDocsFifo()).
    docs.sort(key=lambda d: (not d["date"], d["date"]))
    return {"ok": True, "documents": docs}


# ── sales register (URLs the sales page and its tests already use) ───────────

@router.get("/sales-invoices")
def list_sales_invoices(
    kind: str = Query(default="invoice", pattern="^(invoice|return|all)$"),
    q: str | None = Query(default=None, max_length=120),
    contains: str | None = Query(default=None, max_length=160),
    customer: str | None = Query(default=None, max_length=160),
    product: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One page of the sales register, newest first.
    q: invoice no., customer, status, salesperson or date. contains: anywhere in the saved
    invoice, line items included. customer: exact customer name (any case). product: a
    line item whose description/product name is exactly this (any case)."""
    return list_register(
        db, principal, "salesInvoices", kind=kind, q=q, contains=contains, party=customer, product=product,
        limit=limit, offset=offset, branch_id=branch_id,
    )


@router.get("/sales-invoices/summary")
def sales_invoice_summary(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    return _sales_summary(db, _filters(db, principal, "salesInvoices", branch_id))


@router.get("/sales-invoices/open")
def open_sales_invoices(
    customer: str | None = Query(default=None, max_length=160),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    return open_payables(side="customer", party=customer, branch_id=branch_id, db=db, principal=principal)


@router.get("/sales-invoices/by-number")
def get_sales_invoice_by_number(
    no: str = Query(min_length=1, max_length=160),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    try:
        return get_register_record("salesInvoices", key=no, branch_id=branch_id, db=db, principal=principal)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Sales invoice not found") from exc
        raise


@router.get("/sales-invoices/salespeople")
def sales_invoice_salesperson_stats(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Invoice count and total per salesperson (lower-cased name), returns included."""
    filters = _filters(db, principal, "salesInvoices", None)
    name = func.lower(AppDataRecord.salesperson)
    rows = (
        db.query(name, func.count(AppDataRecord.id), func.coalesce(func.sum(AppDataRecord.amount), 0))
        .filter(*filters, AppDataRecord.salesperson.isnot(None))
        .group_by(name)
        .all()
    )
    return {"ok": True, "stats": {key: {"count": int(count), "total": float(total or 0)} for key, count, total in rows if key}}


@router.get("/sales-invoices/stock-movements")
def sales_invoice_stock_movements(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One 'sale' movement per invoice line, for the Stock Movements view. POS sales are
    skipped: they already have a real StockMovement row (sync_pos_stock())."""
    filters = _filters(db, principal, "salesInvoices", None)
    movements: list[dict[str, Any]] = []
    query = (
        db.query(AppDataRecord.payload)
        .filter(*filters, AppDataRecord.doc_kind == "invoice")
        .order_by(AppDataRecord.created_at)
        .yield_per(500)
    )
    for (payload,) in query:
        try:
            inv = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(inv, dict) or not inv.get("date") or str(inv.get("source") or "").lower().startswith("pos"):
            continue
        for line in inv["lines"] if isinstance(inv.get("lines"), list) else []:
            if not isinstance(line, dict):
                continue
            item = str(line.get("description") or line.get("product") or "").strip()
            try:
                qty = float(line.get("qty") or line.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if item and qty:
                movements.append({
                    "item_name": item, "movement_type": "sale", "quantity": -qty, "date": inv.get("date"),
                    "reference": inv.get("invoice_no") or "", "unit": line.get("unit") or "PCS",
                })
    return {"ok": True, "movements": movements}


# ── purchase documents (uploaded invoice files) ───────────────────────────────
# Each one carries the uploaded file as base64 plus everything extracted from it,
# often hundreds of KB. They used to ride in every bootstrap (up to 500, parsed on
# the server on every page open); the Purchases upload screen now asks for them.

@router.get("/purchase-documents")
def list_purchase_documents(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Newest first, without the file itself (fetch one with /purchase-documents/{id})."""
    filters = _filters(db, principal, "purchaseDocuments", None)
    total = db.query(func.count(AppDataRecord.id)).filter(*filters).scalar() or 0
    rows = (
        db.query(AppDataRecord).filter(*filters)
        .order_by(AppDataRecord.created_at.desc(), AppDataRecord.id.desc())
        .offset(offset).limit(limit).all()
    )
    records = []
    for row in rows:
        record = serialize(row)
        if isinstance(record, dict):
            record.pop("base64", None)
            records.append(record)
    return {"ok": True, "records": records, "total": total, "has_more": offset + len(rows) < total}


@router.get("/purchase-documents/{doc_id}")
def get_purchase_document(
    doc_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One uploaded document, file included."""
    filters = _filters(db, principal, "purchaseDocuments", None)
    row = db.query(AppDataRecord).filter(*filters, AppDataRecord.record_key == doc_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True, "record": serialize(row)}
