from time import sleep

from celery import Celery

from app.config import get_settings
from app.database import SessionLocal
from app.models import Job, JobStatus


settings = get_settings()
result_backend = "cache+memory://" if settings.redis_url == "memory://" else settings.redis_url
celery_app = Celery("taxflow", broker=settings.redis_url, backend=result_backend)
celery_app.conf.task_always_eager = settings.celery_task_always_eager
celery_app.conf.task_eager_propagates = True


@celery_app.task(name="reports.generate_vat_summary")
def generate_vat_summary(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = JobStatus.running.value
        db.commit()
        sleep(2)
        job.status = JobStatus.completed.value
        job.result = "VAT summary is ready for review."
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(Job, job_id)
        if job:
            job.status = JobStatus.failed.value
            job.result = str(exc)
            db.commit()
        raise
    finally:
        db.close()
