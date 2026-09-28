"""Live company data for the AI Assistant, so it can answer questions like
"what's my revenue this month" or "who owes me the most" from real numbers.

Built from the same (cached) report builders the Dashboard and Reports pages
use, trimmed to a compact JSON snapshot. Read-only; company-scoped to the
signed-in admin's company."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.auth_principal import Principal
from app.models import Company, Employee, ExceptionEvent, User
from app.voice_briefing import next_vat_due

TOP_N = 10


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _top(rows: list[dict[str, Any]] | None, key: str = "total", n: int = TOP_N) -> list[dict[str, Any]]:
    rows = [r for r in (rows or []) if _dec(r.get(key)) != 0]
    return sorted(rows, key=lambda r: _dec(r.get(key)), reverse=True)[:n]


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def build_ai_context(db: Session, user: User) -> dict[str, Any]:
    from app.routers import reports  # local import: reports imports a lot at module load

    company_id = user.company_id
    company = db.get(Company, company_id)
    today = dt.date.today()
    dashboard = _safe(lambda: reports._cached_or_build(f"dashboard:{company_id}:all", 60,
                                                        lambda: reports._build_dashboard(db, company_id, None)), {})
    summary = _safe(lambda: reports._cached_or_build(f"summary:{company_id}:all", 120,
                                                      lambda: reports._build_summary(db, company_id, None)), {})
    period_end, vat_due = next_vat_due(today)

    kpis = dashboard.get("kpis") or {}
    context: dict[str, Any] = {
        "company": {"name": getattr(company, "name", ""), "currency": getattr(company, "currency", None) or "AED",
                    "vat_rate_percent": str(getattr(company, "vat_rate", None) or "5"), "today": today.isoformat()},
        "totals_to_date": {
            "revenue": kpis.get("revenue"), "purchases": kpis.get("total_purchases"),
            "output_vat": kpis.get("output_vat"), "input_vat": kpis.get("input_vat"), "vat_payable": kpis.get("vat_payable"),
            "open_invoices": kpis.get("open_invoice_count"), "open_invoice_amount": kpis.get("open_invoice_amount"),
            "payroll_net": kpis.get("payroll_net"),
        },
        "profit_and_loss": summary.get("profit_loss"),
        "monthly_revenue_and_vat": (dashboard.get("monthly_revenue_vat") or [])[-12:],
        # Status field only ("overdue" = explicitly marked); past-due by due date is in receivables_summary.
        "sales_invoices_by_status_field": dashboard.get("invoice_status"),
        "receivables_summary": _receivables_summary(summary.get("aging")),
        "purchases": dashboard.get("purchase_summary"),
        "top_customers_by_sales": (dashboard.get("top_customers") or [])[:TOP_N],
        "receivables_aging_top": _top(summary.get("aging")),
        "payables_aging_top": _top(summary.get("ap_aging")),
        "top_suppliers_by_purchases": _top(summary.get("supplier_ledger")),
        "working_capital": summary.get("working_capital"),
        "business_health": summary.get("ai_health"),
        "vat_position": ("refund due" if _dec(kpis.get("vat_payable")) < 0 else "payable"),
        "vat_return": {"assumes_calendar_quarters": True, "current_period_end": period_end.isoformat(),
                       "due_date": vat_due.isoformat(), "days_left": (vat_due - today).days},
        "staff": dashboard.get("hr_snapshot"),
        "staff_by_department": _safe(lambda: _staff_by_department(db, company_id), {}),
        "low_stock": _safe(lambda: _low_stock(db, user), []),
        "open_exceptions": _safe(lambda: [
            {"category": r.category, "severity": r.severity, "message": r.message}
            for r in db.query(ExceptionEvent).filter(ExceptionEvent.company_id == company_id, ExceptionEvent.status != "closed")
            .order_by(ExceptionEvent.created_at.desc()).limit(8)
        ], []),
    }
    return {k: v for k, v in context.items() if v not in (None, {}, [])}


def _receivables_summary(aging: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Unpaid customer balances split by due date, from the AR aging report."""
    buckets = {k: Decimal(0) for k in ("current", "d1_30", "d31_60", "d61_90", "over90")}
    past_due_customers = 0
    for row in aging or []:
        for k in buckets:
            buckets[k] += _dec(row.get(k))
        if any(_dec(row.get(k)) > 0 for k in ("d1_30", "d31_60", "d61_90", "over90")):
            past_due_customers += 1
    past_due = buckets["d1_30"] + buckets["d31_60"] + buckets["d61_90"] + buckets["over90"]
    return {
        "total_outstanding": f"{sum(buckets.values()):.2f}", "not_yet_due": f"{buckets['current']:.2f}",
        "past_due": f"{past_due:.2f}", "past_due_1_30_days": f"{buckets['d1_30']:.2f}",
        "past_due_31_60_days": f"{buckets['d31_60']:.2f}", "past_due_61_90_days": f"{buckets['d61_90']:.2f}",
        "past_due_over_90_days": f"{buckets['over90']:.2f}", "customers_with_past_due": past_due_customers,
    }


def _staff_by_department(db: Session, company_id: str) -> dict[str, int]:
    from sqlalchemy import func

    rows = (
        db.query(Employee.department, func.count(Employee.id))
        .filter(Employee.company_id == company_id, Employee.status == "active")
        .group_by(Employee.department)
        .all()
    )
    return {dept or "Unassigned": n for dept, n in rows}


def _low_stock(db: Session, user: User) -> list[dict[str, Any]]:
    from app.routers.inventory import list_stock_levels

    principal = Principal(kind="user", company_id=user.company_id, display_name=user.full_name,
                          is_admin=True, permissions=frozenset(), user=user)
    rows = list_stock_levels(branch_id=None, db=db, principal=principal) or []
    out = []
    for r in rows:
        stock = _dec(r.get("current_stock"))
        reorder = _dec(r.get("reorder_level") or r.get("min_stock"))
        if stock <= 0 or (reorder and stock <= reorder):
            out.append({"product": r.get("name"), "code": r.get("code"), "in_stock": str(stock), "reorder_level": str(reorder)})
    return sorted(out, key=lambda r: _dec(r["in_stock"]))[:TOP_N]


def rule_answer(q: str, ctx: dict[str, Any]) -> str | None:
    """Plain answers from the snapshot when no AI key is configured."""
    q = q.lower()
    cur = (ctx.get("company") or {}).get("currency", "AED")
    t = ctx.get("totals_to_date") or {}
    pl = ctx.get("profit_and_loss") or {}
    if any(w in q for w in ("profit", "margin", "ربح")):
        return f"Revenue to date is {cur} {pl.get('revenue', t.get('revenue', '0'))}, gross profit {cur} {pl.get('gross_profit', '0')} and net profit {cur} {pl.get('net_profit', '0')}."
    if any(w in q for w in ("owe me", "receivable", "overdue", "outstanding", "debtor", "متأخر")):
        top = ctx.get("receivables_aging_top") or []
        lead = ", ".join(f"{r.get('customer')} ({cur} {r.get('total')})" for r in top[:3])
        rs = ctx.get("receivables_summary") or {}
        return (f"{cur} {_dec(rs.get('past_due')):,.2f} is past due across {rs.get('customers_with_past_due', 0)} customers "
                f"(total outstanding {cur} {_dec(rs.get('total_outstanding')):,.2f}).") + (f" Largest balances: {lead}." if lead else "")
    if any(w in q for w in ("vat", "tax", "ضريبة")):
        v = ctx.get("vat_return") or {}
        vp = _dec(t.get("vat_payable"))
        head = f"A VAT refund of {cur} {abs(vp):,.2f} is due" if vp < 0 else f"VAT payable so far is {cur} {vp:,.2f}"
        return f"{head} (output {cur} {t.get('output_vat', '0')}, input {cur} {t.get('input_vat', '0')}). The next return is due on {v.get('due_date')}, in {v.get('days_left')} days."
    if any(w in q for w in ("revenue", "sales", "income", "مبيعات", "إيرادات")):
        months = ctx.get("monthly_revenue_and_vat") or []
        last = months[-1] if months else {}
        return f"Revenue to date is {cur} {t.get('revenue', '0')}." + (f" Latest month ({last.get('period')}): {cur} {last.get('sales')}." if last else "")
    if any(w in q for w in ("staff", "employee", "present", "absent", "attendance", "موظف")):
        s = ctx.get("staff") or {}
        return f"{s.get('present_today', 0)} of {s.get('employee_count', 0)} staff are present today, {s.get('on_leave_today', 0)} on leave; {s.get('pending_approvals', 0)} approvals are pending."
    if any(w in q for w in ("stock", "inventory", "مخزون")):
        low = ctx.get("low_stock") or []
        return ("Low or out of stock: " + ", ".join(f"{r['product']} ({r['in_stock']})" for r in low[:5]) + ".") if low else "No products are below their reorder level."
    if any(w in q for w in ("supplier", "payable", "pay ", "مورد")):
        top = ctx.get("payables_aging_top") or []
        return ("You owe most to: " + ", ".join(f"{r.get('supplier') or r.get('customer') or r.get('party')} ({cur} {r.get('total')})" for r in top[:3]) + ".") if top else "No supplier balances are outstanding."
    return None
