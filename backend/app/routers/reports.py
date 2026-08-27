import hashlib
import json
from datetime import date as _date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError as SQLAOperationalError
from sqlalchemy.exc import TimeoutError as SQLATimeoutError
from sqlalchemy.orm import Session

import app.cache as cache
from app.auth_principal import Principal, require_principal_permission, resolve_active_branch
from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import (
    Account,
    AccrualPrepaymentRecord,
    AppDataRecord,
    AuditLog,
    AuditLogDetail,
    Branch,
    BudgetRecord,
    CashFlowForecastRecord,
    ConsolidationRecord,
    CorporateTaxRecord,
    CostCenterRecord,
    Document,
    Employee,
    ExceptionEvent,
    FixedAssetRecord,
    GeneralLedgerEntry,
    Invoice,
    Job,
    JournalEntry,
    JournalLine,
    MonthEndCloseRecord,
    Payment,
    PayrollRun,
    Receipt,
    SourceTransaction,
    StockProductMapping,
    TaxCode,
    TaxLine,
    User,
    Warehouse,
)


router = APIRouter(prefix="/reports", tags=["reports"])


def _cached_or_build(key: str, fresh_ttl: int, build_fn) -> dict[str, Any]:
    """Fresh cache hit -> return immediately. Otherwise call build_fn(); on
    success, cache (with a 24h staleness safety net, see
    cache.set_with_staleness()) and return the fresh result. On a DB
    overload specifically -- sqlalchemy.exc.TimeoutError (the local
    connection pool couldn't hand out a connection within pool_timeout) or
    OperationalError (the DB SERVER itself refused/dropped the connection,
    e.g. its own max_connections limit hit — confirmed via live load
    testing to be at least as common as the pool-side TimeoutError in
    practice, so both need the same treatment) -- fall back to whatever was
    last cached, even if stale, rather than a hard error -- for any company
    that's loaded this report at all in the last day, a burst of concurrent
    load becomes "you got slightly-old numbers" instead of a 500/503 to the
    user. Only lets the error propagate (to main.py's 503 handler) when
    there's truly nothing cached to fall back to."""
    data, is_fresh = cache.get_with_staleness(key, fresh_ttl)
    if is_fresh:
        return data
    try:
        result = build_fn()
    except (SQLATimeoutError, SQLAOperationalError):
        if data is not None:
            stale = dict(data)
            stale["stale"] = True
            return stale
        raise
    cache.set_with_staleness(key, result)
    return result


@router.get("/dashboard")
@limiter.limit("120/minute")
def dashboard(
    request: Request,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("reports:view")),
) -> dict[str, Any]:
    # Widened from admin-only in the "Main Dashboard Access" phase. Was
    # unconditionally company-wide regardless of who asked; now scoped the
    # same way trial_balance() already is — a branch-locked Employee/Branch
    # principal sees only their own branch's revenue/purchases/invoices,
    # an admin (or a "reports:view_all_branches" employee) sees everything
    # by default and can opt into one branch via ?branch_id=.
    company_id = principal.company_id
    resolved_branch_id = branch_id if principal.can_cross_branch("reports") else resolve_active_branch(principal, branch_id)
    # Cache key includes branch_id so one branch's result is never served
    # to another branch or to the unscoped company-wide view.
    cache_key = f"dashboard:{company_id}:{resolved_branch_id or 'all'}"
    return _cached_or_build(cache_key, 60, lambda: _build_dashboard(db, company_id, resolved_branch_id))


@router.get("/branch-performance")
@limiter.limit("120/minute")
def branch_performance(request: Request, db: Session = Depends(get_db), principal: Principal = Depends(require_principal_permission("reports:view"))) -> dict[str, Any]:
    company_id = principal.company_id
    data = _cached_or_build(f"branch_performance:{company_id}", 60, lambda: _build_branch_performance(db, company_id))
    if principal.can_cross_branch("reports"):
        return data
    # A Branch Login (or a branch-locked Employee) must only ever see its own
    # branch's row here, not every other branch's revenue/profit — the
    # underlying build is cached company-wide (correct, since the data
    # itself doesn't vary by requester), so this filters the response per
    # request instead of computing/caching a separate copy per branch.
    allowed = principal.accessible_branch_ids
    return {
        "has_branches": data["has_branches"],
        "branches": [row for row in data["branches"] if row["branch_id"] in allowed],
        "unassigned": None,
    }


def _build_dashboard(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    # branch_id scopes revenue/purchases/invoices — the figures a branch
    # manager actually asked to see broken out per branch. Staff/payroll
    # deliberately stay company-wide even when branch_id is set: HRMS/ESS
    # access is explicitly never branch-gated anywhere else in this app
    # (module_catalog.py's BRANCH_ELIGIBLE_MODULES excludes "hrms"/"ess" for
    # exactly this reason — "governed purely by an Employee's own Role,
    # never by which branch they're assigned to"), and VAT (TaxLine) has no
    # branch_id column to filter on at all. module_counts/recent_activity
    # (audit log) are unfiltered admin/system-health figures, not
    # branch-owned business data, so they stay company-wide too.
    app_sales = app_sales_invoice_records(db, company_id, branch_id)
    app_purchases = app_purchase_records(db, company_id, branch_id)
    revenue_query = db.query(func.coalesce(func.sum(Invoice.subtotal), 0)).filter(Invoice.company_id == company_id, Invoice.status != "draft")
    open_count_query = db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id, Invoice.status != "paid")
    open_amount_query = db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id, Invoice.status != "paid")
    if branch_id:
        branch_filter = (Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None))
        revenue_query = revenue_query.filter(branch_filter)
        open_count_query = open_count_query.filter(branch_filter)
        open_amount_query = open_amount_query.filter(branch_filter)
    revenue = money(revenue_query.scalar())
    revenue += sum((record_amount(row, "subtotal", "net_amount", "amount") for row in app_sales if normalized_ref(row.get("status", "")) != "draft"), Decimal("0.00"))
    open_invoice_count = int(open_count_query.scalar() or 0)
    app_open_sales = [row for row in app_sales if not is_paid_status(row.get("status")) and not _is_credit_note(row)]
    open_invoice_count += len(app_open_sales)
    open_invoice_amount = money(open_amount_query.scalar())
    open_invoice_amount += sum((record_amount(row, "total", "amount", "net_amount") for row in app_open_sales), Decimal("0.00"))
    payroll_net = money(db.query(func.coalesce(func.sum(PayrollRun.net_total), 0)).filter(PayrollRun.company_id == company_id).scalar())
    output_vat = money(db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0)).filter(TaxLine.company_id == company_id, TaxLine.direction == "output").scalar())
    input_vat = money(db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0)).filter(TaxLine.company_id == company_id, TaxLine.direction == "input").scalar())
    output_vat += sum((record_amount(row, "vat_amount", "vat", "tax_amount") for row in app_sales), Decimal("0.00"))
    input_vat += sum((record_amount(row, "tax_amount", "vat_amount", "vat") for row in app_purchases), Decimal("0.00"))
    vat_payable = output_vat - input_vat

    invoice_count = count(db, Invoice, company_id) + len(app_sales)
    employee_count = count(db, Employee, company_id)
    app_counts = app_data_counts(db, company_id)
    table_counts = _company_table_counts(db, company_id, [
        "accounts", "journal_entries", "tax_codes", "tax_lines", "warehouses",
        "stock_product_mappings", "payroll_runs", "jobs", "documents",
        "audit_logs", "exception_events", "receipts", "payments",
    ])
    source_tx_by_module = _source_transaction_module_counts(db, company_id)
    sales_source_count = sum(source_tx_by_module.get(m, 0) for m in ("sales", "sales_invoice"))
    purchase_source_count = sum(source_tx_by_module.get(m, 0) for m in ("purchase", "purchase_bill"))
    module_counts = {
        "invoice_count": invoice_count,
        "product_count": app_counts.get("products", 0),
        "customer_count": app_counts.get("customers", 0),
        "quotation_count": app_counts.get("quotations", 0),
        "purchase_record_count": app_counts.get("purchaseRecords", 0),
        "sales_category_count": app_counts.get("salesCategories", 0),
        "sales_unit_count": app_counts.get("salesUnits", 0),
        "bill_count": app_counts.get("bills", 0),
        "vendor_count": app_counts.get("vendors", 0),
        "payment_count": app_counts.get("payments", 0),
        "sales_source_count": sales_source_count,
        "purchase_source_count": purchase_source_count,
        "account_count": table_counts.get("accounts", 0),
        "journal_count": table_counts.get("journal_entries", 0),
        "source_transaction_count": sum(source_tx_by_module.values()),
        "tax_code_count": table_counts.get("tax_codes", 0),
        "tax_line_count": table_counts.get("tax_lines", 0),
        "warehouse_count": table_counts.get("warehouses", 0),
        "inventory_mapping_count": table_counts.get("stock_product_mappings", 0),
        "employee_count": employee_count,
        "payroll_run_count": table_counts.get("payroll_runs", 0),
        "job_count": table_counts.get("jobs", 0),
        "document_count": table_counts.get("documents", 0),
        "audit_count": table_counts.get("audit_logs", 0),
        "exception_count": table_counts.get("exception_events", 0),
        "receipt_count": table_counts.get("receipts", 0),
        "payment_receipt_count": table_counts.get("payments", 0) + table_counts.get("receipts", 0),
        "purchase_invoice_count": app_counts.get("purchaseInvoices", 0) + app_counts.get("purchaseDocuments", 0),
    }
    status = invoice_status(db, company_id, app_sales, branch_id)
    pur_summary = _purchase_summary(db, company_id, branch_id)
    return {
        "kpis": {
            "revenue": amount(revenue),
            "total_purchases": pur_summary["total"],
            "vat_payable": amount(vat_payable),
            "output_vat": amount(output_vat),
            "input_vat": amount(input_vat),
            "open_invoice_count": open_invoice_count,
            "open_invoice_amount": amount(open_invoice_amount),
            "staff_total": employee_count,
            "staff_present": employee_count,
            "payroll_net": amount(payroll_net),
        },
        "monthly_revenue_vat": monthly_revenue_vat(db, company_id, app_sales, branch_id),
        "recent_activity": recent_activity(db, company_id),
        "top_customers": top_customers(db, company_id, app_sales, branch_id),
        "invoice_status": status,
        "purchase_summary": pur_summary,
        "staff_today": {
            "present": employee_count,
            "total": employee_count,
            "leave": 0,
            "absent": 0,
            "source": "Employees database",
        },
        "module_counts": module_counts,
        # Flat fields remain temporarily for older frontend versions.
        "revenue": amount(revenue),
        "open_invoices": open_invoice_count,
        "payroll_net": amount(payroll_net),
        "vat_payable": amount(vat_payable),
        **module_counts,
    }


def money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def amount(value: Decimal) -> str:
    return f"{value:.2f}"


def count(db: Session, model: object, company_id: str) -> int:
    return int(db.query(func.count(model.id)).filter(model.company_id == company_id).scalar() or 0)


def _company_table_counts(db: Session, company_id: str, tables: list[str]) -> dict[str, int]:
    """One round trip for N single-table `count(*) WHERE company_id=?`
    queries, via UNION ALL over a fixed, hardcoded table-name list (never
    request-derived, so string-building the table names in is safe — only
    company_id is a bind parameter). Replaces dashboard()'s previous ~14
    separate count() calls, one of the two biggest contributors to its
    per-request query count."""
    if not tables:
        return {}
    union_sql = " UNION ALL ".join(f"SELECT '{t}' AS k, count(*) AS n FROM {t} WHERE company_id = :cid" for t in tables)
    rows = db.execute(text(union_sql), {"cid": company_id}).all()
    return {k: int(n or 0) for k, n in rows}


def _source_transaction_module_counts(db: Session, company_id: str) -> dict[str, int]:
    """SourceTransaction count broken down by module, in one GROUP BY query
    instead of three separate count() calls (plain total, sales-only,
    purchase-only) each scanning the same table."""
    rows = (
        db.query(SourceTransaction.module, func.count(SourceTransaction.id))
        .filter(SourceTransaction.company_id == company_id)
        .group_by(SourceTransaction.module)
        .all()
    )
    return {module: int(n or 0) for module, n in rows}


def app_data_counts(db: Session, company_id: str) -> dict[str, int]:
    rows = (
        db.query(AppDataRecord.collection, func.count(AppDataRecord.id))
        .filter(AppDataRecord.company_id == company_id)
        .group_by(AppDataRecord.collection)
        .all()
    )
    return {collection: int(total or 0) for collection, total in rows}


def app_data_payloads(db: Session, company_id: str, collection: str) -> list[dict[str, Any]]:
    rows = (
        db.query(AppDataRecord.payload)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
        .all()
    )
    payloads: list[dict[str, Any]] = []
    for (payload,) in rows:
        try:
            data = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            payloads.append(data)
    return payloads


def app_data_payloads_with_branch(db: Session, company_id: str, collection: str) -> list[tuple[dict[str, Any], str | None]]:
    """Same as app_data_payloads() but also returns each row's AppDataRecord.branch_id
    (server-stamped at write time, see app_data.py — NULL for records saved by an
    admin/User principal rather than a branch-scoped Employee/Branch login)."""
    rows = (
        db.query(AppDataRecord.payload, AppDataRecord.branch_id)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
        .all()
    )
    out: list[tuple[dict[str, Any], str | None]] = []
    for payload, branch_id in rows:
        try:
            data = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((data, branch_id))
    return out


def normalized_ref(value: object) -> str:
    return str(value or "").strip().lower()


def _branch_row_included(row_branch_id: str | None, branch_id: str | None) -> bool:
    """Same rule _posted_journal_line_totals() uses for JournalEntry.branch_id:
    an unscoped caller (branch_id=None) sees everything; a branch-scoped
    caller sees its own branch's rows PLUS any row with no branch_id at all
    (predates Branch Management, or was saved by an admin/User principal —
    see app_data_payloads_with_branch()'s own docstring) rather than losing
    that data entirely."""
    return branch_id is None or row_branch_id == branch_id or row_branch_id is None


def app_sales_invoice_records(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    existing_refs = {
        normalized_ref(value)
        for (value,) in db.query(Invoice.invoice_number).filter(Invoice.company_id == company_id).all()
        if normalized_ref(value)
    }
    records = []
    for row, row_branch_id in app_data_payloads_with_branch(db, company_id, "salesInvoices"):
        if not _branch_row_included(row_branch_id, branch_id):
            continue
        invoice_ref = normalized_ref(row.get("invoice_no") or row.get("invoice_number") or row.get("ref"))
        if invoice_ref and invoice_ref in existing_refs:
            continue
        records.append(row)
    return records


def app_purchase_records(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    """purchaseRecords + bills app-data rows, deduped against purchases that
    already posted a real input TaxLine (saving a purchaseRecords or bills
    row triggers sync_purchase_accounting()/sync_bill_accounting() ->
    approve_and_post_source(), app_data.py's purchaseRecords/bills branches)
    — the same dedup app_sales_invoice_records() already does against
    Invoice, applied to the purchase side. Without this, _build_summary()'s
    input_vat double-counted: once from the real TaxLine, once from the raw
    app-data row's own tax_amount, for every normally-posted purchase —
    understating net_vat_payable. Bills were excluded here entirely until
    they started posting real TaxLines (see sync_bill_accounting()), which
    itself understated net_vat_payable by 100% of every bill's input VAT."""
    existing_refs = {
        normalized_ref(reference)
        for (reference,) in db.query(SourceTransaction.reference)
        .join(TaxLine, TaxLine.source_id == SourceTransaction.id)
        .filter(
            SourceTransaction.company_id == company_id,
            SourceTransaction.module.in_(["purchase", "purchase_bill"]),
            TaxLine.direction == "input",
        )
        .distinct()
        .all()
        if normalized_ref(reference)
    }
    records = []
    for row, row_branch_id in app_data_payloads_with_branch(db, company_id, "purchaseRecords"):
        if not _branch_row_included(row_branch_id, branch_id):
            continue
        # Mirrors app_data.py's sync_domain_model() reference formula for
        # purchaseRecords, so a row with no ref/invoice_no still dedupes
        # correctly against the "PURCHASE-{id}" fallback reference its own
        # posted TaxLine was created under.
        purchase_ref = normalized_ref(
            row.get("ref") or row.get("invoice_no")
            or (f"PURCHASE-{row['id']}" if row.get("id") else None)
        )
        if purchase_ref and purchase_ref in existing_refs:
            continue
        records.append(row)
    for row, row_branch_id in app_data_payloads_with_branch(db, company_id, "bills"):
        if not _branch_row_included(row_branch_id, branch_id):
            continue
        # Mirrors app_data.py's sync_domain_model() reference formula for bills.
        bill_ref = normalized_ref(
            row.get("bill_no")
            or (f"BILL-{row['id']}" if row.get("id") else None)
        )
        if bill_ref and bill_ref in existing_refs:
            continue
        records.append(row)
    return records


def record_amount(record: dict[str, Any], *keys: str) -> Decimal:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return money(value)
    return Decimal("0.00")


def is_paid_status(value: object) -> bool:
    return normalized_ref(value) in {"paid", "posted", "complete", "completed", "received", "settled"}


def period_label(value: object) -> str:
    if not value:
        return "Current"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m")
    return str(value)[:7]


def monthly_revenue_vat(db: Session, company_id: str, app_sales: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, str]]:
    periods: dict[str, dict[str, Decimal]] = {}
    # This function only ever returns the most recent 6 periods (see the
    # [-6:] below), but previously queried a company's ENTIRE invoice/
    # transaction history to compute them — cost that grows forever as a
    # tenant accumulates data. Bounding to ~7 months (6 target + 1 buffer
    # for edge-of-month safety) keeps the query cost roughly constant
    # regardless of company age, with no behavior change to the output.
    cutoff = (_date.today().replace(day=1) - timedelta(days=210)).replace(day=1)
    invoice_query = db.query(Invoice).filter(Invoice.company_id == company_id, Invoice.created_at >= cutoff)
    if branch_id:
        invoice_query = invoice_query.filter((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    invoices = invoice_query.all()
    for invoice in invoices:
        item = periods.setdefault(period_label(invoice.created_at), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["sales"] += money(invoice.total)
        item["output_vat"] += money(invoice.vat)
    for invoice in app_sales:
        item = periods.setdefault(period_label(invoice.get("date") or invoice.get("created_at")), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["sales"] += record_amount(invoice, "total", "amount", "net_amount")
        item["output_vat"] += record_amount(invoice, "vat_amount", "vat", "tax_amount")
    purchase_filters = [
        SourceTransaction.company_id == company_id,
        SourceTransaction.module.in_(["purchase", "purchase_bill"]),
        SourceTransaction.created_at >= cutoff,
    ]
    if branch_id:
        purchase_filters.append((SourceTransaction.branch_id == branch_id) | (SourceTransaction.branch_id.is_(None)))
    purchases = db.query(SourceTransaction).filter(*purchase_filters).all()
    for purchase in purchases:
        item = periods.setdefault(period_label(purchase.created_at), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["purchases"] += money(purchase.total)
        item["input_vat"] += money(purchase.vat)
    if not periods:
        periods["Current"] = {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")}
    return [
        {
            "period": period,
            "sales": amount(values["sales"]),
            "purchases": amount(values["purchases"]),
            "output_vat": amount(values["output_vat"]),
            "input_vat": amount(values["input_vat"]),
            "net_vat": amount(values["output_vat"] - values["input_vat"]),
        }
        for period, values in list(sorted(periods.items()))[-6:]
    ]


def recent_activity(db: Session, company_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == company_id)
        .order_by(AuditLog.created_at.desc())
        .limit(6)
        .all()
    )
    return [
        {
            "title": row.action.replace("_", " ").title(),
            "module": row.module,
            "time": row.created_at.strftime("%d %b %H:%M") if row.created_at else "Now",
            "tone": "ok" if index == 0 else "info",
        }
        for index, row in enumerate(rows)
    ]


def top_customers(db: Session, company_id: str, app_sales: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, str]]:
    query = (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0).label("total"))
        .filter(Invoice.company_id == company_id)
    )
    if branch_id:
        query = query.filter((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    rows = (
        query.group_by(Invoice.customer_name)
        .order_by(func.coalesce(func.sum(Invoice.total), 0).desc())
        .limit(5)
        .all()
    )
    totals: dict[str, Decimal] = {}
    for name, total in rows:
        totals[str(name or "Customer")] = money(total)
    for invoice in app_sales:
        name = str(invoice.get("customer") or invoice.get("customer_name") or "Customer")
        totals[name] = totals.get(name, Decimal("0.00")) + record_amount(invoice, "total", "amount", "net_amount")
    return [
        {"name": name, "total": amount(total)}
        for name, total in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:5]
    ]


def _purchase_row_amount(row: dict[str, Any]) -> Decimal:
    """Extract the total amount from a purchase record using multiple fallback strategies."""
    # 1. Try common total fields — skip if zero (might be unset placeholder)
    for key in ("total", "grand_total", "amount"):
        val = row.get(key)
        if val not in (None, ""):
            d = money(val)
            if d != Decimal("0"):
                return d
    # 2. net_amount + tax_amount
    net = money(row.get("net_amount") or row.get("subtotal") or 0)
    tax = money(row.get("tax_amount") or row.get("vat_amount") or row.get("vat") or 0)
    if net or tax:
        return net + tax
    # 3. Sum product lines
    lines = row.get("lines")
    if isinstance(lines, list):
        line_sum = sum(
            money(ln.get("total") or ln.get("line_total") or ln.get("amount") or 0)
            for ln in lines
        )
        if line_sum:
            return line_sum
    return Decimal("0.00")


def _purchase_row_net(row: dict[str, Any]) -> Decimal:
    """Extract net amount (excl. VAT) from a purchase record."""
    net = money(row.get("net_amount") or row.get("subtotal") or 0)
    if net:
        return net
    # Fall back: total minus VAT
    total = _purchase_row_amount(row)
    vat = money(row.get("vat_amount") or row.get("tax_amount") or row.get("vat") or 0)
    return total - vat if total else Decimal("0.00")


def _purchase_summary(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    records = [
        row for row, row_branch_id in (
            app_data_payloads_with_branch(db, company_id, "purchaseRecords")
            + app_data_payloads_with_branch(db, company_id, "bills")
        )
        if _branch_row_included(row_branch_id, branch_id)
    ]
    total = Decimal("0")
    net_total = Decimal("0")
    paid_amount = Decimal("0")
    paid_count = 0
    pending_count = 0
    for rec in records:
        row_total = _purchase_row_amount(rec)
        row_net = _purchase_row_net(rec)
        row_paid = record_amount(rec, "paid", "paid_amount")
        total += row_total
        net_total += row_net
        if is_paid_status(normalized_ref(rec.get("status") or "")):
            paid_count += 1
            paid_amount += row_paid if row_paid else row_total
        else:
            pending_count += 1
            paid_amount += row_paid
    total_count = len(records)

    # Fallback to SourceTransaction when no AppDataRecord entries exist
    if total == Decimal("0") and total_count == 0:
        st_filters = [
            SourceTransaction.company_id == company_id,
            SourceTransaction.module.in_(["purchase", "purchase_bill"]),
        ]
        if branch_id:
            st_filters.append((SourceTransaction.branch_id == branch_id) | (SourceTransaction.branch_id.is_(None)))
        total = money(
            db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
            .filter(*st_filters)
            .scalar()
        )
        net_total = total  # SourceTransaction has no separate net field
        total_count = int(
            db.query(func.count(SourceTransaction.id))
            .filter(*st_filters)
            .scalar() or 0
        )
        if paid_amount == Decimal("0"):
            paid_amount = money(
                db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
                .filter(*st_filters, SourceTransaction.status.in_(["paid", "posted", "complete", "completed", "received", "settled"]))
                .scalar()
            )

    payment_rate = int(paid_amount / total * 100) if total else 0
    return {
        "total": amount(total),
        "net": amount(net_total),
        "paid": amount(paid_amount),
        "paid_count": paid_count,
        "pending_count": pending_count,
        "total_count": total_count,
        "payment_rate": payment_rate,
    }


_INVOICE_STATUS_KEY = {"paid": "paid", "issued": "pending", "pending": "pending", "overdue": "overdue", "cancelled": "overdue", "draft": "draft"}


def invoice_status(db: Session, company_id: str, app_sales: list[dict[str, Any]], branch_id: str | None = None) -> dict[str, dict[str, str | int]]:
    # Single GROUP BY replaces what was previously 2 setup queries + 2
    # queries per bucket (4 buckets) = 10 queries total. Bucket names/amount
    # semantics preserved exactly: bucket amounts sum Invoice.total (not
    # subtotal), the overall "total" bucket sums Invoice.subtotal excluding
    # drafts, and any Invoice.status value outside the 4 known buckets still
    # counts toward "total" (if not draft) without landing in any bucket —
    # all of this matched the original per-bucket-query version's behavior.
    query = (
        db.query(Invoice.status, func.count(Invoice.id), func.coalesce(func.sum(Invoice.total), 0), func.coalesce(func.sum(Invoice.subtotal), 0))
        .filter(Invoice.company_id == company_id)
    )
    if branch_id:
        # Same NULL-stays-visible rule as _posted_journal_line_totals().
        query = query.filter((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    rows = query.group_by(Invoice.status).all()
    buckets: dict[str, dict[str, Any]] = {k: {"count": 0, "amount": Decimal("0.00")} for k in ("paid", "pending", "overdue", "draft")}
    total_count = 0
    total_amount = Decimal("0.00")
    for status, row_count, row_total, row_subtotal in rows:
        status_norm = normalized_ref(status)
        row_count = int(row_count or 0)
        key = _INVOICE_STATUS_KEY.get(status_norm)
        if key:
            buckets[key]["count"] += row_count
            buckets[key]["amount"] += money(row_total)
        if status_norm != "draft":
            total_count += row_count
            total_amount += money(row_subtotal)
    for invoice in app_sales:
        status = normalized_ref(invoice.get("status"))
        if status != "draft":
            total_count += 1
            total_amount += record_amount(invoice, "subtotal", "net_amount", "amount")
        key = _INVOICE_STATUS_KEY.get(status) or ("pending" if status in {"ready", "sent", "unpaid"} else None)
        if key:
            buckets[key]["count"] += 1
            buckets[key]["amount"] += record_amount(invoice, "total", "amount", "net_amount")
    statuses: dict[str, dict[str, str | int]] = {}
    for key, vals in buckets.items():
        pct = int((vals["count"] / total_count) * 100) if total_count else 0
        statuses[key] = {"count": vals["count"], "amount": amount(vals["amount"]), "percentage": pct}
    statuses["total"] = {"count": total_count, "amount": amount(total_amount), "percentage": 100 if total_count else 0}
    return statuses


def _build_branch_performance(db: Session, company_id: str) -> dict[str, Any]:
    """Per-branch revenue/purchases/profit + pending vs collected invoices.

    Deliberately reads ONLY AppDataRecord.branch_id, not Invoice.branch_id or
    SourceTransaction.branch_id (the "real" posted mirrors _build_summary()
    etc. read for company-wide totals). Those posting paths stamp branch_id
    from the acting principal at post time (see app_data.py's
    sync_domain_model()/approve_and_post_source()), not from the original
    record's own branch_id — so a record explicitly tagged with a branch_id
    in its payload (the override path app_data.py's save endpoint honors)
    can still post a SourceTransaction/Invoice with branch_id=NULL. Deduping
    AppDataRecord rows against those tables (the way app_sales_invoice_records()/
    app_purchase_records() do for company-wide totals) would then silently
    drop that row's amount from every branch bucket once it posts. The
    AppDataRecord.branch_id column is the one place branch attribution is
    actually reliable, so this reads it directly and doesn't dedupe against
    the posted tables — safe here because, unlike _build_summary(), nothing
    in this function also sums those posted tables to combine with it.

    Records saved by a company admin (branch_id NULL) land in the
    "unassigned" bucket rather than being dropped, since for most companies
    that's still the majority of the data."""
    branches = db.query(Branch).filter(Branch.company_id == company_id).all()
    branch_names = {b.id: b.name for b in branches}

    buckets: dict[str | None, dict[str, Decimal | int]] = {}

    def bucket(branch_id: str | None) -> dict[str, Decimal | int]:
        return buckets.setdefault(branch_id, {
            "revenue": Decimal("0.00"), "purchases": Decimal("0.00"),
            "paid_count": 0, "paid_amount": Decimal("0.00"),
            "pending_count": 0, "pending_amount": Decimal("0.00"),
        })

    for row, branch_id in app_data_payloads_with_branch(db, company_id, "salesInvoices"):
        status = normalized_ref(row.get("status"))
        b = bucket(branch_id)
        if status != "draft":
            b["revenue"] += record_amount(row, "subtotal", "net_amount", "amount")
        key = _INVOICE_STATUS_KEY.get(status) or ("pending" if status in {"ready", "sent", "unpaid"} else None)
        if key == "paid":
            b["paid_count"] += 1
            b["paid_amount"] += record_amount(row, "total", "amount", "net_amount")
        elif key in ("pending", "overdue"):
            b["pending_count"] += 1
            b["pending_amount"] += record_amount(row, "total", "amount", "net_amount")

    for row, branch_id in app_data_payloads_with_branch(db, company_id, "purchaseRecords"):
        bucket(branch_id)["purchases"] += _purchase_row_amount(row)
    for row, branch_id in app_data_payloads_with_branch(db, company_id, "bills"):
        bucket(branch_id)["purchases"] += _purchase_row_amount(row)

    def to_row(branch_id: str | None, name: str, vals: dict[str, Decimal | int]) -> dict[str, Any]:
        revenue = vals["revenue"]
        profit = revenue - vals["purchases"]
        margin = (profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
        return {
            "branch_id": branch_id,
            "name": name,
            "revenue": amount(revenue),
            "purchases": amount(vals["purchases"]),
            "profit": amount(profit),
            "margin_pct": amount(margin),
            "invoices_collected": {"count": vals["paid_count"], "amount": amount(vals["paid_amount"])},
            "invoices_pending": {"count": vals["pending_count"], "amount": amount(vals["pending_amount"])},
        }

    rows = [
        to_row(branch_id, branch_names.get(branch_id, "Unnamed Branch"), vals)
        for branch_id, vals in buckets.items()
        if branch_id is not None
    ]
    rows.sort(key=lambda r: Decimal(r["profit"]), reverse=True)

    unassigned_vals = buckets.get(None)
    has_unassigned_activity = bool(unassigned_vals) and any(
        unassigned_vals[k] for k in ("revenue", "purchases", "paid_count", "pending_count")
    )
    unassigned = to_row(None, "Unassigned", unassigned_vals) if has_unassigned_activity else None

    return {
        "has_branches": len(branches) > 0,
        "branches": rows,
        "unassigned": unassigned,
    }



@router.get("/debug/purchase")
@limiter.limit("120/minute")
def debug_purchase(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("reports:view")),
) -> dict[str, Any]:
    """Diagnostic: shows exactly what is stored in DB for bills/purchaseRecords.
    Was bare get_current_user (any authenticated session, no rate limit) —
    a branch-assigned employee with no reports access could read company-wide
    purchase data through it; now gated the same as every other report."""
    import json as _json
    company_id = principal.company_id
    if principal.branch_id and not principal.can_cross_branch("reports"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available to a branch login.")
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(["bills", "purchaseRecords"]))
        .order_by(AppDataRecord.created_at.desc())
        .limit(20)
        .all()
    )
    records = []
    for r in rows:
        try:
            parsed = _json.loads(r.payload or "{}")
        except Exception as e:
            parsed = {"_parse_error": str(e), "_raw": str(r.payload)[:200]}
        records.append({
            "id": r.id,
            "collection": r.collection,
            "record_key": r.record_key,
            "payload_length": len(r.payload or ""),
            "payload_preview": (r.payload or "")[:300],
            "parsed_total": parsed.get("total"),
            "parsed_status": parsed.get("status"),
            "parsed_vendor": parsed.get("vendor"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    purchase_summary = _purchase_summary(db, company_id)
    return {
        "company_id": company_id,
        "record_count": len(rows),
        "purchase_summary": purchase_summary,
        "records": records,
    }


@router.get("/trial-balance")
@limiter.limit("120/minute")
def trial_balance(
    request: Request,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("reports:view")),
) -> dict[str, Any]:
    company_id = principal.company_id
    # Branch Management, Phase 3: a branch-assigned employee sees only their
    # branch's posted journal entries (plus branch-less legacy data); the
    # company admin (branch_id always None) sees everything, as before.
    # Branch Security Layer Phase 2: "reports:view_all_branches" lets a
    # specific branch employee see company-wide totals too, without being
    # a full admin. Phase 3: ?branch_id= lets an unrestricted viewer (admin
    # or cross-branch employee) opt into ONE branch's figures, and a
    # genuinely multi-branch employee pick among their own assigned set.
    resolved_branch_id = branch_id if principal.can_cross_branch("reports") else resolve_active_branch(principal, branch_id)
    # Cache key includes branch_id so one branch's result is never served
    # to another branch or to the unscoped company-wide view.
    cache_key = f"trial_balance:{company_id}:{resolved_branch_id or 'all'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = {"status": "ready", "source": "posted journal entries", "rows": trial_balance_rows(db, company_id, resolved_branch_id)}
    cache.set(cache_key, result, ttl=120)
    return result


@router.get("/summary")
@limiter.limit("120/minute")
def report_summary(
    request: Request,
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("reports:view")),
) -> dict[str, Any]:
    # See dashboard()'s comment above — same widening, same reasoning.
    company_id = principal.company_id
    # Most sections of _build_summary() are now branch-scoped (revenue,
    # purchases, payroll, expenses, balance sheet, trial balance, GL,
    # customer/supplier ledger, AR/AP aging, revenue intelligence) via the
    # same resolve_active_branch()/can_cross_branch() pattern dashboard()
    # uses — a Branch Login now sees its OWN branch's numbers instead of
    # either the whole company's (the original leak) or a hard 403 (this
    # endpoint's first fix). A few sections remain company-wide because
    # their backing tables have no branch_id column at all — see the
    # comments inside _build_summary() next to corporate/assets/
    # budget_cash/control and the VAT TaxLine breakdown.
    resolved_branch_id = branch_id if principal.can_cross_branch("reports") else resolve_active_branch(principal, branch_id)

    def _build() -> dict[str, Any]:
        result = _build_summary(db, company_id, resolved_branch_id)
        # Stable fingerprint for frontend diff-check (skips re-render when data unchanged)
        _sig = f"{result.get('dashboard',{}).get('revenue',0)}:{result.get('dashboard',{}).get('expenses',0)}:{result.get('dashboard',{}).get('net_profit',0)}"
        result["_version"] = hashlib.md5(_sig.encode()).hexdigest()[:12]
        return result

    cache_key = f"summary:{company_id}:{resolved_branch_id or 'all'}"
    return _cached_or_build(cache_key, 120, _build)


def _is_recognized_revenue_status(status: object) -> bool:
    """Only issued/paid invoices are recognized revenue — drafts aren't yet
    committed sales, and cancelled/returned invoices were never fulfilled."""
    return normalized_ref(status) in {"issued", "paid"}


def _is_credit_note(row: dict[str, Any]) -> bool:
    """A negative-signed return/credit-note document (POS Sales Return v1 —
    see _is_credit_note_record() in app_data.py, the write-side twin of
    this check). These never get a real Invoice row (app_data.py skips
    sync_sales_invoice() for them), so they're never deduped out of
    app_sales_invoice_records() and must be recognized here instead of via
    _is_recognized_revenue_status(), whose issued/paid check would
    otherwise exclude them. Gated on sign, not just status/text, so
    legacy hand-keyed Sales Returns — stored POSITIVE, excluded from this
    summary today — keep behaving exactly as they do today; only widening
    the status list would have retroactively inflated revenue from them."""
    text = f"{row.get('document_type','')} {row.get('source','')} {row.get('status','')}".lower()
    return "return" in text and record_amount(row, "total", "amount", "net_amount") < 0


def _build_summary(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    app_sales = app_sales_invoice_records(db, company_id, branch_id)
    app_purchases = app_purchase_records(db, company_id, branch_id)
    # Computed once and reused below (readiness.documents, ai.forecast_confidence,
    # einvoicing.total) — previously 3 separate identical count(Invoice) round trips.
    # Deliberately company-wide even when branch_id is set — a documents/
    # e-invoicing readiness score isn't a financial figure a branch could
    # leak, and scoping it would need its own branch-filtered count() variant.
    invoice_count_db = count(db, Invoice, company_id)
    recognized_app_sales = [
        row for row in app_sales if _is_recognized_revenue_status(row.get("status")) or _is_credit_note(row)
    ]
    revenue_filter = [Invoice.company_id == company_id, Invoice.status.in_(["issued", "paid"])]
    expense_filter = [SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["expense", "expenses"])]
    payroll_filter = [PayrollRun.company_id == company_id]
    if branch_id:
        revenue_filter.append((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
        expense_filter.append((SourceTransaction.branch_id == branch_id) | (SourceTransaction.branch_id.is_(None)))
        payroll_filter.append((PayrollRun.branch_id == branch_id) | (PayrollRun.branch_id.is_(None)))
    revenue = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(*revenue_filter).scalar())
    revenue += sum((record_amount(row, "total", "amount", "net_amount") for row in recognized_app_sales), Decimal("0.00"))
    # Use same SQL JSON extraction as _purchase_summary to cover all field variants
    purchases = money(_purchase_summary(db, company_id, branch_id)["total"])
    payroll = money(db.query(func.coalesce(func.sum(PayrollRun.net_total), 0)).filter(*payroll_filter).scalar())
    expenses = money(db.query(func.coalesce(func.sum(SourceTransaction.total), 0)).filter(*expense_filter).scalar())
    # app_data "expenses" collection has no branch_id column of its own
    # (AppDataRecord.branch_id does, but expenses aren't written through the
    # branch-aware save path today) — left company-wide, same as corporate/
    # assets/budget_cash/control below.
    app_expenses = app_data_payloads(db, company_id, "expenses")
    expenses += sum((record_amount(row, "total", "amount", "net_amount") for row in app_expenses), Decimal("0.00"))
    operating_expenses = expenses + payroll
    gross_profit = revenue - purchases
    net_profit = gross_profit - operating_expenses
    gross_margin = (gross_profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
    # TaxLine has no branch_id column, so the DB-sourced portion of the VAT
    # breakdown stays company-wide regardless of branch_id — only the
    # app_sales/app_purchases additions immediately below are branch-scoped.
    tax_breakdown = tax_line_breakdown_both_directions(db, company_id)
    output_breakdown = tax_breakdown["output"]
    output_taxable = output_breakdown["standard"] + output_breakdown["zero"] + output_breakdown["exempt"]
    output_taxable += sum((record_amount(row, "subtotal", "net_amount", "taxable_amount") for row in recognized_app_sales), Decimal("0.00"))
    output_vat = output_breakdown["vat"]
    output_vat += sum((record_amount(row, "vat_amount", "vat", "tax_amount") for row in recognized_app_sales), Decimal("0.00"))
    input_breakdown = tax_breakdown["input"]
    input_taxable = input_breakdown["standard"] + input_breakdown["zero"] + input_breakdown["exempt"]
    input_taxable += sum((record_amount(row, "net_amount", "subtotal", "taxable_amount") for row in app_purchases), Decimal("0.00"))
    input_vat = input_breakdown["vat"]
    input_vat += sum((record_amount(row, "tax_amount", "vat_amount", "vat") for row in app_purchases), Decimal("0.00"))
    aging_rows = receivables_aging(db, company_id, app_sales, branch_id)
    ar_total = sum(money(row["total"]) for row in aging_rows)
    overdue_total = sum(money(row["d31_60"]) + money(row["d61_90"]) + money(row["over90"]) for row in aging_rows)
    risk_score = "Low" if overdue_total == 0 else "Medium" if overdue_total < ar_total / Decimal("2") else "High"
    monthly = monthly_revenue_vat(db, company_id, app_sales, branch_id)
    result = {
        "dashboard": {
            "revenue": amount(revenue),
            "gross_margin": amount(gross_margin),
            "cash_runway_months": amount(Decimal("0.00")),
            "risk_score": risk_score,
            "risk_issues": int((1 if overdue_total else 0) + (1 if input_vat > output_vat else 0)),
            "monthly": monthly,
            "receivables": receivables_mix(aging_rows),
            "cash_forecast": [
                {"label": "30 days", "amount": amount(revenue - purchases), "tone": "ok"},
                {"label": "60 days", "amount": amount((revenue - purchases) - operating_expenses), "tone": "warn"},
                {"label": "90 days", "amount": amount(net_profit), "tone": "danger" if net_profit < 0 else "ok"},
            ],
            "ai_score": 82 if risk_score == "Low" else 68 if risk_score == "Medium" else 45,
            "ai_summary": f"Reports are generated from database records: AED {amount(revenue)} revenue, AED {amount(purchases)} purchases, and AED {amount(output_vat - input_vat)} net VAT.",
            "actions": suggested_actions(overdue_total, output_vat - input_vat, payroll),
        },
        "vat": {
            "period": monthly[-1]["period"] if monthly else "Current",
            "output": {
                # App-data sales invoices carry no VAT-treatment tag, so their amount
                # is folded into "standard" alongside standard-rated DB transactions.
                "standard_rated": amount(output_breakdown["standard"] + sum((record_amount(row, "subtotal", "net_amount", "taxable_amount") for row in recognized_app_sales), Decimal("0.00"))),
                "zero_rated": amount(output_breakdown["zero"]),
                "exempt": amount(output_breakdown["exempt"]),
                "total_supplies": amount(output_taxable),
                "output_vat": amount(output_vat),
            },
            "input": {
                "standard_rated": amount(input_breakdown["standard"] + sum((record_amount(row, "net_amount", "subtotal", "taxable_amount") for row in app_purchases), Decimal("0.00"))),
                "zero_rated": amount(input_breakdown["zero"]),
                "exempt": amount(input_breakdown["exempt"]),
                "total_purchases": amount(input_taxable),
                "input_vat": amount(input_vat),
            },
            "settlement": {
                "output_vat": amount(output_vat),
                "input_vat": amount(input_vat),
                "net_vat_payable": amount(output_vat - input_vat),
            },
            "movement": monthly,
            "readiness": {
                "trn_checks": 100,
                "vat_math": 100 if output_vat >= 0 and input_vat >= 0 else 0,
                "documents": min(100, int((count(db, Document, company_id) / max(1, invoice_count_db)) * 100)),
                "duplicates": 100,
            },
        },
        "profit_loss": {
            "revenue": amount(revenue),
            "other_income": "0.00",
            "total_revenue": amount(revenue),
            "cogs": amount(purchases),
            "gross_profit": amount(gross_profit),
            "payroll": amount(payroll),
            "other_expenses": amount(expenses),
            "total_expenses": amount(operating_expenses),
            "net_profit": amount(net_profit),
            "ytd": {
                "revenue": amount(revenue),
                "cogs": amount(purchases),
                "gross_profit": amount(gross_profit),
                "expenses": amount(operating_expenses),
                "net_profit": amount(net_profit),
            },
        },
    }
    _bs = balance_sheet_rows(db, company_id, branch_id)
    _ap_aging = ap_aging_rows(db, company_id, app_purchases, branch_id)
    ap_total = sum(money(r["total"]) for r in _ap_aging)
    _wc = working_capital_rows(db, company_id, _bs, revenue=revenue, purchases=purchases, ar_total=ar_total, ap_total=ap_total)

    # E-invoicing readiness metrics
    invoice_count_total = invoice_count_db + len(app_sales)
    app_with_trn = sum(1 for r in app_sales if str(r.get("customer_trn") or "").strip())
    trn_rate = int(app_with_trn / len(app_sales) * 100) if app_sales else 100
    with_trn_total = app_with_trn + int(invoice_count_db * trn_rate / 100)
    einv_score = min(100, int((with_trn_total / max(1, invoice_count_total)) * 70) + 20) if invoice_count_total else 0

    result.update({
        "balance_sheet": _bs,
        "trial_balance": trial_balance_rows(db, company_id, branch_id),
        "aging": aging_rows,
        "ai": {
            "forecast_confidence": 87 if invoice_count_db else 0,
            "anomalies": int((1 if overdue_total else 0) + (1 if input_vat > output_vat else 0)),
            "potential_savings": amount(operating_expenses * Decimal("0.05")),
            "collection_upside": amount(overdue_total),
            "anomalies_list": anomaly_rows(overdue_total, input_vat, output_vat, payroll),
            "report_text": report_ai_text(revenue, gross_margin, output_vat - input_vat, overdue_total, net_profit),
        },
        # These four sections are backed by tables with no branch_id column
        # at all (CorporateTaxRecord/ConsolidationRecord, FixedAssetRecord/
        # AccrualPrepaymentRecord, BudgetRecord/CashFlowForecastRecord,
        # CostCenterRecord/MonthEndCloseRecord/AuditLog) — company-wide
        # regardless of branch_id, same as the Dashboard's own documented
        # exceptions (VAT/staff/payroll). Would need a schema migration plus
        # write-side changes to make branch-aware.
        "corporate": corporate_report_rows(db, company_id, net_profit),
        "assets": asset_report_rows(db, company_id),
        "budget_cash": budget_cash_rows(db, company_id, revenue, purchases, operating_expenses, net_profit),
        "control": control_report_rows(db, company_id, revenue, purchases, net_profit),
        "general_ledger": general_ledger_rows(db, company_id, branch_id),
        "customer_ledger": customer_ledger_rows(db, company_id, app_sales, branch_id),
        "supplier_ledger": supplier_ledger_rows(db, company_id, app_purchases, branch_id),
        "ap_aging": _ap_aging,
        "revenue_intelligence": revenue_intelligence_rows(db, company_id, app_sales, monthly, branch_id),
        "working_capital": _wc,
        "ai_health": ai_health_score(revenue, gross_margin, net_profit, money(_wc["current_ratio"]), overdue_total, ar_total),
        "einvoicing": {
            "total": invoice_count_total,
            "with_trn": with_trn_total,
            "with_qr": invoice_count_total,
            "score": einv_score,
        },
    })
    return result


def tax_line_breakdown_both_directions(db: Session, company_id: str) -> dict[str, dict[str, Decimal]]:
    """Splits TaxLine taxable amounts into standard/zero-rated/exempt buckets
    per direction (output/input), by joining to TaxCode instead of assuming
    everything is standard-rated. Both directions in 2 queries total instead
    of calling a per-direction version twice (4 queries) — _build_summary is
    the only caller and always wants both."""
    empty = lambda: {"standard": Decimal("0.00"), "zero": Decimal("0.00"), "exempt": Decimal("0.00"), "vat": Decimal("0.00")}
    result = {"output": empty(), "input": empty()}
    rows = (
        db.query(TaxLine.direction, TaxCode.code, func.coalesce(func.sum(TaxLine.taxable_amount), 0), func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .join(TaxLine, TaxLine.tax_code_id == TaxCode.id)
        .filter(TaxLine.company_id == company_id)
        .group_by(TaxLine.direction, TaxCode.code)
        .all()
    )
    for direction, code, taxable, tax in rows:
        bucket = result.get(direction)
        if bucket is None:
            continue
        taxable_d = money(taxable)
        tax_d = money(tax)
        code_upper = str(code or "").upper()
        if "EXEMPT" in code_upper:
            bucket["exempt"] += taxable_d
        elif "ZERO" in code_upper:
            bucket["zero"] += taxable_d
        else:
            bucket["standard"] += taxable_d
        bucket["vat"] += tax_d
    # TaxLines without a resolved tax_code (tax_code_id is NULL) are still standard-rated by default.
    untagged_rows = (
        db.query(TaxLine.direction, func.coalesce(func.sum(TaxLine.taxable_amount), 0), func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == company_id, TaxLine.tax_code_id.is_(None))
        .group_by(TaxLine.direction)
        .all()
    )
    for direction, taxable, tax in untagged_rows:
        bucket = result.get(direction)
        if bucket is None:
            continue
        bucket["standard"] += money(taxable)
        bucket["vat"] += money(tax)
    return result


def _posted_journal_line_totals(db: Session, company_id: str, branch_id: str | None = None):
    query = (
        db.query(
            JournalLine.account_id.label("account_id"),
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(JournalEntry.company_id == company_id, JournalEntry.status == "posted")
    )
    if branch_id:
        # NULL branch_id = predates Branch Management / no branch tagged —
        # stays visible to a branch-scoped viewer rather than vanishing,
        # same rule as attendance filtering (Phase 2).
        query = query.filter((JournalEntry.branch_id == branch_id) | (JournalEntry.branch_id.is_(None)))
    return query.group_by(JournalLine.account_id).subquery()


def _opening_balance_dr_cr(opening_balance: Any, opening_balance_type: Any) -> tuple[Decimal, Decimal]:
    ob_value = money(opening_balance or 0)
    if str(opening_balance_type or "DR").upper() == "CR":
        return Decimal("0.00"), ob_value
    return ob_value, Decimal("0.00")


def trial_balance_rows(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, str]]:
    jl_totals = _posted_journal_line_totals(db, company_id, branch_id)
    rows = (
        db.query(Account.code, Account.name, Account.opening_balance, Account.opening_balance_type, jl_totals.c.debit, jl_totals.c.credit)
        .outerjoin(jl_totals, jl_totals.c.account_id == Account.id)
        .filter(Account.company_id == company_id)
        .order_by(Account.code)
        .all()
    )
    result = []
    for code, name, ob, ob_type, debit, credit in rows:
        ob_dr, ob_cr = _opening_balance_dr_cr(ob, ob_type)
        debit_total = money(debit or 0) + ob_dr
        credit_total = money(credit or 0) + ob_cr
        if not (debit_total or credit_total):
            continue
        result.append({"code": code, "name": name, "debit": amount(debit_total), "credit": amount(credit_total)})
    return result


def balance_sheet_rows(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    jl_totals = _posted_journal_line_totals(db, company_id, branch_id)
    rows = (
        db.query(Account.code, Account.name, Account.type, Account.opening_balance, Account.opening_balance_type, jl_totals.c.debit, jl_totals.c.credit)
        .outerjoin(jl_totals, jl_totals.c.account_id == Account.id)
        .filter(Account.company_id == company_id)
        .order_by(Account.code)
        .all()
    )
    sections: dict[str, list[dict[str, str]]] = {"assets": [], "liabilities": [], "equity": []}
    totals = {"assets": Decimal("0.00"), "liabilities": Decimal("0.00"), "equity": Decimal("0.00")}
    for code, name, account_type, ob, ob_type, debit, credit in rows:
        normalized = str(account_type or "").strip().lower()
        ob_dr, ob_cr = _opening_balance_dr_cr(ob, ob_type)
        debit_value = money(debit or 0) + ob_dr
        credit_value = money(credit or 0) + ob_cr
        if not (debit_value or credit_value):
            continue
        if normalized == "asset":
            balance = debit_value - credit_value
            section = "assets"
        elif normalized == "liability":
            balance = credit_value - debit_value
            section = "liabilities"
        elif normalized == "equity":
            balance = credit_value - debit_value
            section = "equity"
        else:
            continue
        sections[section].append({"code": code, "name": name, "amount": amount(balance)})
        totals[section] += balance
    total_liabilities_equity = totals["liabilities"] + totals["equity"]
    return {
        **sections,
        "totals": {
            "assets": amount(totals["assets"]),
            "liabilities": amount(totals["liabilities"]),
            "equity": amount(totals["equity"]),
            "liabilities_equity": amount(total_liabilities_equity),
            "difference": amount(totals["assets"] - total_liabilities_equity),
        },
    }


def _days_overdue(due_date_str: Any) -> int:
    """Days past due date. Negative = not yet due, 0 = current, positive = overdue."""
    try:
        if not due_date_str:
            return 0
        due = _date.fromisoformat(str(due_date_str)[:10])
        return (_date.today() - due).days
    except (ValueError, TypeError):
        return 0


def _add_to_aging_bucket(buckets: dict[str, Decimal], value: Decimal, days_overdue: int) -> None:
    if days_overdue <= 0:
        buckets["current"] += value
    elif days_overdue <= 30:
        buckets["d1_30"] += value
    elif days_overdue <= 60:
        buckets["d31_60"] += value
    elif days_overdue <= 90:
        buckets["d61_90"] += value
    else:
        buckets["over90"] += value


def receivables_aging(db: Session, company_id: str, app_sales: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, str]]:
    result: dict[str, dict[str, Decimal]] = {}

    # DB invoices — no due_date field; use created_at + 30 days as proxy
    query = db.query(Invoice.customer_name, Invoice.total, Invoice.created_at).filter(
        Invoice.company_id == company_id, Invoice.status != "paid"
    )
    if branch_id:
        # NULL branch_id = predates Branch Management — stays visible to a
        # branch-scoped viewer, same rule used throughout this file.
        query = query.filter((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    db_rows = query.all()
    for customer, total, created_at in db_rows:
        key = str(customer or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {k: Decimal("0") for k in ("current", "d1_30", "d31_60", "d61_90", "over90")})
        proxy_due = str((created_at.date() + timedelta(days=30))) if created_at else ""
        _add_to_aging_bucket(e, money(total), _days_overdue(proxy_due))

    # App sales invoices — have real due_date
    for invoice in app_sales:
        if is_paid_status(invoice.get("status")) or _is_credit_note(invoice):
            continue
        key = str(invoice.get("customer") or invoice.get("customer_name") or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {k: Decimal("0") for k in ("current", "d1_30", "d31_60", "d61_90", "over90")})
        _add_to_aging_bucket(e, record_amount(invoice, "total", "amount", "net_amount"), _days_overdue(invoice.get("due_date")))

    return sorted(
        [
            {
                "customer": k,
                "current": amount(v["current"]),
                "d1_30": amount(v["d1_30"]),
                "d31_60": amount(v["d31_60"]),
                "d61_90": amount(v["d61_90"]),
                "over90": amount(v["over90"]),
                "total": amount(sum(v.values())),
            }
            for k, v in result.items()
        ],
        key=lambda x: -money(x["total"]),
    )


def receivables_mix(rows: list[dict[str, str]]) -> dict[str, Any]:
    totals = {
        "current": sum(money(row["current"]) for row in rows),
        "d1_30": sum(money(row["d1_30"]) for row in rows),
        "d31_60": sum(money(row["d31_60"]) for row in rows),
        "d61_90": sum(money(row["d61_90"]) for row in rows),
        "over90": sum(money(row["over90"]) for row in rows),
    }
    grand = sum(totals.values()) or Decimal("1.00")
    return {
        "label": amount(sum(totals.values())),
        "buckets": [
            {"label": "Current", "amount": amount(totals["current"]), "percentage": int((totals["current"] / grand) * 100), "tone": "ok"},
            {"label": "1-30 Days", "amount": amount(totals["d1_30"]), "percentage": int((totals["d1_30"] / grand) * 100), "tone": "ok"},
            {"label": "31-60 Days", "amount": amount(totals["d31_60"]), "percentage": int((totals["d31_60"] / grand) * 100), "tone": "warn"},
            {"label": "61-90 Days", "amount": amount(totals["d61_90"] + totals["over90"]), "percentage": int(((totals["d61_90"] + totals["over90"]) / grand) * 100), "tone": "danger"},
        ],
    }


def suggested_actions(overdue_total: Decimal, net_vat: Decimal, payroll: Decimal) -> list[str]:
    actions = []
    if overdue_total:
        actions.append(f"Follow up AED {amount(overdue_total)} overdue receivables.")
    actions.append(f"Review net VAT payable AED {amount(net_vat)} before filing.")
    if payroll:
        actions.append(f"Reconcile payroll net AED {amount(payroll)} against WPS records.")
    if not actions:
        actions.append("No report exceptions found in the current database records.")
    return actions


def anomaly_rows(overdue_total: Decimal, input_vat: Decimal, output_vat: Decimal, payroll: Decimal) -> list[dict[str, str]]:
    rows = []
    if overdue_total:
        rows.append({"area": "Receivables", "signal": f"AED {amount(overdue_total)} outstanding", "impact": "Medium", "action": "Open"})
    if input_vat > output_vat:
        rows.append({"area": "VAT", "signal": "Input VAT is higher than output VAT", "impact": "Medium", "action": "Review"})
    if payroll:
        rows.append({"area": "Payroll", "signal": f"AED {amount(payroll)} payroll net included in reports", "impact": "Low", "action": "Check"})
    if not rows:
        rows.append({"area": "Reports", "signal": "No anomalies found from current DB data", "impact": "Low", "action": "View"})
    return rows


def corporate_report_rows(db: Session, company_id: str, net_profit: Decimal) -> dict[str, Any]:
    corporate_rows = db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == company_id).order_by(CorporateTaxRecord.created_at.desc()).all()
    if corporate_rows:
        latest = corporate_rows[0]
        accounting_profit = money(latest.accounting_profit)
        tax_adjustments = money(latest.tax_adjustments)
        taxable_income = money(latest.taxable_income)
        tax_due = money(latest.tax_due)
        status = latest.status
    else:
        accounting_profit = net_profit
        tax_adjustments = Decimal("0.00")
        taxable_income = max(Decimal("0.00"), accounting_profit + tax_adjustments)
        tax_due = max(Decimal("0.00"), taxable_income - Decimal("375000.00")) * Decimal("0.09")
        status = "calculated"

    consolidations = db.query(ConsolidationRecord).filter(ConsolidationRecord.company_id == company_id).order_by(ConsolidationRecord.group_name).all()
    return {
        "stats": {
            "corporate_tax": amount(tax_due),
            "taxable_income": amount(taxable_income),
            "related_party_count": 0,
            "group_entity_count": len(consolidations),
        },
        "tax_rows": [
            {"line": "Accounting Profit", "amount": amount(accounting_profit), "status": status.title()},
            {"line": "Tax Adjustments", "amount": amount(tax_adjustments), "status": "Review" if tax_adjustments else "Ready"},
            {"line": "Small Business Relief Threshold", "amount": "375000.00", "status": "Applied"},
            {"line": "Taxable Profit", "amount": amount(taxable_income), "status": "Calculated"},
            {"line": "Corporate Tax Payable", "amount": amount(tax_due), "status": "Draft"},
        ],
        "related_party_rows": [],
        "consolidation_rows": [
            {
                "entity": row.subsidiary_name,
                "currency": row.currency,
                "translated_amount": amount(money(row.translated_amount)),
                "elimination_amount": amount(money(row.elimination_amount)),
                "status": row.status,
            }
            for row in consolidations
        ],
    }


def asset_report_rows(db: Session, company_id: str) -> dict[str, Any]:
    assets = db.query(FixedAssetRecord).filter(FixedAssetRecord.company_id == company_id).order_by(FixedAssetRecord.asset_code).all()
    accruals = db.query(AccrualPrepaymentRecord).filter(AccrualPrepaymentRecord.company_id == company_id).order_by(AccrualPrepaymentRecord.created_at.desc()).all()
    return {
        "fixed_assets": [
            {
                "asset": row.asset_name,
                "category": row.category,
                "cost": amount(money(row.purchase_cost)),
                "depreciation": amount(money(row.accumulated_depreciation)),
                "book_value": amount(money(row.purchase_cost) - money(row.accumulated_depreciation)),
                "location": row.location or "-",
            }
            for row in assets
        ],
        "depreciation": [
            {
                "month": period_label(row.created_at),
                "expense": "0.00",
                "accumulated": amount(money(row.accumulated_depreciation)),
                "posting": row.status.title(),
            }
            for row in assets
        ],
        "accruals": [
            {
                "reference": row.reference,
                "type": row.record_type,
                "amount": amount(money(row.total_amount)),
                "reversal": str(row.reversal_day),
                "status": row.status,
            }
            for row in accruals
            if row.record_type.lower().startswith("accr")
        ],
        "prepayments": [
            {
                "item": row.description,
                "total": amount(money(row.total_amount)),
                "monthly": amount(money(row.monthly_amount)),
                "remaining": amount(max(Decimal("0.00"), money(row.total_amount) - money(row.monthly_amount))),
                "status": row.status,
            }
            for row in accruals
            if "prepay" in row.record_type.lower()
        ],
    }


def budget_cash_rows(db: Session, company_id: str, revenue: Decimal, purchases: Decimal, operating_expenses: Decimal, net_profit: Decimal) -> dict[str, Any]:
    budgets = db.query(BudgetRecord).filter(BudgetRecord.company_id == company_id).order_by(BudgetRecord.fiscal_year.desc()).all()
    forecasts = db.query(CashFlowForecastRecord).filter(CashFlowForecastRecord.company_id == company_id).order_by(CashFlowForecastRecord.forecast_date).all()
    budget_rows = [
        {
            "department": row.cost_center or row.account_code,
            "budget": amount(money(row.annual_budget)),
            "actual": amount(money(row.actual_amount)),
            "variance": amount(money(row.variance_amount)),
            "analysis": row.approval_status,
        }
        for row in budgets
    ]
    if not budget_rows:
        budget_rows = [
            {"department": "Revenue", "budget": amount(revenue), "actual": amount(revenue), "variance": "0.00", "analysis": "From invoices"},
            {"department": "Operating cost", "budget": amount(purchases + operating_expenses), "actual": amount(purchases + operating_expenses), "variance": "0.00", "analysis": "From sources/payroll"},
        ]
    return {
        "budget_rows": budget_rows,
        "cash_flow_rows": [
            {"section": "Operating Cash Flow", "direct": amount(net_profit), "indirect": amount(net_profit), "status": "Ready"},
            {"section": "Investing Cash Flow", "direct": "0.00", "indirect": "0.00", "status": "No entries"},
            {"section": "Financing Cash Flow", "direct": "0.00", "indirect": "0.00", "status": "No entries"},
        ],
        "forecast_rows": [
            {
                "period": row.forecast_date,
                "receipts": amount(money(row.expected_receipts)),
                "payments": amount(money(row.expected_payments)),
                "net": amount(money(row.net_cash_flow)),
                "risk": "Low" if money(row.net_cash_flow) >= 0 else "High",
            }
            for row in forecasts
        ]
        or [
            {"period": "30 Days", "receipts": amount(revenue), "payments": amount(purchases + operating_expenses), "net": amount(net_profit), "risk": "Low" if net_profit >= 0 else "High"}
        ],
    }


def control_report_rows(db: Session, company_id: str, revenue: Decimal, purchases: Decimal, net_profit: Decimal) -> dict[str, Any]:
    centers = db.query(CostCenterRecord).filter(CostCenterRecord.company_id == company_id).order_by(CostCenterRecord.code).all()
    close_rows = db.query(MonthEndCloseRecord).filter(MonthEndCloseRecord.company_id == company_id).order_by(MonthEndCloseRecord.period.desc()).all()
    audit_rows = db.query(AuditLog).filter(AuditLog.company_id == company_id).order_by(AuditLog.created_at.desc()).limit(8).all()
    audit_detail_count = db.query(func.count(AuditLogDetail.id)).filter(AuditLogDetail.company_id == company_id).scalar() or 0
    cost_rows = [
        {
            "center": row.name,
            "revenue": "0.00",
            "cost": "0.00",
            "profit": "0.00",
            "margin": "0.00%",
        }
        for row in centers
    ]
    if not cost_rows:
        margin = (net_profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
        cost_rows = [{"center": "Company total", "revenue": amount(revenue), "cost": amount(purchases), "profit": amount(net_profit), "margin": f"{amount(margin)}%"}]
    return {
        "cost_centers": cost_rows,
        "projects": [{"project": "Company total", "revenue": amount(revenue), "cost": amount(purchases), "profit": amount(net_profit), "status": "DB summary"}],
        "month_end": [
            {"checklist": row.checklist_item, "owner": row.owner or "-", "status": row.status, "evidence": "Locked" if row.locked else "Open"}
            for row in close_rows
        ],
        "audit": [
            {
                "action": row.action.replace("_", " ").title(),
                "user": row.user_id or "System",
                "reason": row.detail or "-",
                "correlation": row.record_id or f"AUD-{index + 1:04d}",
                "status": "Logged",
            }
            for index, row in enumerate(audit_rows)
        ],
        "audit_detail_count": int(audit_detail_count),
    }


def report_ai_text(revenue: Decimal, gross_margin: Decimal, net_vat: Decimal, overdue_total: Decimal, net_profit: Decimal) -> str:
    return (
        f"Reports are generated from current database records. Revenue is AED {amount(revenue)}, "
        f"gross margin is {amount(gross_margin)}%, net VAT is AED {amount(net_vat)}, "
        f"open collection upside is AED {amount(overdue_total)}, and net profit is AED {amount(net_profit)}."
    )


def general_ledger_rows(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, str]]:
    # Unlike monthly_revenue_vat()'s ~7-month cutoff (that one only ever
    # shows 6 months of trend data), a General Ledger legitimately wants a
    # fuller history — but with no bound at all, ORDER BY Account.code first
    # (not date first) meant the LIMIT below could get filled entirely by
    # one or two accounts' full history before ever reaching others, and the
    # underlying sort cost grows forever with a tenant's age regardless. A
    # 1-year floor keeps the worst case bounded while still covering what
    # anyone browsing a ledger normally needs; older entries remain in the
    # DB and other reports (trial balance, GL account balances) are
    # unaffected since they aggregate rather than list rows.
    cutoff = _date.today() - timedelta(days=365)
    rows = (
        db.query(
            Account.code,
            Account.name,
            Account.opening_balance,
            Account.opening_balance_type,
            GeneralLedgerEntry.entry_date,
            GeneralLedgerEntry.voucher_no,
            GeneralLedgerEntry.voucher_type,
            GeneralLedgerEntry.narration,
            GeneralLedgerEntry.debit,
            GeneralLedgerEntry.credit,
            GeneralLedgerEntry.balance,
            GeneralLedgerEntry.party,
        )
        .join(Account, Account.id == GeneralLedgerEntry.account_id)
        .filter(GeneralLedgerEntry.company_id == company_id, GeneralLedgerEntry.entry_date >= cutoff)
    )
    if branch_id:
        rows = rows.filter((GeneralLedgerEntry.branch_id == branch_id) | (GeneralLedgerEntry.branch_id.is_(None)))
    rows = (
        rows.order_by(Account.code, GeneralLedgerEntry.entry_date, GeneralLedgerEntry.created_at)
        .limit(2000)
        .all()
    )
    result = []
    seen_accounts: dict[str, dict] = {}
    for code, name, ob, ob_type, entry_date, voucher_no, voucher_type, narration, debit, credit, balance, party in rows:
        if code not in seen_accounts:
            ob_val = money(ob or 0)
            ob_dr = money(ob_val) if ob_type == "DR" else Decimal("0.00")
            ob_cr = money(ob_val) if ob_type == "CR" else Decimal("0.00")
            ob_bal = ob_dr - ob_cr
            seen_accounts[code] = {"name": name, "ob_bal": ob_bal}
            if ob_val:
                result.append({
                    "account_code": code,
                    "account_name": name,
                    "date": "",
                    "reference": "Opening Balance",
                    "voucher_type": "",
                    "description": "Balance brought forward",
                    "debit": amount(ob_dr),
                    "credit": amount(ob_cr),
                    "balance": amount(ob_bal),
                    "party": "",
                    "row_type": "opening",
                })
        result.append({
            "account_code": code,
            "account_name": name,
            "date": str(entry_date.date() if entry_date else ""),
            "reference": str(voucher_no or ""),
            "voucher_type": str(voucher_type or ""),
            "description": str(narration or ""),
            "debit": amount(money(debit)),
            "credit": amount(money(credit)),
            "balance": amount(money(balance)),
            "party": str(party or ""),
            "row_type": "entry",
        })
    return result


def customer_ledger_rows(db: Session, company_id: str, app_sales: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    invoice_filter = [Invoice.company_id == company_id]
    if branch_id:
        invoice_filter.append((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    # Single source of truth: Invoice table only
    for name, total, cnt in (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0), func.count(Invoice.id))
        .filter(*invoice_filter)
        .group_by(Invoice.customer_name)
        .all()
    ):
        key = str(name or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += money(total)
        e["count"] += int(cnt)
    # Include app_sales only if not already in Invoice table (check by reference)
    invoice_refs = {
        str(r[0] or "").strip()
        for r in db.query(Invoice.invoice_number).filter(*invoice_filter).all()
    }
    for row in app_sales:
        ref = str(row.get("invoice_no") or row.get("invoice_number") or row.get("reference") or "").strip()
        if ref and ref in invoice_refs:
            continue
        key = str(row.get("customer") or row.get("customer_name") or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += record_amount(row, "total", "amount", "net_amount")
        e["count"] += 1
    return sorted(
        [{"party": k, "customer": k, "total": amount(v["total"]), "transactions": v["count"]} for k, v in result.items()],
        key=lambda x: -money(x["total"])
    )[:100]


def supplier_ledger_rows(db: Session, company_id: str, app_purchases: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    st_filter = [SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill", "expense", "expenses"])]
    if branch_id:
        st_filter.append((SourceTransaction.branch_id == branch_id) | (SourceTransaction.branch_id.is_(None)))
    # Single source of truth: SourceTransaction only (purchase modules)
    for name, total, cnt in (
        db.query(SourceTransaction.party_name, func.coalesce(func.sum(SourceTransaction.total), 0), func.count(SourceTransaction.id))
        .filter(*st_filter)
        .group_by(SourceTransaction.party_name)
        .all()
    ):
        key = str(name or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += money(total)
        e["count"] += int(cnt)
    # Include app_purchases only if not already in SourceTransaction (check by reference)
    st_refs = {
        str(r[0] or "").strip()
        for r in db.query(SourceTransaction.reference).filter(*st_filter).all()
    }
    for row in app_purchases:
        ref = str(row.get("invoice_no") or row.get("reference") or "").strip()
        if ref and ref in st_refs:
            continue
        key = str(row.get("supplier") or row.get("vendor") or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += record_amount(row, "total", "amount", "net_amount")
        e["count"] += 1
    return sorted(
        [{"party": k, "supplier": k, "total": amount(v["total"]), "transactions": v["count"]} for k, v in result.items()],
        key=lambda x: -money(x["total"])
    )[:100]


def ap_aging_rows(db: Session, company_id: str, app_purchases: list[dict[str, Any]], branch_id: str | None = None) -> list[dict[str, str]]:
    result: dict[str, dict[str, Decimal]] = {}

    ap_filter = [SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]), SourceTransaction.status != "paid"]
    if branch_id:
        ap_filter.append((SourceTransaction.branch_id == branch_id) | (SourceTransaction.branch_id.is_(None)))
    # SourceTransaction — no due_date; use created_at + 30 days as proxy
    for party, total, created_at in (
        db.query(SourceTransaction.party_name, func.coalesce(func.sum(SourceTransaction.total), 0), func.max(SourceTransaction.created_at))
        .filter(*ap_filter)
        .group_by(SourceTransaction.party_name)
        .all()
    ):
        key = str(party or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {k: Decimal("0") for k in ("current", "d1_30", "d31_60", "d61_90", "over90")})
        proxy_due = str((created_at.date() + timedelta(days=30))) if created_at else ""
        _add_to_aging_bucket(e, money(total), _days_overdue(proxy_due))

    # App purchase records — have real due_date
    for row in app_purchases:
        if not is_paid_status(row.get("status")):
            key = str(row.get("supplier") or row.get("vendor") or "Unknown").strip() or "Unknown"
            e = result.setdefault(key, {k: Decimal("0") for k in ("current", "d1_30", "d31_60", "d61_90", "over90")})
            _add_to_aging_bucket(e, record_amount(row, "total", "amount", "net_amount"), _days_overdue(row.get("due_date")))

    return sorted(
        [
            {
                "supplier": k,
                "current": amount(v["current"]),
                "d1_30": amount(v["d1_30"]),
                "d31_60": amount(v["d31_60"]),
                "d61_90": amount(v["d61_90"]),
                "over90": amount(v["over90"]),
                "total": amount(sum(v.values())),
            }
            for k, v in result.items()
        ],
        key=lambda x: -money(x["total"]),
    )


def revenue_intelligence_rows(db: Session, company_id: str, app_sales: list[dict[str, Any]], monthly: list[dict[str, Any]], branch_id: str | None = None) -> dict[str, Any]:
    ri_filter = [Invoice.company_id == company_id]
    if branch_id:
        ri_filter.append((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    by_customer = (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0))
        .filter(*ri_filter)
        .group_by(Invoice.customer_name)
        .order_by(func.coalesce(func.sum(Invoice.total), 0).desc())
        .limit(10)
        .all()
    )
    customer_rows = [{"customer": name or "Unknown", "revenue": amount(money(total))} for name, total in by_customer]
    if not customer_rows:
        customer_rows = [{"customer": r.get("customer_name", r.get("customer", "Unknown")), "revenue": amount(record_amount(r, "total", "amount", "net_amount"))} for r in app_sales[:10]]
    monthly_rev = [{"period": m["period"], "revenue": m.get("sales", "0.00"), "purchases": m.get("purchases", "0.00")} for m in monthly]
    return {"by_customer": customer_rows, "monthly": monthly_rev, "growth_pct": _growth_pct(monthly)}


def _growth_pct(monthly: list[dict[str, Any]]) -> str:
    if len(monthly) < 2:
        return "0.00"
    prev = money(monthly[-2].get("sales", 0))
    curr = money(monthly[-1].get("sales", 0))
    if not prev:
        return "0.00"
    return amount(((curr - prev) / prev) * Decimal("100"))


def working_capital_rows(
    db: Session,
    company_id: str,
    balance_sheet: dict[str, Any],
    revenue: Decimal = Decimal("0"),
    purchases: Decimal = Decimal("0"),
    ar_total: Decimal = Decimal("0"),
    ap_total: Decimal = Decimal("0"),
) -> dict[str, Any]:
    assets = sum(money(r["amount"]) for r in balance_sheet.get("assets", []))
    liabilities = sum(money(r["amount"]) for r in balance_sheet.get("liabilities", []))
    current_ratio = (assets / liabilities).quantize(Decimal("0.01")) if liabilities else Decimal("0.00")
    working_capital = assets - liabilities
    receivable_days = int((ar_total / revenue * 365).quantize(Decimal("1"))) if revenue else 0
    payable_days = int((ap_total / purchases * 365).quantize(Decimal("1"))) if purchases else 0
    return {
        "current_ratio": amount(current_ratio),
        "quick_ratio": amount(current_ratio),
        "working_capital": amount(working_capital),
        "current_assets": amount(assets),
        "current_liabilities": amount(liabilities),
        "inventory_turnover": "0.00",
        "receivable_days": str(receivable_days),
        "payable_days": str(payable_days),
    }


def ai_health_score(revenue: Decimal, gross_margin: Decimal, net_profit: Decimal, current_ratio: Decimal, overdue_total: Decimal, ar_total: Decimal) -> dict[str, Any]:
    def clamp(v: int) -> int:
        return max(0, min(100, v))
    liquidity = clamp(int(current_ratio * 40))
    profitability = clamp(int(float(gross_margin)))
    debt = clamp(100 - int(float(gross_margin) * 0.5))
    cash_reserve = clamp(80 if net_profit >= 0 else 30)
    payment_behavior = clamp(100 - int((float(overdue_total) / float(ar_total) * 100) if ar_total else 0))
    overall = (liquidity + profitability + debt + cash_reserve + payment_behavior) // 5
    band = "Healthy" if overall >= 70 else "Watch" if overall >= 40 else "Risk"
    return {
        "liquidity_score": liquidity,
        "profitability_score": profitability,
        "debt_level_score": debt,
        "cash_reserve_score": cash_reserve,
        "payment_behavior_score": payment_behavior,
        "overall_score": overall,
        "band": band,
    }
