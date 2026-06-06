from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.module_integration import sync_sales_invoice_accounting
from app.models import Invoice, InvoiceLine, User
from app.schemas import InvoiceCreate, InvoiceOut


router = APIRouter(prefix="/invoices", tags=["invoices"])


def calculate_totals(invoice: Invoice) -> None:
    subtotal = Decimal("0.00")
    vat = Decimal("0.00")
    for line in invoice.lines:
        line_total = line.quantity * line.unit_price
        subtotal += line_total
        vat += line_total * (line.vat_rate / Decimal("100"))
    invoice.subtotal = subtotal
    invoice.vat = vat
    invoice.total = subtotal + vat


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Invoice]:
    return (
        db.query(Invoice)
        .options(joinedload(Invoice.lines))
        .filter(Invoice.company_id == current_user.company_id)
        .order_by(Invoice.created_at.desc())
        .all()
    )


@router.post("", response_model=InvoiceOut, status_code=201)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invoice:
    existing = (
        db.query(Invoice.id)
        .filter(Invoice.company_id == current_user.company_id, Invoice.invoice_number == payload.invoice_number)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Invoice number already exists for this company")

    invoice = Invoice(
        company_id=current_user.company_id,
        customer_name=payload.customer_name,
        invoice_number=payload.invoice_number,
    )
    invoice.lines = [InvoiceLine(**line.model_dump()) for line in payload.lines]
    calculate_totals(invoice)
    db.add(invoice)
    db.flush()
    sync_sales_invoice_accounting(db, invoice, current_user.id)
    db.commit()
    db.refresh(invoice)
    return invoice
