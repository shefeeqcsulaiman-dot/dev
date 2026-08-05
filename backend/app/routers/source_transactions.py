from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.accounting_posting import PostingError, post_source_transaction, repost_source_transaction
from app.database import get_db
from app.dependencies import company_allows_module, get_current_user
from app.models import Account, AuditLog, PostingJob, SourceTransaction, SourceTransactionLine, User
from app.schemas import PostingJobOut, RepostByReferenceIn, SourceTransactionCreate, SourceTransactionOut


router = APIRouter(prefix="/source-transactions", tags=["source transactions"])


def recalc(transaction: SourceTransaction) -> None:
    subtotal = Decimal("0.00")
    vat = Decimal("0.00")
    for line in transaction.lines:
        line.amount = line.quantity * line.unit_price
        line.vat_amount = line.amount * (line.vat_rate / Decimal("100"))
        subtotal += line.amount
        vat += line.vat_amount
    transaction.subtotal = subtotal
    transaction.vat = vat
    transaction.total = subtotal + vat


@router.get("", response_model=list[SourceTransactionOut])
def list_sources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[SourceTransaction]:
    return (
        db.query(SourceTransaction)
        .options(joinedload(SourceTransaction.lines))
        .filter(SourceTransaction.company_id == current_user.company_id)
        .order_by(SourceTransaction.created_at.desc())
        .all()
    )


@router.post("", response_model=SourceTransactionOut, status_code=201)
def create_source(
    payload: SourceTransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceTransaction:
    # payload.module is caller-supplied free text (sales/purchase/pos/expense/...)
    # — the source-transactions router itself isn't module-specific, so the gate
    # has to inspect each record rather than the whole router (see require_module
    # in dependencies.py for the router-level version used elsewhere).
    if not company_allows_module(current_user.company.modules_enabled, payload.module):
        raise HTTPException(status_code=403, detail=f"The '{payload.module}' module is not enabled for your company")
    transaction = SourceTransaction(
        company_id=current_user.company_id,
        branch_id=payload.branch_id,
        module=payload.module,
        reference=payload.reference,
        party_name=payload.party_name,
    )
    transaction.lines = [SourceTransactionLine(**line.model_dump()) for line in payload.lines]
    recalc(transaction)
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


@router.post("/{source_id}/validate", response_model=SourceTransactionOut)
def validate_source(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceTransaction:
    transaction = (
        db.query(SourceTransaction)
        .options(joinedload(SourceTransaction.lines))
        .filter(SourceTransaction.id == source_id, SourceTransaction.company_id == current_user.company_id)
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Source transaction not found")
    account_codes = {line.account_code for line in transaction.lines}
    existing_codes = {
        row[0]
        for row in db.query(Account.code)
        .filter(Account.company_id == current_user.company_id, Account.code.in_(account_codes))
        .all()
    }
    missing = sorted(account_codes - existing_codes)
    if missing:
        transaction.status = "review"
        transaction.validation_result = f"Missing account mappings: {', '.join(missing)}"
    else:
        transaction.status = "validated"
        transaction.validation_result = "All line account mappings and tax calculations are valid"
    recalc(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


@router.post("/{source_id}/approve", response_model=PostingJobOut, status_code=202)
def approve_source(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostingJob:
    transaction = (
        db.query(SourceTransaction)
        .options(joinedload(SourceTransaction.lines))
        .filter(SourceTransaction.id == source_id, SourceTransaction.company_id == current_user.company_id)
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Source transaction not found")
    existing_job = (
        db.query(PostingJob)
        .filter(
            PostingJob.company_id == current_user.company_id,
            PostingJob.source_id == transaction.id,
            PostingJob.status == "posted",
        )
        .first()
    )
    if existing_job:
        return existing_job

    if transaction.status not in {"validated", "approved", "posted"}:
        validate_source(source_id, db, current_user)
        db.refresh(transaction)
    if transaction.status not in {"validated", "approved"}:
        raise HTTPException(status_code=422, detail=transaction.validation_result or "Source transaction is not valid")

    transaction.status = "approved"
    transaction.approved_by = current_user.id
    transaction.approved_at = datetime.now(timezone.utc)
    job = PostingJob(company_id=current_user.company_id, source_id=transaction.id, status="queued")
    db.add(job)
    db.flush()
    post_source_transaction(db, job, current_user.id)
    db.add(AuditLog(company_id=current_user.company_id, user_id=current_user.id, module=transaction.module, action="approved", record_id=transaction.id, detail=transaction.reference))
    db.commit()
    db.refresh(job)
    return job


@router.post("/repost-by-reference", response_model=SourceTransactionOut)
def repost_by_reference(
    payload: RepostByReferenceIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceTransaction:
    """Manual "Update Ledger" action — for a purchase/sale/expense that's
    already posted, but was edited afterward so the journal/GL no longer
    matches its current amounts. Looked up by (module, reference) rather
    than source_id since that's what the calling screens (Purchase edit,
    AI Extraction) already have on hand."""
    if not company_allows_module(current_user.company.modules_enabled, payload.module):
        raise HTTPException(status_code=403, detail=f"The '{payload.module}' module is not enabled for your company")
    transaction = (
        db.query(SourceTransaction)
        .options(joinedload(SourceTransaction.lines))
        .filter(
            SourceTransaction.company_id == current_user.company_id,
            SourceTransaction.module == payload.module,
            SourceTransaction.reference == payload.reference,
        )
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="No ledger-linked record found for this reference yet — save it first so it posts automatically.")
    if transaction.status != "posted":
        raise HTTPException(status_code=422, detail="This record hasn't posted to the ledger yet — save it first.")
    try:
        repost_source_transaction(db, transaction, current_user.id)
    except PostingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(transaction)
    return transaction
