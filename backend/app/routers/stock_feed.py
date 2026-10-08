"""Stock Movements, filtered and paged in the database.

The stock Monthly History popup (and the Stock Movements tab before it) used to
download every movement the company ever had --
the newest 500 purchase movements (GET /inventory/stock-movements) plus a 'sale'
movement for every line of every sales invoice (GET /sales-invoices/stock-movements)
-- then filter by item/month and work out running balances in the browser. Besides
the download size, the 500-purchase cap made balances wrong once a company had more
purchases than that.

Here both sources are one UNION ALL query: purchases from stock_movements, sales from
document_lines (app/doc_lines.py) joined to their invoices. Filters, paging and the
per-item running balance (a window SUM over the item's whole history) all run in SQL,
so a page costs the same however many years of movements there are.

Rows mirror what the two old endpoints returned, with two fixes: dates are the
documents' parsed ISO dates (record_date; a tracked-stock sale takes its invoice's date,
not the day it was saved), so they sort correctly whatever format the invoice used; and
an invoice whose tracked items already have real stock movements isn't counted a
second time from its lines.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, String, and_, cast, exists, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session

from app.auth_principal import resolve_active_branch
from app.database import get_db
from app.dependencies import Principal, get_current_principal
from app.models import AppDataRecord, DocumentLine, StockMovement, StockProductMapping
from app.routers.inventory import backfill_purchase_stock_movements, inventory_backfill_disabled
from app.routers.registers import _filters

router = APIRouter(prefix="/app-data", tags=["stock-movements"])

_MONTH = r"^\d{4}-\d{2}$"


def _purchase_rows(db: Session, principal: Principal, branch_id: str | None):
    """stock_movements rows, as GET /inventory/stock-movements shows them (no 500 cap)."""
    item = func.coalesce(func.nullif(StockProductMapping.taxflow_name, ""), func.nullif(StockProductMapping.name, ""),
                         StockProductMapping.sku)
    # Each document number's date (and a purchase's supplier), worked out ONCE per request
    # with GROUP BY and joined in. A correlated lookup per movement made the database walk
    # the company's documents for every movement (a page took 60+ s at 15,000 movements).
    # MIN(): a number saved on more than one record gives one value, never extra rows.
    def by_number(collection: str):
        return (
            select(AppDataRecord.record_key.label("ref"), func.min(AppDataRecord.record_date).label("day"),
                   func.min(AppDataRecord.party).label("party"))
            .where(AppDataRecord.company_id == principal.company_id, AppDataRecord.collection == collection,
                   AppDataRecord.record_key.isnot(None))
            .group_by(AppDataRecord.record_key)
            .subquery()
        )

    purchases = by_number("purchaseRecords")
    # Tracked-stock sales are saved as reference "SALE-<invoice no>" (sync_sales_invoice_stock).
    sales = by_number("salesInvoices")
    day = func.coalesce(purchases.c.day, sales.c.day)
    vendor = purchases.c.party
    query = (
        select(
            func.coalesce(day, func.substr(cast(StockMovement.created_at, String), 1, 10)).label("day"),
            StockMovement.created_at.label("seq_at"),
            StockMovement.id.label("seq_id"),
            literal(0).label("seq_line"),
            StockMovement.movement_type.label("movement_type"),
            item.label("item_name"),
            cast(StockMovement.quantity, Float).label("quantity"),
            cast(StockMovement.unit_cost, Float).label("unit_cost"),
            StockMovement.reference.label("reference"),
            null().label("unit"),
            vendor.label("vendor_name"),
        )
        .join(StockProductMapping, StockMovement.mapping_id == StockProductMapping.id)
        .outerjoin(purchases, purchases.c.ref == StockMovement.reference)
        # Match on the number after "SALE-" (a computed key on the grouped side can't be
        # indexed, which made this a scan of every invoice per movement).
        .outerjoin(sales, and_(StockMovement.reference.like("SALE-%"),
                               sales.c.ref == func.substr(StockMovement.reference, 6)))
        .where(StockMovement.company_id == principal.company_id)
    )
    if principal.can_cross_branch("inventory"):
        if branch_id:
            query = query.where(StockMovement.branch_id == branch_id)
    else:
        active = resolve_active_branch(principal, branch_id)
        if active:
            query = query.where(or_(StockMovement.branch_id == active, StockMovement.branch_id.is_(None)))
    return query


def _sale_rows(db: Session, principal: Principal):
    """A 'sale' movement per sales invoice line, as GET /sales-invoices/stock-movements gives
    them (invoices only, dated, not POS, named item, non-zero quantity) -- except invoices
    that already have real stock movements (tracked stock items, reference SALE-<no>):
    the old screen showed both, counting those sales twice. None when the login can't
    read sales invoices: the screen then shows purchases only, as before."""
    try:
        filters = _filters(db, principal, "salesInvoices", None)
    except HTTPException:
        return None
    return (
        select(
            AppDataRecord.record_date.label("day"),
            AppDataRecord.created_at.label("seq_at"),
            AppDataRecord.id.label("seq_id"),
            DocumentLine.line_no.label("seq_line"),
            literal("sale").label("movement_type"),
            DocumentLine.item_name.label("item_name"),
            (-cast(DocumentLine.quantity, Float)).label("quantity"),
            null().label("unit_cost"),
            DocumentLine.doc_ref.label("reference"),
            DocumentLine.unit.label("unit"),
            null().label("vendor_name"),
        )
        .join(AppDataRecord, AppDataRecord.id == DocumentLine.record_id)
        .where(
            *filters,
            AppDataRecord.doc_kind == "invoice",
            DocumentLine.doc_date.isnot(None),
            or_(DocumentLine.doc_source.is_(None), ~DocumentLine.doc_source.like("pos%")),
            DocumentLine.item_name.isnot(None),
            DocumentLine.quantity.isnot(None),
            DocumentLine.quantity != 0,
            ~exists().where(
                StockMovement.company_id == AppDataRecord.company_id,
                StockMovement.movement_type.in_(("sales_invoice", "sales_return")),
                StockMovement.reference == literal("SALE-") + AppDataRecord.record_key,
            ),
        )
    )


def _movements(db: Session, principal: Principal, branch_id: str | None):
    if not inventory_backfill_disabled(db, principal.company_id):
        backfill_purchase_stock_movements(db, principal)
    parts = [_purchase_rows(db, principal, branch_id)]
    sales = _sale_rows(db, principal)
    if sales is not None:
        parts.append(sales)
    return union_all(*parts).subquery("movements")


@router.get("/stock-movements")
def list_stock_movements(
    item: str | None = Query(default=None, max_length=200),
    month: str | None = Query(default=None, pattern=_MONTH),
    limit: int = Query(default=50, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One page of stock movements, newest first. item: exact item name (any case).
    month: YYYY-MM. Each row's balance is the item's stock after that movement, counted
    over its whole history (so a month filter doesn't restart it at zero)."""
    m = _movements(db, principal, branch_id)
    # The item filter goes BEFORE the running balance (a balance is per item, so this
    # gives the same figures and only that item's rows get sorted); the month filter goes
    # after it, so a month doesn't restart the balance at zero.
    item_match = func.lower(m.c.item_name) == item.strip().lower() if item and item.strip() else None
    month_match = (lambda col: func.substr(col, 1, 7) == month) if month else None

    count_query = select(func.count()).select_from(m)
    if item_match is not None:
        count_query = count_query.where(item_match)
    if month_match is not None:
        count_query = count_query.where(month_match(m.c.day))
    total = db.execute(count_query).scalar() or 0

    order = (m.c.day.asc().nulls_first(), m.c.seq_at.asc(), m.c.seq_id.asc(), m.c.seq_line.asc())
    balanced = select(
        m,
        func.sum(m.c.quantity).over(partition_by=func.lower(m.c.item_name), order_by=order).label("balance"),
    )
    if item_match is not None:
        balanced = balanced.where(item_match)
    balanced = balanced.subquery("balanced")
    page_query = select(balanced).order_by(
        balanced.c.day.desc().nulls_last(), balanced.c.seq_at.desc(), balanced.c.seq_id.desc(), balanced.c.seq_line.desc(),
    ).offset(offset).limit(limit)
    if month_match is not None:
        page_query = page_query.where(month_match(balanced.c.day))
    rows = db.execute(page_query).mappings().all()
    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "movements": [
            {
                "date": row["day"] or "",
                "movement_type": row["movement_type"],
                "item_name": row["item_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "unit_cost": None if row["unit_cost"] is None else float(row["unit_cost"]),
                "reference": row["reference"] or "",
                "unit": row["unit"] or ("PCS" if row["movement_type"] == "sale" else None),
                "vendor_name": row["vendor_name"] or "",
                "balance": float(row["balance"] or 0),
            }
            for row in rows
        ],
    }

