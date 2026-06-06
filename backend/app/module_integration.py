from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.accounting_posting import post_source_transaction
from app.models import (
    CorporateTaxRecord,
    Invoice,
    PostingJob,
    SourceTransaction,
    SourceTransactionLine,
)


def money(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def sync_sales_invoice_accounting(db: Session, invoice: Invoice, user_id: str | None = None) -> SourceTransaction:
    lines = [
        {
            "description": line.description,
            "account_code": "3000",
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "vat_rate": line.vat_rate,
        }
        for line in invoice.lines
    ]
    tx = upsert_source_transaction(
        db,
        company_id=invoice.company_id,
        module="sales",
        reference=invoice.invoice_number,
        party_name=invoice.customer_name,
        subtotal=invoice.subtotal,
        vat=invoice.vat,
        total=invoice.total,
        lines=lines,
        default_account_code="3000",
    )
    approve_and_post_source(db, tx, user_id)
    refresh_corporate_tax_from_posted_sources(db, invoice.company_id)
    return tx


def sync_purchase_accounting(
    db: Session,
    company_id: str,
    reference: str,
    party_name: str,
    subtotal: Decimal,
    vat: Decimal,
    total: Decimal,
    lines: list[dict[str, Any]] | None = None,
    user_id: str | None = None,
) -> SourceTransaction:
    tx = upsert_source_transaction(
        db,
        company_id=company_id,
        module="purchase",
        reference=reference,
        party_name=party_name,
        subtotal=subtotal,
        vat=vat,
        total=total,
        lines=lines,
        default_account_code="4000",
    )
    approve_and_post_source(db, tx, user_id)
    refresh_corporate_tax_from_posted_sources(db, company_id)
    return tx


def upsert_source_transaction(
    db: Session,
    company_id: str,
    module: str,
    reference: str,
    party_name: str,
    subtotal: Decimal,
    vat: Decimal,
    total: Decimal,
    lines: list[dict[str, Any]] | None,
    default_account_code: str,
) -> SourceTransaction:
    tx = (
        db.query(SourceTransaction)
        .filter(
            SourceTransaction.company_id == company_id,
            SourceTransaction.module == module,
            SourceTransaction.reference == reference,
        )
        .first()
    )
    if not tx:
        tx = SourceTransaction(company_id=company_id, module=module, reference=reference)
        db.add(tx)
        db.flush()
    tx.party_name = party_name
    tx.subtotal = money(subtotal)
    tx.vat = money(vat)
    tx.total = money(total)
    tx.status = "approved"

    db.query(SourceTransactionLine).filter(SourceTransactionLine.source_id == tx.id).delete(synchronize_session=False)
    db.flush()
    normalized_lines = lines if isinstance(lines, list) and lines else [
        {
            "description": reference,
            "account_code": default_account_code,
            "quantity": Decimal("1"),
            "unit_price": subtotal,
            "vat_rate": Decimal("5") if money(vat) else Decimal("0"),
        }
    ]
    for raw_line in normalized_lines:
        quantity = money(raw_line.get("quantity") or raw_line.get("qty") or 1)
        unit_price = money(
            raw_line.get("unit_price")
            or raw_line.get("unit_cost_before_tax")
            or raw_line.get("unit_cost")
            or raw_line.get("price")
            or raw_line.get("cost")
        )
        amount = money(raw_line.get("amount") or raw_line.get("line_total") or (quantity * unit_price))
        vat_rate = money(raw_line.get("vat_rate") or raw_line.get("tax_rate") or (Decimal("5") if money(vat) else Decimal("0")))
        db.add(
            SourceTransactionLine(
                source_id=tx.id,
                description=str(raw_line.get("description") or raw_line.get("product") or raw_line.get("product_name") or reference)[:255],
                account_code=str(raw_line.get("account_code") or default_account_code),
                quantity=quantity if quantity > 0 else Decimal("1.00"),
                unit_price=unit_price,
                vat_rate=vat_rate,
                amount=amount,
                vat_amount=amount * (vat_rate / Decimal("100")),
            )
        )
    return tx


def approve_and_post_source(db: Session, tx: SourceTransaction, user_id: str | None) -> PostingJob:
    existing_job = (
        db.query(PostingJob)
        .filter(PostingJob.company_id == tx.company_id, PostingJob.source_id == tx.id, PostingJob.status == "posted")
        .first()
    )
    if existing_job:
        tx.status = "posted"
        return existing_job
    tx.status = "approved"
    tx.approved_by = user_id
    tx.approved_at = datetime.now(timezone.utc)
    job = PostingJob(company_id=tx.company_id, source_id=tx.id, status="queued")
    db.add(job)
    db.flush()
    post_source_transaction(db, job, user_id)
    db.flush()
    return job


def refresh_corporate_tax_from_posted_sources(db: Session, company_id: str, period: str | None = None) -> CorporateTaxRecord:
    period = period or datetime.now(timezone.utc).strftime("%Y")
    sales = money(
        db.query(func.coalesce(func.sum(SourceTransaction.subtotal), 0))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["sales", "sales_invoice"]), SourceTransaction.status == "posted")
        .scalar()
    )
    purchases = money(
        db.query(func.coalesce(func.sum(SourceTransaction.subtotal), 0))
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.module.in_(["purchase", "purchase_bill"]), SourceTransaction.status == "posted")
        .scalar()
    )
    accounting_profit = sales - purchases
    taxable_income = max(Decimal("0.00"), accounting_profit)
    tax_due = max(Decimal("0.00"), taxable_income - Decimal("375000.00")) * Decimal("0.09")
    record = (
        db.query(CorporateTaxRecord)
        .filter(CorporateTaxRecord.company_id == company_id, CorporateTaxRecord.period == period)
        .first()
    )
    if not record:
        record = CorporateTaxRecord(company_id=company_id, period=period)
        db.add(record)
    record.accounting_profit = money(accounting_profit)
    record.tax_adjustments = Decimal("0.00")
    record.taxable_income = money(taxable_income)
    record.tax_due = money(tax_due)
    record.status = "calculated"
    return record
