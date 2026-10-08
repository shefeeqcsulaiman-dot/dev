import hashlib
import json
import re
import threading
from contextvars import ContextVar
from datetime import date as _date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, case, event, func, or_, select, text
from sqlalchemy.exc import OperationalError as SQLAOperationalError
from sqlalchemy.exc import TimeoutError as SQLATimeoutError
from sqlalchemy.orm import Session

import app.cache as cache
from app.account_totals import account_totals as stored_account_totals
from app.doc_index import (
    PAID_STATUSES,
    money,
    normalized_ref,
    purchase_row_amount as _purchase_row_amount,
    purchase_row_net as _purchase_row_net,
    record_amount,
)
from app.config import get_settings
from app.auth_principal import Principal, require_principal_permission, resolve_active_branch
from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.routers.attendance import _company_offset, _local_today
from app.models import (
    Account,
    AccrualPrepaymentRecord,
    AppDataRecord,
    AttendanceDetail,
    AuditLog,
    AuditLogDetail,
    Branch,
    Company,
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
    LeaveRequest,
    MonthEndCloseRecord,
    Payment,
    PayrollItem,
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


# A fixed set of locks, picked by hashing the cache key: one lock per key used to be
# kept forever (every company x report x branch), growing with the number of companies.
# Two keys sharing a lock only means one waits for the other's rebuild.
_BUILD_LOCK_STRIPES = 256
_build_locks = tuple(threading.Lock() for _ in range(_BUILD_LOCK_STRIPES))


def _build_lock(key: str) -> threading.Lock:
    import zlib

    return _build_locks[zlib.crc32(key.encode()) % _BUILD_LOCK_STRIPES]


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
    if not cache.available():
        return _build_and_cache(key, data, build_fn)
    # One rebuild per key at a time; concurrent callers wait and reuse it (no cold-cache stampede).
    with _build_lock(key):
        data, is_fresh = cache.get_with_staleness(key, fresh_ttl)
        if is_fresh:
            return data
        return _build_and_cache(key, data, build_fn)


def _build_and_cache(key: str, data, build_fn) -> dict[str, Any]:
    memo_token = _payload_memo.set({})
    try:
        result = build_fn()
    except (SQLATimeoutError, SQLAOperationalError):
        if data is not None:
            stale = dict(data)
            stale["stale"] = True
            return stale
        raise
    finally:
        _payload_memo.reset(memo_token)
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


def _pending_appdata_count(db: Session, company_id: str, collection: str) -> int:
    """Overtime requests and attendance corrections are AppDataRecord-backed
    (no dedicated SQL table, unlike LeaveRequest), so "pending" isn't a
    column to filter on in SQL -- has to be read out of each row's JSON
    payload. Same "small admin-managed list" scale assumption already made
    elsewhere for these exact collections (see loans/jobRequisitions'
    own comment), and this dashboard build is cached 60s (_cached_or_build),
    so one Python-side scan per cache period is cheap enough."""
    rows = db.query(AppDataRecord.payload).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection == collection,
    ).all()
    pending = 0
    for (payload,) in rows:
        try:
            data = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            continue
        if str(data.get("status") or "").strip().lower() == "pending":
            pending += 1
    return pending


def _hr_dashboard_snapshot(db: Session, company_id: str, app_counts: dict[str, int]) -> dict[str, Any]:
    """Workforce snapshot for the main (non-HRMS-portal) Dashboard's own
    "Branch Performance"-style card -- same 6 figures as hrms.html's own KPI
    row (refreshHrmsKpis()), computed server-side here since index.html never
    loads the HR AppDataRecord tables (#employee-tbody etc.) that function
    reads from the DOM. Deliberately real queries, not the dashboard's other
    HR-ish fields (staff_present/staff_today above) which are long-standing
    employee_count-as-present-count placeholders -- see this endpoint's own
    payroll_net/staff_today fields for that pre-existing shortcut, untouched
    here since fixing those is a separate concern."""
    today = _local_today(_company_offset(db, company_id)).isoformat()
    active_employee_count = int(
        db.query(func.count(Employee.id))
        .filter(Employee.company_id == company_id, Employee.status == "active")
        .scalar() or 0
    )
    present_today = int(
        db.query(func.count(func.distinct(AttendanceDetail.employee_id)))
        .join(Employee, Employee.employee_no == AttendanceDetail.employee_id)
        .filter(
            AttendanceDetail.company_id == company_id,
            Employee.company_id == company_id,
            Employee.status == "active",
            AttendanceDetail.work_date == today,
            AttendanceDetail.clock_in_1.isnot(None),
        )
        .scalar() or 0
    )
    on_leave_today = int(
        db.query(func.count(LeaveRequest.id))
        .filter(
            LeaveRequest.company_id == company_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        )
        .scalar() or 0
    )
    pending_leave = int(
        db.query(func.count(LeaveRequest.id))
        .filter(LeaveRequest.company_id == company_id, LeaveRequest.status == "pending")
        .scalar() or 0
    )
    current_month = today[:7]
    net_payroll_this_month = money(
        db.query(func.coalesce(func.sum(PayrollRun.net_total), 0))
        .filter(PayrollRun.company_id == company_id, PayrollRun.period == current_month)
        .scalar()
    )
    pending_ot = _pending_appdata_count(db, company_id, "overtimeRequests")
    pending_corrections = _pending_appdata_count(db, company_id, "attendanceCorrections")
    return {
        "employee_count": active_employee_count,
        "present_today": present_today,
        "on_leave_today": on_leave_today,
        "pending_approvals": pending_leave + pending_ot + pending_corrections,
        "net_payroll_this_month": amount(net_payroll_this_month),
        # jobRequisitions has no "open" vs "filled" status split in the UI
        # either (hrms.html's own "Open Positions" tile is the same raw
        # count, see refreshHrmsKpis()'s openRecs) -- matched here rather
        # than inventing a stricter definition this endpoint alone enforces.
        "open_positions": app_counts.get("jobRequisitions", 0),
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
    # Only recognized-revenue (issued/paid) or credit-note app_sales rows —
    # _build_summary() below already excludes drafts from this same VAT
    # figure; this reimplementation didn't, so a draft app-data sales row's
    # VAT was counted here but not there, and the Dashboard's "Net VAT
    # Payable" tile could disagree with the VAT Report tab for identical
    # underlying data.
    output_vat += sum(
        (record_amount(row, "vat_amount", "vat", "tax_amount") for row in app_sales if _is_recognized_revenue_status(row.get("status")) or _is_credit_note(row)),
        Decimal("0.00"),
    )
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
    pur_summary["cost_of_sales"] = amount(_cost_of_sales(db, company_id, branch_id, money(pur_summary["net"])))
    hr_snapshot = _hr_dashboard_snapshot(db, company_id, app_counts)
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
        "hr_snapshot": hr_snapshot,
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


# Per-request memo for one report/dashboard build (see _cached_or_build), so figures two
# parts of a build both need are computed once.
_payload_memo: ContextVar[dict | None] = ContextVar("report_payload_memo", default=None)


def _branch_row_included(row_branch_id: str | None, branch_id: str | None) -> bool:
    """Same rule _posted_journal_line_totals() uses for JournalEntry.branch_id:
    an unscoped caller (branch_id=None) sees everything; a branch-scoped
    caller sees its own branch's rows PLUS any row with no branch_id at all
    (predates Branch Management, or was saved by an admin/User principal
    rather than a branch-scoped login) rather than losing
    that data entirely."""
    return branch_id is None or row_branch_id == branch_id or row_branch_id is None


def app_sales_invoice_records(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    existing_refs = {
        normalized_ref(value)
        for (value,) in db.query(Invoice.invoice_number).filter(Invoice.company_id == company_id).all()
        if normalized_ref(value)
    }
    # Every saved app-data invoice (bar negative credit notes) also posts a real Invoice
    # row with the same number, so nearly all of them are dropped below. Skip those in SQL
    # instead of parsing every invoice's JSON on each dashboard/report request: a row is
    # left out here only when the Python check would certainly drop it too (its stored
    # key, which is its invoice_no, matches a posted invoice number and invoice_no is a
    # non-empty string). Anything less certain still goes through the check below.
    posted_numbers = select(func.lower(func.trim(Invoice.invoice_number))).where(Invoice.company_id == company_id)
    rows = (
        db.query(AppDataRecord.payload, AppDataRecord.branch_id)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "salesInvoices",
            ~and_(
                func.lower(func.trim(AppDataRecord.record_key)).in_(posted_numbers),
                AppDataRecord.payload.like('%"invoice_no": "_%'),
            ),
        )
        .all()
    )
    records = []
    for payload, row_branch_id in rows:
        try:
            row = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or not _branch_row_included(row_branch_id, branch_id):
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
    # Rows whose posting reference (fig_ref, stamped on save with the formula below)
    # matches a posted input TaxLine are left out in SQL instead of decoding every
    # purchase and bill; the Python check below still runs on what is left.
    posted_refs = (
        select(func.lower(func.trim(SourceTransaction.reference)))
        .join(TaxLine, TaxLine.source_id == SourceTransaction.id)
        .where(
            SourceTransaction.company_id == company_id,
            SourceTransaction.module.in_(["purchase", "purchase_bill"]),
            TaxLine.direction == "input",
        )
    )
    query = db.query(AppDataRecord.collection, AppDataRecord.payload, AppDataRecord.branch_id).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection.in_(("purchaseRecords", "bills")),
        or_(AppDataRecord.fig_ref.is_(None), AppDataRecord.fig_ref.notin_(posted_refs)),
    )
    if branch_id:
        query = query.filter(or_(AppDataRecord.branch_id == branch_id, AppDataRecord.branch_id.is_(None)))
    records = []
    for collection, payload, _row_branch_id in query.order_by(AppDataRecord.collection.desc(), AppDataRecord.created_at, AppDataRecord.id).all():
        try:
            row = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        if collection == "purchaseRecords":
            # Mirrors app_data.py's sync_domain_model() reference formula for
            # purchaseRecords, so a row with no ref/invoice_no still dedupes
            # correctly against the "PURCHASE-{id}" fallback reference its own
            # posted TaxLine was created under.
            ref = normalized_ref(
                row.get("ref") or row.get("invoice_no")
                or (f"PURCHASE-{row['id']}" if row.get("id") else None)
            )
        else:
            # Mirrors app_data.py's sync_domain_model() reference formula for bills.
            ref = normalized_ref(row.get("bill_no") or (f"BILL-{row['id']}" if row.get("id") else None))
        if ref and ref in existing_refs:
            continue
        records.append(row)
    return records


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
    # Just the three columns used (not whole ORM objects: building those was most of this
    # function's time on the dashboard).
    invoice_query = db.query(Invoice.created_at, Invoice.total, Invoice.vat).filter(Invoice.company_id == company_id, Invoice.created_at >= cutoff)
    if branch_id:
        invoice_query = invoice_query.filter((Invoice.branch_id == branch_id) | (Invoice.branch_id.is_(None)))
    for created_at, total, vat in invoice_query.all():
        item = periods.setdefault(period_label(created_at), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["sales"] += money(total)
        item["output_vat"] += money(vat)
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
    purchases = db.query(SourceTransaction.created_at, SourceTransaction.total, SourceTransaction.vat).filter(*purchase_filters).all()
    for created_at, total, vat in purchases:
        item = periods.setdefault(period_label(created_at), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["purchases"] += money(total)
        item["input_vat"] += money(vat)
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


def _branch_filter(query, branch_id: str | None):
    """_branch_row_included() as SQL: a branch sees its own rows plus rows with no branch."""
    if branch_id:
        query = query.filter(or_(AppDataRecord.branch_id == branch_id, AppDataRecord.branch_id.is_(None)))
    return query


def _purchase_summary(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    """Purchases and bills: totals, net and paid/pending, summed in SQL from the
    figures stamped on each record (doc_index.doc_figures())."""
    query = db.query(
        AppDataRecord.fig_status,
        func.count(AppDataRecord.id),
        func.coalesce(func.sum(AppDataRecord.fig_gross), 0),
        func.coalesce(func.sum(AppDataRecord.fig_net), 0),
        func.coalesce(func.sum(AppDataRecord.fig_paid), 0),
        # A paid record counts its recorded paid amount, or its total when none is recorded.
        func.coalesce(func.sum(case((func.coalesce(AppDataRecord.fig_paid, 0) != 0, AppDataRecord.fig_paid), else_=AppDataRecord.fig_gross)), 0),
    ).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(("purchaseRecords", "bills")))
    total = Decimal("0")
    net_total = Decimal("0")
    paid_amount = Decimal("0")
    paid_count = 0
    pending_count = 0
    total_count = 0
    for status, n, gross, net, paid_field, paid_or_gross in _branch_filter(query, branch_id).group_by(AppDataRecord.fig_status).all():
        n = int(n or 0)
        total_count += n
        total += money(gross)
        net_total += money(net)
        if normalized_ref(status) in PAID_STATUSES:
            paid_count += n
            paid_amount += money(paid_or_gross)
        else:
            pending_count += n
            paid_amount += money(paid_field)

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


_INVOICE_STATUS_KEY = {"paid": "paid", "issued": "pending", "pending": "pending", "overdue": "overdue", "cancelled": "cancelled", "draft": "draft"}


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
    # "cancelled" used to be merged into the "overdue" bucket, which is
    # actively misleading — a voided invoice isn't awaiting collection, it
    # was never going to be collected. It gets its own bucket, and (like
    # draft) is excluded from the overall total — it was never a real sale.
    buckets: dict[str, dict[str, Any]] = {k: {"count": 0, "amount": Decimal("0.00")} for k in ("paid", "pending", "overdue", "draft", "cancelled")}
    total_count = 0
    total_amount = Decimal("0.00")
    for status, row_count, row_total, row_subtotal in rows:
        status_norm = normalized_ref(status)
        row_count = int(row_count or 0)
        key = _INVOICE_STATUS_KEY.get(status_norm)
        if key:
            buckets[key]["count"] += row_count
            buckets[key]["amount"] += money(row_total)
        if status_norm not in {"draft", "cancelled"}:
            total_count += row_count
            total_amount += money(row_subtotal)
    for invoice in app_sales:
        status = normalized_ref(invoice.get("status"))
        if status not in {"draft", "cancelled"}:
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

    # Grouped by branch and status in SQL from the stamped figures (doc_index.doc_figures());
    # the status rules are applied per group below.
    sales = (
        db.query(
            AppDataRecord.branch_id, AppDataRecord.fig_status, func.count(AppDataRecord.id),
            func.coalesce(func.sum(AppDataRecord.fig_net), 0), func.coalesce(func.sum(AppDataRecord.fig_gross), 0),
        )
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "salesInvoices")
        .group_by(AppDataRecord.branch_id, AppDataRecord.fig_status)
        .all()
    )
    for branch_id, status, n, net, gross in sales:
        status = normalized_ref(status)
        n = int(n or 0)
        b = bucket(branch_id)
        if status != "draft":
            b["revenue"] += money(net)
        key = _INVOICE_STATUS_KEY.get(status) or ("pending" if status in {"ready", "sent", "unpaid"} else None)
        if key == "paid":
            b["paid_count"] += n
            b["paid_amount"] += money(gross)
        elif key in ("pending", "overdue"):
            b["pending_count"] += n
            b["pending_amount"] += money(gross)

    purchases = (
        db.query(AppDataRecord.branch_id, func.coalesce(func.sum(AppDataRecord.fig_net), 0))
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(("purchaseRecords", "bills")))
        .group_by(AppDataRecord.branch_id)
        .all()
    )
    for branch_id, net in purchases:
        bucket(branch_id)["purchases"] += money(net)

    # Every real Branch must appear in the response even with zero activity
    # so far — otherwise a newly created (or simply quiet) branch never
    # shows up in the Branch Performance card at all, and head office has no
    # "View as" row to click to view its (empty) dashboard. Without this,
    # `bucket()` above is only ever called for branch_ids that already have
    # at least one salesInvoices/purchaseRecords/bills row, silently
    # dropping every branch with no transactions yet from `branches` below
    # despite `has_branches` correctly reporting True.
    for b in branches:
        bucket(b.id)

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
    result = _cached_or_build(cache_key, 120, _build)
    if principal.is_dept_scoped:
        # The summary's audit trail stores the raw record of every action
        # company-wide (other departments' employees, salaries, ...), so a
        # department-scoped login doesn't get it. Copies only -- `result` is
        # the shared cached object and must never be mutated.
        result = {**result, "control": {**(result.get("control") or {}), "audit": []}}
    return result


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


def _ebitda_breakdown(db: Session, company_id: str, net_profit: Decimal) -> dict[str, Any]:
    """Real EBITDA add-backs, not net profit relabeled (the previous "EBITDA
    Est." tile literally set itself to net_profit -- see
    renderProfitabilityAnalytics() in app.js before this). Interest and
    Depreciation/Amortization aren't tracked as their own concept anywhere in
    this app: there's no dedicated account category for them, and fixed-asset
    depreciation (FixedAssetRecord.accumulated_depreciation) is never posted
    to the GL as an expense at all (accounting_posting.py has no posting path
    for it). Inferred here instead from GL activity against any expense
    account whose NAME contains "interest"/"depreciation"/"amortization" —
    correct for a company that names its accounts conventionally, AED 0 add-
    back (not a guess) for one that hasn't set such accounts up at all.
    Corporate tax is the one add-back that's genuinely reliable: the seeded
    chart of accounts always creates code "5100" for it (company_defaults.py)."""
    accounts = db.query(Account).filter(Account.company_id == company_id, Account.is_active.is_(True)).all()

    def _sum_debits(account_ids: list[str]) -> Decimal:
        if not account_ids:
            return Decimal("0.00")
        return money(
            db.query(func.coalesce(func.sum(GeneralLedgerEntry.debit - GeneralLedgerEntry.credit), 0))
            .filter(GeneralLedgerEntry.company_id == company_id, GeneralLedgerEntry.account_id.in_(account_ids))
            .scalar()
        )

    tax_ids = [a.id for a in accounts if a.code == "5100" or "corporate tax" in a.name.lower()]
    interest_ids = [a.id for a in accounts if "interest" in a.name.lower()]
    da_ids = [
        a.id for a in accounts
        if "depreciation" in a.name.lower() or "amortization" in a.name.lower() or "amortisation" in a.name.lower()
    ]
    tax = _sum_debits(tax_ids)
    interest = _sum_debits(interest_ids)
    depreciation_amortization = _sum_debits(da_ids)
    ebitda = money(net_profit + tax + interest + depreciation_amortization)
    return {
        "net_profit": amount(net_profit),
        "tax_addback": amount(tax),
        "interest_addback": amount(interest),
        "depreciation_amortization_addback": amount(depreciation_amortization),
        "ebitda": amount(ebitda),
        "addbacks_are_estimates": not (tax_ids or interest_ids or da_ids),
    }


# Revenue-side vs expense-side Account.type values, matching the seeded
# chart of accounts (company_defaults.py: "sales" is the only revenue type;
# "purchase"/"direct expense"/"indirect expense" are the three expense types).
_REVENUE_ACCOUNT_TYPES = {"sales"}
_EXPENSE_ACCOUNT_TYPES = {"purchase", "direct expense", "indirect expense"}


def _cost_center_breakdown(db: Session, company_id: str, branch_id: str | None) -> list[dict[str, Any]]:
    """Real GL-based P&L per cost centre. cost_center (GeneralLedgerEntry.
    cost_center) is populated only when a transaction goes through
    Accounting > Vouchers with a cost centre selected — auto-posted Sales/
    Purchase/Receipt/Payment entries never set it (create_gl_entries_from_
    journal() is never called with cost_center from accounting_posting.py's
    own auto-posting paths, only from the manual voucher path in
    routers/accounting.py). Entries with no cost centre are grouped under
    "Unassigned" rather than dropped, so totals here still reconcile to the
    real GL and a company that hasn't adopted cost centres yet still gets a
    truthful (if unsegmented) answer instead of an empty report."""
    memo = _payload_memo.get()
    memo_key = ("cost_centers", company_id, branch_id)
    if memo is not None and memo_key in memo:
        return [dict(r) for r in memo[memo_key]]
    accounts_by_id = {a.id: a for a in db.query(Account).filter(Account.company_id == company_id).all()}
    # Summed per cost centre and account in SQL rather than loading every GL line.
    query = db.query(
        GeneralLedgerEntry.cost_center, GeneralLedgerEntry.account_id,
        func.coalesce(func.sum(GeneralLedgerEntry.debit), 0), func.coalesce(func.sum(GeneralLedgerEntry.credit), 0),
    ).filter(GeneralLedgerEntry.company_id == company_id)
    if branch_id:
        query = query.filter((GeneralLedgerEntry.branch_id == branch_id) | (GeneralLedgerEntry.branch_id.is_(None)))
    buckets: dict[str, dict[str, Decimal]] = {}
    for cost_center, account_id, debit, credit in query.group_by(GeneralLedgerEntry.cost_center, GeneralLedgerEntry.account_id).all():
        account = accounts_by_id.get(account_id)
        if not account or account.is_group:
            continue
        key = (cost_center or "").strip() or "Unassigned"
        bucket = buckets.setdefault(key, {"revenue": Decimal("0.00"), "expense": Decimal("0.00")})
        if account.type in _REVENUE_ACCOUNT_TYPES:
            bucket["revenue"] += money(credit) - money(debit)
        elif account.type in _EXPENSE_ACCOUNT_TYPES:
            bucket["expense"] += money(debit) - money(credit)
    # Company-wide totals, for each center's SHARE of total revenue/expense
    # alongside its own margin — margin_pct alone doesn't tell a reader
    # whether a high-margin center is a small side operation or the bulk of
    # the business.
    total_revenue = sum((v["revenue"] for v in buckets.values()), Decimal("0.00"))
    total_expense = sum((v["expense"] for v in buckets.values()), Decimal("0.00"))
    result = []
    for key, vals in buckets.items():
        revenue, expense = vals["revenue"], vals["expense"]
        profit = revenue - expense
        margin = (profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
        result.append({
            "cost_center": key,
            "revenue": amount(revenue),
            "expense": amount(expense),
            "profit": amount(profit),
            "margin_pct": amount(margin),
            "revenue_pct_of_total": amount((revenue / total_revenue * Decimal("100")).quantize(Decimal("0.01")) if total_revenue else Decimal("0.00")),
            "expense_pct_of_total": amount((expense / total_expense * Decimal("100")).quantize(Decimal("0.01")) if total_expense else Decimal("0.00")),
        })
    result.sort(key=lambda r: Decimal(r["profit"]), reverse=True)
    if memo is not None:
        memo[memo_key] = [dict(r) for r in result]
    return result


def _department_payroll_breakdown(db: Session, company_id: str) -> list[dict[str, Any]]:
    """Payroll cost by department — the one real, trackable per-department
    financial figure in this system. Employee.department exists, but no
    Invoice/Bill/Expense record carries a department at all (grep-confirmed;
    only Employee and a couple of HR/approval-matrix tables do), so a full
    revenue/profit-by-department breakdown isn't computable from real data.
    Deliberately labeled "payroll cost", never "profit" or "P&L", so this
    doesn't imply precision the underlying data doesn't have."""
    rows = (
        db.query(Employee.department, func.coalesce(func.sum(PayrollItem.net_pay), 0))
        .join(PayrollItem, PayrollItem.employee_id == Employee.id)
        .join(PayrollRun, PayrollRun.id == PayrollItem.run_id)
        .filter(Employee.company_id == company_id, PayrollRun.company_id == company_id)
        .group_by(Employee.department)
        .all()
    )
    priced = [(dept or "Unassigned", money(cost)) for dept, cost in rows]
    total = sum((cost for _, cost in priced), Decimal("0.00")) or Decimal("1.00")
    priced.sort(key=lambda r: r[1], reverse=True)
    # Active headcount per department — separate from the payroll-cost query
    # above (an INNER JOIN through PayrollItem, so it only ever sees
    # employees who've actually appeared in a run) so a department with
    # active staff who haven't been through payroll yet still gets a real
    # headcount instead of silently missing from this breakdown.
    headcount_rows = (
        db.query(Employee.department, func.count(Employee.id))
        .filter(Employee.company_id == company_id, Employee.status == "active")
        .group_by(Employee.department)
        .all()
    )
    headcount_by_dept = {(dept or "Unassigned"): int(n) for dept, n in headcount_rows}
    return [
        {
            "department": dept,
            "payroll_cost": amount(cost),
            "pct_of_total_payroll": amount((cost / total * Decimal("100")).quantize(Decimal("0.01"))),
            "headcount": headcount_by_dept.get(dept, 0),
            "cost_per_employee": amount(
                (cost / headcount_by_dept[dept]).quantize(Decimal("0.01"))
                if headcount_by_dept.get(dept) else Decimal("0.00")
            ),
        }
        for dept, cost in priced
    ]


def _profitability_summary_text(
    cost_centers: list[dict[str, Any]], departments: list[dict[str, Any]],
    business_units: list[dict[str, Any]], ebitda_margin_pct: Decimal, gross_margin_pct: Decimal,
) -> str:
    """Short management-readable highlights, in the same
    generated-from-database-records style as report_ai_text() elsewhere in
    this file. Only ever states what the underlying breakdowns already
    computed — no separate estimate or trend of its own (there's no stored
    per-cost-centre/department history to compute a real trend from)."""
    parts: list[str] = []
    real_centers = [c for c in cost_centers if c["cost_center"] != "Unassigned"]
    if real_centers:
        best = max(real_centers, key=lambda c: Decimal(c["profit"]))
        parts.append(f"{best['cost_center']} is the strongest cost centre at AED {best['profit']} profit ({best['margin_pct']}% margin)")
        worst = min(real_centers, key=lambda c: Decimal(c["profit"]))
        if Decimal(worst["profit"]) < 0 and worst["cost_center"] != best["cost_center"]:
            parts.append(f"{worst['cost_center']} is running at a loss (AED {worst['profit']})")
    elif cost_centers:
        parts.append("no General Ledger activity is tagged to a named cost centre yet — all GL activity is Unassigned")
    real_depts = [d for d in departments if d["department"] != "Unassigned"]
    if real_depts:
        heaviest = max(real_depts, key=lambda d: Decimal(d["payroll_cost"]))
        parts.append(f"{heaviest['department']} carries the largest payroll cost (AED {heaviest['payroll_cost']}, {heaviest['pct_of_total_payroll']}% of total payroll across {heaviest['headcount']} employee{'s' if heaviest['headcount']!=1 else ''})")
    if len(business_units) > 1:
        best_bu = max(business_units, key=lambda b: Decimal(b["profit"]))
        worst_bu = min(business_units, key=lambda b: Decimal(b["profit"]))
        if best_bu["name"] != worst_bu["name"]:
            parts.append(f"{best_bu['name']} is the top-performing business unit ({best_bu['margin_pct']}% margin) versus {worst_bu['name']} ({worst_bu['margin_pct']}%)")
    parts.append(f"EBITDA margin is {amount(ebitda_margin_pct)}% against a {amount(gross_margin_pct)}% gross margin")
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def _profitability_analysis(
    db: Session, company_id: str, branch_id: str | None, net_profit: Decimal, gross_profit: Decimal, revenue: Decimal,
) -> dict[str, Any]:
    """Cost-centre / department / business-unit / EBITDA / gross-margin
    profitability analysis for management — feeds the Profitability
    Analytics report tab and the CFO Recommendations card. Business unit
    reuses _build_branch_performance()'s existing real per-branch revenue/
    purchases/gross-profit/margin computation rather than rebuilding it."""
    branch_perf = _build_branch_performance(db, company_id)
    business_units = list(branch_perf["branches"])
    if branch_perf["unassigned"]:
        business_units.append(branch_perf["unassigned"])
    gross_margin_pct = (gross_profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
    ebitda = _ebitda_breakdown(db, company_id, net_profit)
    ebitda_margin_pct = (
        (Decimal(ebitda["ebitda"]) / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
    )
    ebitda["margin_pct"] = amount(ebitda_margin_pct)
    cost_centers = _cost_center_breakdown(db, company_id, branch_id)
    departments = _department_payroll_breakdown(db, company_id)
    return {
        "ebitda": ebitda,
        "gross_margin_pct": amount(gross_margin_pct),
        "cost_centers": cost_centers,
        "departments": departments,
        "business_units": business_units,
        "summary_text": _profitability_summary_text(cost_centers, departments, business_units, ebitda_margin_pct, gross_margin_pct),
    }


def _cost_of_sales(db: Session, company_id: str, branch_id: str | None, purchases_net: Decimal) -> Decimal:
    """Purchases less the increase in 1200 Inventory. Under perpetual inventory stock purchases sit
    in Inventory until sold, so this equals Cost of Goods Sold; periodic companies expense purchases."""
    row = db.query(Company.stock_mode, Company.inventory_accounting).filter(Company.id == company_id).first()
    if not row or row[0] == "without_stock" or (row[1] or "periodic") != "perpetual":
        return purchases_net
    totals = posted_account_totals(db, company_id, branch_id)
    inventory_ids = [aid for (aid,) in db.query(Account.id).filter(Account.company_id == company_id, Account.code == "1200")]
    movement = sum((totals.get(aid, (Decimal("0"), Decimal("0")))[0] - totals.get(aid, (Decimal("0"), Decimal("0")))[1]
                    for aid in inventory_ids), Decimal("0"))
    return money(purchases_net - money(movement))


def _manual_journal_expenses(db: Session, company_id: str, branch_id: str | None = None) -> Decimal:
    """Expense-account debits from hand-posted vouchers, which no other P&L
    source sees. Payroll-run journals (PAY-JE-...) are excluded because
    payroll is already counted from PayrollRun."""
    query = (
        db.query(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .join(Account, Account.id == JournalLine.account_id)
        .filter(
            JournalEntry.company_id == company_id,
            JournalEntry.status == "posted",
            JournalEntry.source_module == "voucher",
            Account.type.in_(list(_EXPENSE_ACCOUNT_TYPES)),
            ~JournalEntry.entry_number.like("%PAY-JE%"),
        )
    )
    if branch_id:
        query = query.filter((JournalEntry.branch_id == branch_id) | (JournalEntry.branch_id.is_(None)))
    return money(query.scalar())


def _localize_currency_text(value: Any, db: Session, company_id: str) -> Any:
    """Narrative text is built with a literal "AED"; swap in the company's
    currency (numbers themselves are already currency-agnostic)."""
    currency = (db.query(Company.currency).filter(Company.id == company_id).scalar() or "AED").strip() or "AED"
    if currency == "AED":
        return value

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return re.sub(r"\bAED\b", currency, node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        return node

    return walk(value)


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
    # Ex-VAT throughout: VAT is a pass-through liability, not revenue/cost.
    revenue = money(db.query(func.coalesce(func.sum(Invoice.subtotal), 0)).filter(*revenue_filter).scalar())
    revenue += sum((record_amount(row, "subtotal", "net_amount", "amount") for row in recognized_app_sales), Decimal("0.00"))
    purchases = money(_purchase_summary(db, company_id, branch_id)["net"])
    # Gross, not net — a loan/advance deduction is a balance-sheet recovery
    # on the employee's own liability, not a reduction in what the company
    # actually spent on payroll. Summing net_total here understated payroll
    # expense (and so overstated net profit) by exactly the deduction
    # amount. gross_total is already computed and stored by
    # generate_payroll(), just never read by anything until now.
    payroll = money(db.query(func.coalesce(func.sum(PayrollRun.gross_total), 0)).filter(*payroll_filter).scalar())
    expenses = money(db.query(func.coalesce(func.sum(SourceTransaction.subtotal), 0)).filter(*expense_filter).scalar())
    expenses += _manual_journal_expenses(db, company_id, branch_id)
    # app_data "expenses" collection has no branch_id column of its own
    # (AppDataRecord.branch_id does, but expenses aren't written through the
    # branch-aware save path today) — left company-wide, same as corporate/
    # assets/budget_cash/control below.
    expenses += money(
        db.query(func.coalesce(func.sum(AppDataRecord.fig_net), 0))
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "expenses")
        .scalar()
    )
    operating_expenses = expenses + payroll
    cost_of_sales = _cost_of_sales(db, company_id, branch_id, purchases)
    gross_profit = revenue - cost_of_sales
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
    _bs = balance_sheet_rows(db, company_id, branch_id)
    _ap_aging = ap_aging_rows(db, company_id, app_purchases, branch_id)
    ap_total = sum(money(r["total"]) for r in _ap_aging)
    _wc = working_capital_rows(db, company_id, _bs, revenue=revenue, purchases=purchases, ar_total=ar_total, ap_total=ap_total)
    working_capital_value = Decimal(_wc["working_capital"])
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
            "actions": suggested_actions(
                overdue_total, output_vat - input_vat, payroll,
                net_profit=net_profit, gross_margin=gross_margin, revenue=revenue,
                ap_total=ap_total, ar_total=ar_total, working_capital=working_capital_value,
            ),
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
            "cogs": amount(cost_of_sales),
            "gross_profit": amount(gross_profit),
            "payroll": amount(payroll),
            "other_expenses": amount(expenses),
            "total_expenses": amount(operating_expenses),
            "net_profit": amount(net_profit),
            "ytd": {
                "revenue": amount(revenue),
                "cogs": amount(cost_of_sales),
                "gross_profit": amount(gross_profit),
                "expenses": amount(operating_expenses),
                "net_profit": amount(net_profit),
            },
        },
        "profitability_analysis": _profitability_analysis(db, company_id, branch_id, net_profit, gross_profit, revenue),
    }

    # E-invoicing readiness metrics
    invoice_count_total = invoice_count_db + len(app_sales)
    app_with_trn = sum(1 for r in app_sales if str(r.get("customer_trn") or "").strip())
    trn_rate = int(app_with_trn / len(app_sales) * 100) if app_sales else 100
    with_trn_total = app_with_trn + int(invoice_count_db * trn_rate / 100)
    einv_score = min(100, int((with_trn_total / max(1, invoice_count_total)) * 70) + 20) if invoice_count_total else 0

    _ai_anomalies = anomaly_rows(
        overdue_total, input_vat, output_vat, payroll,
        net_profit=net_profit, gross_margin=gross_margin, revenue=revenue,
        ap_total=ap_total, ar_total=ar_total, working_capital=working_capital_value,
    )

    result.update({
        "balance_sheet": _bs,
        "trial_balance": trial_balance_rows(db, company_id, branch_id),
        "aging": aging_rows,
        "ai": {
            "forecast_confidence": 87 if invoice_count_db else 0,
            "anomalies": len([r for r in _ai_anomalies if r["area"] != "Reports"]),
            "potential_savings": amount(operating_expenses * Decimal("0.05")),
            "collection_upside": amount(overdue_total),
            "anomalies_list": _ai_anomalies,
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
    return _localize_currency_text(result, db, company_id)


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


_ACCOUNT_TOTALS_KEY = "reports_posted_account_totals"


def posted_account_totals(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, tuple[Decimal, Decimal]]:
    """{account_id: (debit, credit)} over every posted journal line, computed once per
    session and shared by the trial balance, balance sheet and cost of sales -- one
    summary request used to add up the same journal lines four times (the PostgreSQL
    load test put that at over half of /reports/summary's database time). The memo is
    dropped whenever the session flushes, commits or rolls back (see below), so a request
    that writes and then reports never reads stale totals."""
    memo = db.info.setdefault(_ACCOUNT_TOTALS_KEY, {})
    key = (company_id, branch_id)
    if key not in memo:
        if get_settings().report_totals_source == "live":
            totals = _posted_journal_line_totals(db, company_id, branch_id)
            rows = db.query(totals.c.account_id, totals.c.debit, totals.c.credit).all()
        else:
            # Pre-calculated per account and month (app/account_totals.py).
            rows = [(a, d, c) for a, (d, c) in stored_account_totals(db, company_id, branch_id).items()]
        memo[key] = {account_id: (money(debit or 0), money(credit or 0)) for account_id, debit, credit in rows}
    return memo[key]


@event.listens_for(Session, "after_flush")
@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_soft_rollback")
def _forget_account_totals(session: Session, *_args) -> None:
    session.info.pop(_ACCOUNT_TOTALS_KEY, None)


def _opening_balance_dr_cr(opening_balance: Any, opening_balance_type: Any) -> tuple[Decimal, Decimal]:
    ob_value = money(opening_balance or 0)
    if str(opening_balance_type or "DR").upper() == "CR":
        return Decimal("0.00"), ob_value
    return ob_value, Decimal("0.00")


def trial_balance_rows(db: Session, company_id: str, branch_id: str | None = None) -> list[dict[str, str]]:
    totals = posted_account_totals(db, company_id, branch_id)
    rows = (
        db.query(Account.id, Account.code, Account.name, Account.opening_balance, Account.opening_balance_type)
        .filter(Account.company_id == company_id)
        .order_by(Account.code)
        .all()
    )
    result = []
    for account_id, code, name, ob, ob_type in rows:
        debit, credit = totals.get(account_id, (None, None))
        ob_dr, ob_cr = _opening_balance_dr_cr(ob, ob_type)
        debit_total = money(debit or 0) + ob_dr
        credit_total = money(credit or 0) + ob_cr
        if not (debit_total or credit_total):
            continue
        result.append({"code": code, "name": name, "debit": amount(debit_total), "credit": amount(credit_total)})
    return result


def balance_sheet_rows(db: Session, company_id: str, branch_id: str | None = None) -> dict[str, Any]:
    account_totals = posted_account_totals(db, company_id, branch_id)
    rows = (
        db.query(Account.id, Account.code, Account.name, Account.type, Account.opening_balance, Account.opening_balance_type)
        .filter(Account.company_id == company_id)
        .order_by(Account.code)
        .all()
    )
    sections: dict[str, list[dict[str, str]]] = {"assets": [], "liabilities": [], "equity": []}
    totals = {"assets": Decimal("0.00"), "liabilities": Decimal("0.00"), "equity": Decimal("0.00")}
    earnings = Decimal("0.00")
    for account_id, code, name, account_type, ob, ob_type in rows:
        debit, credit = account_totals.get(account_id, (None, None))
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
        elif normalized in _REVENUE_ACCOUNT_TYPES:
            earnings += credit_value - debit_value
            continue
        elif normalized in _EXPENSE_ACCOUNT_TYPES:
            earnings -= debit_value - credit_value
            continue
        else:
            continue
        sections[section].append({"code": code, "name": name, "amount": amount(balance)})
        totals[section] += balance
    if earnings:
        sections["equity"].append({"code": "", "name": "Current period earnings", "amount": amount(earnings)})
        totals["equity"] += earnings
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

    # DB invoices — no due_date field; use created_at + 30 days as proxy.
    # Excludes "cancelled" (voided, never going to be collected — was
    # inflating AR/overdue totals) and "draft" (not yet a committed sale,
    # same recognized-revenue line the rest of this file already draws)
    # alongside "paid", not just "paid" alone.
    query = db.query(Invoice.customer_name, Invoice.total, Invoice.created_at).filter(
        Invoice.company_id == company_id, Invoice.status.notin_(["paid", "cancelled", "draft"])
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
        status_norm = normalized_ref(invoice.get("status", ""))
        if is_paid_status(invoice.get("status")) or _is_credit_note(invoice) or status_norm in {"cancelled", "draft"}:
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


def suggested_actions(
    overdue_total: Decimal,
    net_vat: Decimal,
    payroll: Decimal,
    net_profit: Decimal = Decimal("0"),
    gross_margin: Decimal = Decimal("0"),
    revenue: Decimal = Decimal("0"),
    ap_total: Decimal = Decimal("0"),
    ar_total: Decimal = Decimal("0"),
    working_capital: Decimal = Decimal("0"),
) -> list[str]:
    actions = []
    if overdue_total:
        actions.append(f"Follow up AED {amount(overdue_total)} overdue receivables.")
    actions.append(f"Review net VAT payable AED {amount(net_vat)} before filing.")
    if payroll:
        actions.append(f"Reconcile payroll net AED {amount(payroll)} against WPS records.")
    if net_profit < 0:
        actions.append(f"Net loss of AED {amount(-net_profit)} this period — review cost structure before next filing.")
    if revenue and gross_margin < Decimal("15"):
        actions.append(f"Gross margin is thin at {amount(gross_margin)}% — review pricing or cost of goods sold.")
    if revenue and payroll and (payroll / revenue) > Decimal("0.40"):
        actions.append(f"Payroll is {amount(payroll / revenue * Decimal('100'))}% of revenue — above the typical 40% benchmark.")
    if ap_total and ar_total and ap_total > ar_total * Decimal("1.5"):
        actions.append(f"Payables (AED {amount(ap_total)}) significantly exceed receivables (AED {amount(ar_total)}) — watch near-term cash outflow.")
    if working_capital < 0:
        actions.append(f"Working capital is negative (AED {amount(working_capital)}) — current liabilities exceed current assets.")
    if not actions:
        actions.append("No report exceptions found in the current database records.")
    return actions


def anomaly_rows(
    overdue_total: Decimal,
    input_vat: Decimal,
    output_vat: Decimal,
    payroll: Decimal,
    net_profit: Decimal = Decimal("0"),
    gross_margin: Decimal = Decimal("0"),
    revenue: Decimal = Decimal("0"),
    ap_total: Decimal = Decimal("0"),
    ar_total: Decimal = Decimal("0"),
    working_capital: Decimal = Decimal("0"),
) -> list[dict[str, str]]:
    rows = []
    if overdue_total:
        rows.append({"area": "Receivables", "signal": f"AED {amount(overdue_total)} outstanding", "impact": "Medium", "action": "Open"})
    if input_vat > output_vat:
        rows.append({"area": "VAT", "signal": "Input VAT is higher than output VAT", "impact": "Medium", "action": "Review"})
    if net_profit < 0:
        rows.append({"area": "Profitability", "signal": f"Net loss of AED {amount(-net_profit)} this period", "impact": "High", "action": "Review"})
    if revenue and gross_margin < Decimal("15"):
        rows.append({"area": "Margins", "signal": f"Gross margin is only {amount(gross_margin)}%", "impact": "Medium", "action": "Review"})
    if revenue and payroll and (payroll / revenue) > Decimal("0.40"):
        rows.append({"area": "Payroll", "signal": f"Payroll is {amount(payroll / revenue * Decimal('100'))}% of revenue", "impact": "Medium", "action": "Review"})
    if ap_total and ar_total and ap_total > ar_total * Decimal("1.5"):
        rows.append({"area": "Cash Flow", "signal": f"Payables exceed receivables by AED {amount(ap_total - ar_total)}", "impact": "Medium", "action": "Review"})
    if working_capital < 0:
        rows.append({"area": "Liquidity", "signal": f"Working capital is negative (AED {amount(working_capital)})", "impact": "High", "action": "Review"})
    if payroll:
        rows.append({"area": "Payroll", "signal": f"AED {amount(payroll)} payroll net included in reports", "impact": "Low", "action": "Check"})
    if not rows:
        rows.append({"area": "Reports", "signal": "No anomalies found from current DB data", "impact": "Low", "action": "View"})
    return rows


def corporate_report_rows(db: Session, company_id: str, net_profit: Decimal) -> dict[str, Any]:
    corporate_rows = db.query(CorporateTaxRecord).filter(CorporateTaxRecord.company_id == company_id).order_by(CorporateTaxRecord.created_at.desc()).all()
    if corporate_rows:
        latest = corporate_rows[0]
        # Profit is always the live P&L figure; the stored record only
        # contributes its adjustments and status (its own profit goes stale).
        accounting_profit = net_profit
        tax_adjustments = money(latest.tax_adjustments)
        taxable_income = max(Decimal("0.00"), accounting_profit + tax_adjustments)
        tax_due = max(Decimal("0.00"), taxable_income - Decimal("375000.00")) * Decimal("0.09")
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
            # Not "Small Business Relief" — that's a separate, elective
            # relief (revenue <= AED 3,000,000, must be actively elected).
            # This is the standard 0% CT bracket every company gets
            # automatically on the first AED 375,000 of taxable income;
            # labeling it "Small Business Relief" could lead a filer to
            # believe they'd made an election they hadn't.
            {"line": "0% Tax Bracket Threshold", "amount": "375000.00", "status": "Applied"},
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
    from app.routers.corporate_accounting import _released_amount
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
                "remaining": amount(max(Decimal("0.00"), money(row.total_amount) - _released_amount(db, company_id, row.id))),
                "status": row.status,
            }
            for row in accruals
            if "prepay" in row.record_type.lower()
        ],
    }


def _operating_cash(db: Session, company_id: str, net_profit: Decimal) -> Decimal:
    """Cash actually held per the ledger (Cash/Bank asset accounts); accrual
    profit is only a fallback when no cash account has any activity."""
    bs = balance_sheet_rows(db, company_id)
    cash_rows = [r for r in bs["assets"] if any(k in r["name"].lower() for k in ("cash", "bank"))]
    if not cash_rows:
        return net_profit if not bs["assets"] else Decimal("0.00")
    return sum((Decimal(r["amount"]) for r in cash_rows), Decimal("0.00"))


def budget_cash_rows(db: Session, company_id: str, revenue: Decimal, purchases: Decimal, operating_expenses: Decimal, net_profit: Decimal) -> dict[str, Any]:
    operating_cash = _operating_cash(db, company_id, net_profit)
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
            {"section": "Operating Cash Flow", "direct": amount(operating_cash), "indirect": amount(operating_cash), "status": "Ready"},
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
    # Real GL-based revenue/cost/profit per named cost centre — this used to
    # hardcode "0.00" for every row regardless of actual data, even though
    # _cost_center_breakdown() (used by the Profitability Analytics tab)
    # already computes this correctly from GeneralLedgerEntry.cost_center.
    # Matched case-insensitively since a center's free-text `name` here and
    # the cost_center string stamped on a GL entry via Accounting > Vouchers
    # aren't guaranteed identical casing.
    gl_by_center = {row["cost_center"].strip().lower(): row for row in _cost_center_breakdown(db, company_id, None)}
    cost_rows = []
    for row in centers:
        gl = gl_by_center.get((row.name or "").strip().lower())
        cost_rows.append({
            "center": row.name,
            "revenue": gl["revenue"] if gl else "0.00",
            "cost": gl["expense"] if gl else "0.00",
            "profit": gl["profit"] if gl else "0.00",
            "margin": f"{gl['margin_pct']}%" if gl else "0.00%",
        })
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
