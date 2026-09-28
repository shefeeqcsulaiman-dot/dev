"""Spoken daily briefing: a few short sentences built from the same numbers
the dashboard shows (overdue invoices, unpaid purchases, VAT due date, staff
today, pending approvals, open exceptions). Read-only; no LLM involved."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import ExceptionEvent

QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def next_vat_due(today: dt.date) -> tuple[dt.date, dt.date]:
    """(period_end, due_date) of the next quarterly VAT return still open:
    UAE returns are due 28 days after the tax period ends. Assumes calendar
    quarters, the most common FTA stagger."""
    for year in (today.year - 1, today.year, today.year + 1):
        for month, day in QUARTER_ENDS:
            end = dt.date(year, month, day)
            due = end + dt.timedelta(days=28)
            if due >= today:
                return end, due
    raise AssertionError("unreachable")


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _aed(value: Decimal, currency: str) -> str:
    return f"{currency} {value:,.0f}"


def build_briefing(db: Session, company_id: str, dashboard: dict[str, Any], today: dt.date,
                   lang: str = "en", currency: str = "AED", name: str | None = None,
                   receivables: dict[str, Any] | None = None) -> dict[str, Any]:
    # Past due by due date (AR aging), not the invoice status field, which
    # only counts invoices someone explicitly marked "Overdue".
    receivables = receivables or {}
    overdue_n, overdue_amt = int(receivables.get("customers_with_past_due") or 0), _dec(receivables.get("past_due"))
    pur = dashboard.get("purchase_summary") or {}
    unpaid_n = int(pur.get("pending_count") or 0)
    unpaid_amt = max(Decimal(0), _dec(pur.get("total")) - _dec(pur.get("paid")))
    hr = dashboard.get("hr_snapshot") or {}
    staff, present = int(hr.get("employee_count") or 0), int(hr.get("present_today") or 0)
    approvals = int(hr.get("pending_approvals") or 0)
    exceptions = db.query(ExceptionEvent).filter(ExceptionEvent.company_id == company_id, ExceptionEvent.status != "closed").count()
    period_end, due = next_vat_due(today)
    days = (due - today).days
    vat_payable = _dec((dashboard.get("kpis") or {}).get("vat_payable"))

    hour_greeting = "morning" if dt.datetime.now().hour < 12 else "afternoon" if dt.datetime.now().hour < 17 else "evening"
    items: list[dict[str, str]] = []
    if lang == "ar":
        greet = {"morning": "صباح الخير", "afternoon": "مساء الخير", "evening": "مساء الخير"}[hour_greeting]
        intro = f"{greet}{'، ' + name if name else ''}."
        if overdue_amt > 0:
            items.append({"key": "overdue", "page": "sales", "text": f"مبالغ متأخرة بقيمة {_aed(overdue_amt, currency)} لدى {overdue_n} من العملاء."})
        if unpaid_n:
            items.append({"key": "purchases", "page": "purchase", "text": f"{unpaid_n} مشتريات بانتظار الدفع بقيمة {_aed(unpaid_amt, currency)}."})
        due_txt = f"{due.day} {AR_MONTHS[due.month - 1]}"
        items.append({"key": "vat", "page": "reports", "text": (
            f"إقرار ضريبة القيمة المضافة مستحق اليوم." if days == 0 else f"إقرار ضريبة القيمة المضافة مستحق خلال {days} يومًا في {due_txt}.")
            + (f" الضريبة المستحقة حاليًا {_aed(vat_payable, currency)}." if vat_payable > 0 else
               f" يُتوقع استرداد ضريبي بقيمة {_aed(-vat_payable, currency)}." if vat_payable < 0 else "")})
        if staff:
            items.append({"key": "staff", "page": "hrms", "text": f"سجّل {present} من أصل {staff} موظفًا حضورهم اليوم." + (f" {approvals} طلبات بانتظار الموافقة." if approvals else "")})
        if exceptions:
            items.append({"key": "exceptions", "page": "exception", "text": f"{exceptions} استثناءات مفتوحة تحتاج إلى مراجعة."})
        if len(items) == 1:
            items.insert(0, {"key": "clear", "page": "dashboard", "text": "لا توجد فواتير متأخرة أو مشتريات غير مدفوعة."})
    else:
        intro = f"Good {hour_greeting}{', ' + name if name else ''}."
        if overdue_amt > 0:
            items.append({"key": "overdue", "page": "sales", "text": f"{_aed(overdue_amt, currency)} is past due across {overdue_n} customer{'s' if overdue_n != 1 else ''}."})
        if unpaid_n:
            items.append({"key": "purchases", "page": "purchase", "text": f"{unpaid_n} purchase{'s are' if unpaid_n != 1 else ' is'} waiting for payment, {_aed(unpaid_amt, currency)} in total."})
        items.append({"key": "vat", "page": "reports", "text": (
            "Your VAT return is due today." if days == 0 else
            f"Your VAT return for the quarter ending {period_end.strftime('%d %B')} is due in {days} day{'s' if days != 1 else ''}, on {due.strftime('%d %B')}.")
            + (f" VAT payable so far is {_aed(vat_payable, currency)}." if vat_payable > 0 else
               f" A VAT refund of {_aed(-vat_payable, currency)} is expected." if vat_payable < 0 else "")})
        if staff:
            items.append({"key": "staff", "page": "hrms", "text": f"{present} of {staff} staff have checked in today." + (f" {approvals} approval{'s are' if approvals != 1 else ' is'} waiting." if approvals else "")})
        if exceptions:
            items.append({"key": "exceptions", "page": "exception", "text": f"{exceptions} open exception{'s need' if exceptions != 1 else ' needs'} review."})
        if len(items) == 1:
            items.insert(0, {"key": "clear", "page": "dashboard", "text": "Nothing is overdue and no purchases are waiting for payment."})
    return {"intro": intro, "items": items, "text": " ".join([intro] + [i["text"] for i in items]), "lang": lang,
            "vat_due": due.isoformat()}
