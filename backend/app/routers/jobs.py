from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Job, User
from app.schemas import JobOut
from app.worker import generate_vat_summary


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.company_id == current_user.company_id)
        .order_by(Job.created_at.desc())
        .all()
    )


@router.post("/vat-summary", response_model=JobOut, status_code=202)
def queue_vat_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    job = Job(company_id=current_user.company_id, kind="vat_summary")
    db.add(job)
    db.commit()
    db.refresh(job)
    generate_vat_summary.delay(job.id)
    return job
