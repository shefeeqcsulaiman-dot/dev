from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.auth_principal import resolve_active_branch
from app.database import get_db
from app.dependencies import Principal, get_current_principal, require_module
from app.module_integration import sync_sales_invoice_accounting
from app.models import Invoice, InvoiceLine
from app.schemas import InvoiceCreate, InvoiceOut


router = APIRouter(prefix="/invoices", tags=["invoices"], dependencies=[Depends(require_module("sales"))])


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
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[Invoice]:
    # Widened to Employee/branch principals + branch-filtered in Branch
    # Management Phase 6 — mirrors trial-balance's Phase 3 treatment
    # (Invoice.branch_id already existed since Phase 3; this endpoint just
    # hadn't been opened up to Employee tokens yet).
    query = db.query(Invoice).options(joinedload(Invoice.lines)).filter(Invoice.company_id == principal.company_id)
    if principal.can_cross_branch("sales"):
        # Admin, or an employee explicitly granted sales:view_all_branches —
        # not constrained to any particular branch; ?branch_id= here is an
        # explicit opt-in "show me just this one" choice, not a restriction.
        if branch_id:
            query = query.filter(Invoice.branch_id == branch_id)
    else:
        # Branch-locked employee (Phase 3: possibly one of SEVERAL branches
        # they're assigned to) — resolve_active_branch keeps them confined
        # to their own accessible set even if they pass an arbitrary
        # ?branch_id=, same "cannot escalate" guarantee as before Phase 3.
        active_branch = resolve_active_branch(principal, branch_id)
        if active_branch:
            query = query.filter((Invoice.branch_id == active_branch) | (Invoice.branch_id.is_(None)))
    return query.order_by(Invoice.created_at.desc()).all()


@router.post("", response_model=InvoiceOut, status_code=201)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> Invoice:
    existing = (
        db.query(Invoice.id)
        .filter(Invoice.company_id == principal.company_id, Invoice.invoice_number == payload.invoice_number)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Invoice number already exists for this company")

    invoice = Invoice(
        company_id=principal.company_id,
        branch_id=payload.branch_id or principal.branch_id,
        customer_name=payload.customer_name,
        invoice_number=payload.invoice_number,
    )
    invoice.lines = [InvoiceLine(**line.model_dump()) for line in payload.lines]
    calculate_totals(invoice)
    db.add(invoice)
    db.flush()
    sync_sales_invoice_accounting(db, invoice, principal.user.id if principal.user else None)
    db.commit()
    db.refresh(invoice)
    return invoice
