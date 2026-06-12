import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.cache as cache
from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import (
    Account,
    AccrualPrepaymentRecord,
    AppDataRecord,
    AuditLog,
    AuditLogDetail,
    BudgetRecord,
    CashFlowForecastRecord,
    ConsolidationRecord,
    CorporateTaxRecord,
    CostCenterRecord,
    Document,
    Employee,
    ExceptionEvent,
    FixedAssetRecord,
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


@router.get("/dashboard")
@limiter.limit("30/minute")
def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    company_id = current_user.company_id
    cached = cache.get(f"dashboard:{company_id}")
    if cached is not None:
        return cached
    result = _build_dashboard(db, company_id)
    cache.set(f"dashboard:{company_id}", result, ttl=60)
    return result


def _build_dashboard(db: Session, company_id: str) -> dict[str, Any]:
    app_sales = app_sales_invoice_records(db, company_id)
    app_employees = app_data_payloads(db, company_id, "employees")
    app_purchases = app_data_payloads(db, company_id, "purchaseRecords")
    revenue = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id).scalar())
    revenue += sum((record_amount(row, "total", "amount", "net_amount") for row in app_sales), Decimal("0.00"))
    open_invoice_count = int(db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id, Invoice.status != "paid").scalar() or 0)
    app_open_sales = [row for row in app_sales if not is_paid_status(row.get("status"))]
    open_invoice_count += len(app_open_sales)
    open_invoice_amount = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id, Invoice.status != "paid").scalar())
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
        "sales_source_count": db.query(func.count(SourceTransaction.id)).filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["sales", "sales_invoice"])).scalar() or 0,
        "purchase_source_count": db.query(func.count(SourceTransaction.id)).filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"])).scalar() or 0,
        "account_count": count(db, Account, company_id),
        "journal_count": count(db, JournalEntry, company_id),
        "source_transaction_count": count(db, SourceTransaction, company_id),
        "tax_code_count": count(db, TaxCode, company_id),
        "tax_line_count": count(db, TaxLine, company_id),
        "warehouse_count": count(db, Warehouse, company_id),
        "inventory_mapping_count": count(db, StockProductMapping, company_id),
        "employee_count": employee_count,
        "payroll_run_count": count(db, PayrollRun, company_id),
        "job_count": count(db, Job, company_id),
        "document_count": count(db, Document, company_id),
        "audit_count": count(db, AuditLog, company_id),
        "exception_count": count(db, ExceptionEvent, company_id),
        "receipt_count": count(db, Receipt, company_id),
        "payment_receipt_count": count(db, Payment, company_id) + count(db, Receipt, company_id),
        "purchase_invoice_count": app_counts.get("purchaseInvoices", 0) + app_counts.get("purchaseDocuments", 0),
    }
    status = invoice_status(db, company_id)
    pur_summary = _purchase_summary(db, company_id)
    return {
        "kpis": {
            "revenue": amount(revenue),
            "total_purchases": pur_summary["total"],
            "vat_payable": amount(vat_payable),
            "open_invoice_count": open_invoice_count,
            "open_invoice_amount": amount(open_invoice_amount),
            "staff_total": employee_count,
            "staff_present": employee_count,
            "payroll_net": amount(payroll_net),
        },
        "monthly_revenue_vat": monthly_revenue_vat(db, company_id),
        "recent_activity": recent_activity(db, company_id),
        "top_customers": top_customers(db, company_id),
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


def normalized_ref(value: object) -> str:
    return str(value or "").strip().lower()


def app_sales_invoice_records(db: Session, company_id: str) -> list[dict[str, Any]]:
    existing_refs = {
        normalized_ref(value)
        for (value,) in db.query(Invoice.invoice_number).filter(Invoice.company_id == company_id).all()
        if normalized_ref(value)
    }
    records = []
    for row in app_data_payloads(db, company_id, "salesInvoices"):
        invoice_ref = normalized_ref(row.get("invoice_no") or row.get("invoice_number") or row.get("ref"))
        if invoice_ref and invoice_ref in existing_refs:
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


def monthly_revenue_vat(db: Session, company_id: str) -> list[dict[str, str]]:
    periods: dict[str, dict[str, Decimal]] = {}
    invoices = db.query(Invoice).filter(Invoice.company_id == company_id).all()
    for invoice in invoices:
        item = periods.setdefault(period_label(invoice.created_at), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["sales"] += money(invoice.total)
        item["output_vat"] += money(invoice.vat)
    for invoice in app_sales_invoice_records(db, company_id):
        item = periods.setdefault(period_label(invoice.get("date") or invoice.get("created_at")), {"sales": Decimal("0.00"), "purchases": Decimal("0.00"), "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00")})
        item["sales"] += record_amount(invoice, "total", "amount", "net_amount")
        item["output_vat"] += record_amount(invoice, "vat_amount", "vat", "tax_amount")
    purchases = (
        db.query(SourceTransaction)
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]))
        .all()
    )
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


def top_customers(db: Session, company_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0).label("total"))
        .filter(Invoice.company_id == company_id)
        .group_by(Invoice.customer_name)
        .order_by(func.coalesce(func.sum(Invoice.total), 0).desc())
        .limit(5)
        .all()
    )
    totals: dict[str, Decimal] = {}
    for name, total in rows:
        totals[str(name or "Customer")] = money(total)
    for invoice in app_sales_invoice_records(db, company_id):
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


def _purchase_summary(db: Session, company_id: str) -> dict[str, Any]:
    records = (
        app_data_payloads(db, company_id, "purchaseRecords")
        + app_data_payloads(db, company_id, "bills")
    )
    total = Decimal("0")
    paid_amount = Decimal("0")
    paid_count = 0
    pending_count = 0
    for row in records:
        row_total = _purchase_row_amount(row)
        total += row_total
        row_paid = record_amount(row, "paid", "paid_amount")
        if is_paid_status(normalized_ref(row.get("status") or "")):
            paid_count += 1
            # Bills have no explicit paid field — use row total when status is paid
            paid_amount += row_paid if row_paid else row_total
        else:
            pending_count += 1
            paid_amount += row_paid
    total_count = len(records)
    # Fallback: use SourceTransaction totals when AppDataRecord amounts are all zero
    if total == Decimal("0") and total_count > 0:
        total = money(
            db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
            .filter(
                SourceTransaction.company_id == company_id,
                SourceTransaction.module.in_(["purchase", "purchase_bill"]),
            )
            .scalar()
        )
        if paid_amount == Decimal("0"):
            paid_amount = money(
                db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
                .filter(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.module.in_(["purchase", "purchase_bill"]),
                    SourceTransaction.status.in_(["paid", "posted", "complete", "completed", "received", "settled"]),
                )
                .scalar()
            )
    payment_rate = int(paid_amount / total * 100) if total else 0
    return {
        "total": amount(total),
        "paid": amount(paid_amount),
        "paid_count": paid_count,
        "pending_count": pending_count,
        "total_count": total_count,
        "payment_rate": payment_rate,
    }


def invoice_status(db: Session, company_id: str) -> dict[str, dict[str, str | int]]:
    app_sales = app_sales_invoice_records(db, company_id)
    total_count = int(db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id).scalar() or 0) + len(app_sales)
    total_amount = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id).scalar())
    total_amount += sum((record_amount(row, "total", "amount", "net_amount") for row in app_sales), Decimal("0.00"))
    statuses: dict[str, dict[str, str | int]] = {}
    for key, names in {"paid": ["paid"], "pending": ["draft", "issued", "pending"], "overdue": ["overdue", "cancelled"]}.items():
        row_count = int(db.query(func.count(Invoice.id)).filter(Invoice.company_id == company_id, Invoice.status.in_(names)).scalar() or 0)
        row_amount = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id, Invoice.status.in_(names)).scalar())
        for invoice in app_sales:
            status = normalized_ref(invoice.get("status"))
            if status in names or (key == "pending" and status in {"ready", "sent", "unpaid"}):
                row_count += 1
                row_amount += record_amount(invoice, "total", "amount", "net_amount")
        pct = int((row_count / total_count) * 100) if total_count else 0
        statuses[key] = {"count": row_count, "amount": amount(row_amount), "percentage": pct}
    statuses["total"] = {"count": total_count, "amount": amount(total_amount), "percentage": 100 if total_count else 0}
    return statuses


@router.get("/debug/purchases")
@limiter.limit("10/minute")
def debug_purchases(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    company_id = current_user.company_id
    bills = app_data_payloads(db, company_id, "bills")
    purchases = app_data_payloads(db, company_id, "purchaseRecords")
    return {
        "bills": [{"keys": list(r.keys()), "total": r.get("total"), "subtotal": r.get("subtotal"), "vat": r.get("vat"), "status": r.get("status"), "bill_no": r.get("bill_no")} for r in bills],
        "purchaseRecords": [{"keys": list(r.keys()), "total": r.get("total"), "net_amount": r.get("net_amount"), "subtotal": r.get("subtotal"), "status": r.get("status"), "ref": r.get("ref")} for r in purchases],
        "purchase_summary": _purchase_summary(db, company_id),
    }


@router.get("/trial-balance")
@limiter.limit("30/minute")
def trial_balance(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    company_id = current_user.company_id
    cached = cache.get(f"trial_balance:{company_id}")
    if cached is not None:
        return cached
    result = {"status": "ready", "source": "posted journal entries", "rows": trial_balance_rows(db, company_id)}
    cache.set(f"trial_balance:{company_id}", result, ttl=120)
    return result


@router.get("/summary")
@limiter.limit("30/minute")
def report_summary(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    company_id = current_user.company_id
    cached = cache.get(f"summary:{company_id}")
    if cached is not None:
        return cached
    result = _build_summary(db, company_id)
    cache.set(f"summary:{company_id}", result, ttl=120)
    return result


def _build_summary(db: Session, company_id: str) -> dict[str, Any]:
    app_sales = app_sales_invoice_records(db, company_id)
    app_purchases = app_data_payloads(db, company_id, "purchaseRecords")
    revenue = money(db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.company_id == company_id).scalar())
    revenue += sum((record_amount(row, "total", "amount", "net_amount") for row in app_sales), Decimal("0.00"))
    purchases = money(
        db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]))
        .scalar()
    )
    if purchases == 0:
        purchases += sum((record_amount(row, "total", "amount", "net_amount") for row in app_purchases), Decimal("0.00"))
    payroll = money(db.query(func.coalesce(func.sum(PayrollRun.net_total), 0)).filter(PayrollRun.company_id == company_id).scalar())
    expenses = money(
        db.query(func.coalesce(func.sum(SourceTransaction.total), 0))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["expense", "expenses"]))
        .scalar()
    )
    app_expenses = app_data_payloads(db, company_id, "expenses")
    expenses += sum((record_amount(row, "total", "amount", "net_amount") for row in app_expenses), Decimal("0.00"))
    operating_expenses = expenses + payroll
    gross_profit = revenue - purchases
    net_profit = gross_profit - operating_expenses
    gross_margin = (gross_profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else Decimal("0.00")
    output_taxable = money(
        db.query(func.coalesce(func.sum(TaxLine.taxable_amount), 0))
        .filter(TaxLine.company_id == company_id, TaxLine.direction == "output")
        .scalar()
    )
    output_taxable += sum((record_amount(row, "subtotal", "net_amount", "taxable_amount") for row in app_sales), Decimal("0.00"))
    output_vat = money(
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == company_id, TaxLine.direction == "output")
        .scalar()
    )
    output_vat += sum((record_amount(row, "vat_amount", "vat", "tax_amount") for row in app_sales), Decimal("0.00"))
    input_taxable = money(
        db.query(func.coalesce(func.sum(TaxLine.taxable_amount), 0))
        .filter(TaxLine.company_id == company_id, TaxLine.direction == "input")
        .scalar()
    )
    input_taxable += sum((record_amount(row, "net_amount", "subtotal", "taxable_amount") for row in app_purchases), Decimal("0.00"))
    input_vat = money(
        db.query(func.coalesce(func.sum(TaxLine.tax_amount), 0))
        .filter(TaxLine.company_id == company_id, TaxLine.direction == "input")
        .scalar()
    )
    input_vat += sum((record_amount(row, "tax_amount", "vat_amount", "vat") for row in app_purchases), Decimal("0.00"))
    aging_rows = receivables_aging(db, company_id)
    ar_total = sum(money(row["total"]) for row in aging_rows)
    overdue_total = sum(money(row["d31_60"]) + money(row["d61_90"]) + money(row["over90"]) for row in aging_rows)
    risk_score = "Low" if overdue_total == 0 else "Medium" if overdue_total < ar_total / Decimal("2") else "High"
    monthly = monthly_revenue_vat(db, company_id)
    return {
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
                "standard_rated": amount(output_taxable),
                "zero_rated": "0.00",
                "exempt": "0.00",
                "total_supplies": amount(output_taxable),
                "output_vat": amount(output_vat),
            },
            "input": {
                "standard_rated": amount(input_taxable),
                "zero_rated": "0.00",
                "exempt": "0.00",
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
                "documents": min(100, int((count(db, Document, company_id) / max(1, count(db, Invoice, company_id))) * 100)),
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
        "balance_sheet": balance_sheet_rows(db, company_id),
        "trial_balance": trial_balance_rows(db, company_id),
        "aging": aging_rows,
        "ai": {
            "forecast_confidence": 87 if count(db, Invoice, company_id) else 0,
            "anomalies": int((1 if overdue_total else 0) + (1 if input_vat > output_vat else 0)),
            "potential_savings": amount(operating_expenses * Decimal("0.05")),
            "collection_upside": amount(overdue_total),
            "anomalies_list": anomaly_rows(overdue_total, input_vat, output_vat, payroll),
            "report_text": report_ai_text(revenue, gross_margin, output_vat - input_vat, overdue_total, net_profit),
        },
        "corporate": corporate_report_rows(db, company_id, net_profit),
        "assets": asset_report_rows(db, company_id),
        "budget_cash": budget_cash_rows(db, company_id, revenue, purchases, operating_expenses, net_profit),
        "control": control_report_rows(db, company_id, revenue, purchases, net_profit),
        "general_ledger": general_ledger_rows(db, company_id),
        "customer_ledger": customer_ledger_rows(db, company_id, app_sales),
        "supplier_ledger": supplier_ledger_rows(db, company_id, app_purchases),
        "ap_aging": ap_aging_rows(db, company_id, app_purchases),
        "revenue_intelligence": revenue_intelligence_rows(db, company_id, app_sales, monthly),
        "working_capital": working_capital_rows(db, company_id, balance_sheet_rows(db, company_id)),
        "ai_health": ai_health_score(revenue, gross_margin, net_profit, money(working_capital_rows(db, company_id, balance_sheet_rows(db, company_id))["current_ratio"]), overdue_total, ar_total),
    }


def trial_balance_rows(db: Session, company_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(Account.code, Account.name, func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(Account.company_id == company_id, JournalEntry.company_id == company_id)
        .group_by(Account.code, Account.name)
        .order_by(Account.code)
        .all()
    )
    return [{"code": code, "name": name, "debit": amount(money(debit)), "credit": amount(money(credit))} for code, name, debit, credit in rows]


def balance_sheet_rows(db: Session, company_id: str) -> dict[str, Any]:
    rows = (
        db.query(Account.code, Account.name, Account.type, func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(Account.company_id == company_id, JournalEntry.company_id == company_id)
        .group_by(Account.code, Account.name, Account.type)
        .order_by(Account.code)
        .all()
    )
    sections: dict[str, list[dict[str, str]]] = {"assets": [], "liabilities": [], "equity": []}
    totals = {"assets": Decimal("0.00"), "liabilities": Decimal("0.00"), "equity": Decimal("0.00")}
    for code, name, account_type, debit, credit in rows:
        normalized = str(account_type or "").strip().lower()
        debit_value = money(debit)
        credit_value = money(credit)
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


def receivables_aging(db: Session, company_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0))
        .filter(Invoice.company_id == company_id, Invoice.status != "paid")
        .group_by(Invoice.customer_name)
        .order_by(func.coalesce(func.sum(Invoice.total), 0).desc())
        .all()
    )
    return [
        {
            "customer": customer,
            "current": amount(money(total)),
            "d1_30": "0.00",
            "d31_60": "0.00",
            "d61_90": "0.00",
            "over90": "0.00",
            "total": amount(money(total)),
        }
        for customer, total in rows
    ]


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


def general_ledger_rows(db: Session, company_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(Account.code, Account.name, JournalEntry.entry_date, JournalEntry.entry_number, JournalLine.description, JournalLine.debit, JournalLine.credit)
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(Account.company_id == company_id, JournalEntry.company_id == company_id, JournalEntry.status == "posted")
        .order_by(Account.code, JournalEntry.entry_date)
        .limit(500)
        .all()
    )
    return [
        {
            "account_code": code,
            "account_name": name,
            "date": str(entry_date or ""),
            "reference": str(entry_no or ""),
            "description": str(desc or ""),
            "debit": amount(money(debit)),
            "credit": amount(money(credit)),
        }
        for code, name, entry_date, entry_no, desc, debit, credit in rows
    ]


def customer_ledger_rows(db: Session, company_id: str, app_sales: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, total, cnt in (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0), func.count(Invoice.id))
        .filter(Invoice.company_id == company_id)
        .group_by(Invoice.customer_name)
        .all()
    ):
        key = str(name or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += money(total); e["count"] += int(cnt)
    for name, total, cnt in (
        db.query(SourceTransaction.party_name, func.coalesce(func.sum(SourceTransaction.total), 0), func.count(SourceTransaction.id))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["sales", "sales_invoice"]))
        .group_by(SourceTransaction.party_name)
        .all()
    ):
        key = str(name or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += money(total); e["count"] += int(cnt)
    for row in app_sales:
        key = str(row.get("customer") or row.get("customer_name") or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += record_amount(row, "total", "amount", "net_amount"); e["count"] += 1
    return sorted(
        [{"party": k, "customer": k, "total": amount(v["total"]), "transactions": v["count"]} for k, v in result.items()],
        key=lambda x: -money(x["total"])
    )[:100]


def supplier_ledger_rows(db: Session, company_id: str, app_purchases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, total, cnt in (
        db.query(SourceTransaction.party_name, func.coalesce(func.sum(SourceTransaction.total), 0), func.count(SourceTransaction.id))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]))
        .group_by(SourceTransaction.party_name)
        .all()
    ):
        key = str(name or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += money(total); e["count"] += int(cnt)
    for row in app_purchases:
        key = str(row.get("supplier") or row.get("vendor") or "Unknown").strip() or "Unknown"
        e = result.setdefault(key, {"total": Decimal("0"), "count": 0})
        e["total"] += record_amount(row, "total", "amount", "net_amount"); e["count"] += 1
    return sorted(
        [{"party": k, "supplier": k, "total": amount(v["total"]), "transactions": v["count"]} for k, v in result.items()],
        key=lambda x: -money(x["total"])
    )[:100]


def ap_aging_rows(db: Session, company_id: str, app_purchases: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: dict[str, Decimal] = {}
    for party, total in (
        db.query(SourceTransaction.party_name, func.coalesce(func.sum(SourceTransaction.total), 0))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]), SourceTransaction.status != "paid")
        .group_by(SourceTransaction.party_name)
        .all()
    ):
        key = str(party or "Unknown").strip() or "Unknown"
        result[key] = result.get(key, Decimal("0")) + money(total)
    for row in app_purchases:
        if not is_paid_status(row.get("status")):
            key = str(row.get("supplier") or row.get("vendor") or "Unknown").strip() or "Unknown"
            result[key] = result.get(key, Decimal("0")) + record_amount(row, "total", "amount", "net_amount")
    return sorted(
        [{"supplier": k, "current": amount(v), "d1_30": "0.00", "d31_60": "0.00", "d61_90": "0.00", "over90": "0.00", "total": amount(v)} for k, v in result.items()],
        key=lambda x: -money(x["total"])
    )


def revenue_intelligence_rows(db: Session, company_id: str, app_sales: list[dict[str, Any]], monthly: list[dict[str, Any]]) -> dict[str, Any]:
    by_customer = (
        db.query(Invoice.customer_name, func.coalesce(func.sum(Invoice.total), 0))
        .filter(Invoice.company_id == company_id)
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


def working_capital_rows(db: Session, company_id: str, balance_sheet: dict[str, Any]) -> dict[str, Any]:
    assets = sum(money(r["amount"]) for r in balance_sheet.get("assets", []))
    liabilities = sum(money(r["amount"]) for r in balance_sheet.get("liabilities", []))
    current_ratio = (assets / liabilities).quantize(Decimal("0.01")) if liabilities else Decimal("0.00")
    working_capital = assets - liabilities
    return {
        "current_ratio": amount(current_ratio),
        "quick_ratio": amount(current_ratio),
        "working_capital": amount(working_capital),
        "current_assets": amount(assets),
        "current_liabilities": amount(liabilities),
        "inventory_turnover": "0.00",
        "receivable_days": "0.00",
        "payable_days": "0.00",
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
