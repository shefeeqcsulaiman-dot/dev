from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import AppDataRecord, Employee, ExceptionEvent, Job, PostingJob, StockProductMapping, User
from app.schemas import ExceptionEventIn, ExceptionEventOut


router = APIRouter(prefix="/exceptions", tags=["exception center"])


def _event(module: str, category: str, severity: str, source_record: str | None, message: str, status: str = "open") -> dict[str, str | None]:
    return {
        "module": module,
        "category": category,
        "severity": severity,
        "source_record": source_record,
        "message": message,
        "status": status,
    }


@router.get("")
def list_exceptions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, object]:
    rows: list[dict[str, str | None]] = []

    for job in db.query(PostingJob).filter(PostingJob.company_id == current_user.company_id, PostingJob.status == "failed").all():
        rows.append(_event("Accounting", "Failed posting", "high", job.source_id, job.error_message or "Posting job failed"))

    for job in db.query(Job).filter(Job.company_id == current_user.company_id, Job.status == "failed").all():
        category = "OCR failure" if "extract" in job.kind.lower() or "ocr" in job.kind.lower() else "Failed job"
        rows.append(_event("Documents", category, "medium", job.id, job.result or f"{job.kind} failed"))

    for collection, module in (("salesInvoices", "Sales"), ("purchaseRecords", "Purchases")):
        keys = [
            key
            for (key,) in db.query(AppDataRecord.record_key)
            .filter(AppDataRecord.company_id == current_user.company_id, AppDataRecord.collection == collection, AppDataRecord.record_key.isnot(None))
            .all()
            if key
        ]
        for key, count in Counter(keys).items():
            if count > 1:
                rows.append(_event(module, "Duplicate invoice", "high", key, f"Invoice/reference {key} appears {count} times"))

    mappings = db.query(StockProductMapping).filter(StockProductMapping.company_id == current_user.company_id).all()
    for mapping in mappings:
        if not mapping.sales_account_code or not mapping.purchase_account_code or not mapping.inventory_account_code:
            rows.append(_event("Inventory", "Unmapped stock item", "medium", mapping.sku, f"{mapping.name} is missing stock/account mapping"))

    for employee in db.query(Employee).filter(Employee.company_id == current_user.company_id, Employee.status == "active", Employee.iban.is_(None)).all():
        rows.append(_event("Payroll", "Payroll error", "medium", employee.employee_no, f"{employee.full_name} is missing IBAN for WPS"))

    saved = (
        db.query(ExceptionEvent)
        .filter(ExceptionEvent.company_id == current_user.company_id, ExceptionEvent.status != "closed")
        .order_by(ExceptionEvent.created_at.desc())
        .all()
    )
    rows.extend(
        _event(row.module, row.category, row.severity, row.source_record, row.message, row.status)
        for row in saved
    )

    summary = {
        "open": len([row for row in rows if row["status"] != "closed"]),
        "high": len([row for row in rows if row["severity"] == "high"]),
        "medium": len([row for row in rows if row["severity"] == "medium"]),
        "low": len([row for row in rows if row["severity"] == "low"]),
    }
    return {"summary": summary, "exceptions": rows}


@router.post("", response_model=ExceptionEventOut, status_code=201)
def create_exception(
    payload: ExceptionEventIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExceptionEvent:
    event = ExceptionEvent(company_id=current_user.company_id, **payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
