from datetime import UTC, datetime, timedelta
from time import sleep

from celery import Celery

from app.config import get_settings
from app.database import SessionLocal
from app.models import AttendanceSession, EmployeeLocationLog, Job, JobStatus


settings = get_settings()
result_backend = "cache+memory://" if settings.redis_url == "memory://" else settings.redis_url
celery_app = Celery("taxflow", broker=settings.redis_url, backend=result_backend)
celery_app.conf.task_always_eager = settings.celery_task_always_eager
celery_app.conf.task_eager_propagates = True

# Sweeps every 5 minutes for sessions with no recent GPS ping (client killed,
# network lost, battery died) — the inline check in hr_access.ping_location
# only catches employees who keep pinging while walking away. Requires a
# Celery beat process running app.worker as the schedule source; the API
# process alone does not execute this on a timer.
celery_app.conf.beat_schedule = {
    "hr-auto-checkout-stale-sessions": {
        "task": "hr.auto_checkout_stale_sessions",
        "schedule": 300.0,
    },
}

_STALE_PING_MINUTES = 10  # grace (5min) + one missed ~2min ping cycle + margin


@celery_app.task(name="hr.auto_checkout_stale_sessions")
def auto_checkout_stale_sessions() -> int:
    """Auto-closes open attendance sessions that have received no GPS ping
    for _STALE_PING_MINUTES — covers dropped connectivity/killed apps that
    the per-ping check in hr_access.py never sees."""
    db = SessionLocal()
    closed = 0
    try:
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=_STALE_PING_MINUTES)
        open_sessions = db.query(AttendanceSession).filter(AttendanceSession.status == "open").all()
        for session in open_sessions:
            latest = (
                db.query(EmployeeLocationLog)
                .filter(EmployeeLocationLog.session_id == session.id)
                .order_by(EmployeeLocationLog.created_at.desc())
                .first()
            )
            reference = latest.created_at if latest else session.check_in
            reference = reference if reference.tzinfo else reference.replace(tzinfo=UTC)
            if reference < cutoff:
                session.check_out = now
                session.status = "closed"
                session.auto_checkout = True
                db.add(session)
                closed += 1
        if closed:
            db.commit()
        return closed
    finally:
        db.close()


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
