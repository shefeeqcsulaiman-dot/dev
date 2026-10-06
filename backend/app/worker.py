import logging
from datetime import UTC, datetime, timedelta
from time import sleep

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.database import SessionLocal
from app.models import AttendanceSession, BiometricDevice, EmployeeLocationLog, Job, JobStatus

logger = logging.getLogger("taxflow.worker")


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
    "hr-sync-biotime-devices": {
        "task": "hr.sync_biotime_devices",
        "schedule": 300.0,
    },
    # 02:00 UTC ~= 06:00 Gulf time, before business hours.
    "backup-nightly-all-companies": {
        "task": "backup.nightly_all_companies",
        "schedule": crontab(hour=2, minute=0),
    },
}

_STALE_PING_MINUTES = 10  # grace (5min) + one missed ~2min ping cycle + margin
_BACKUP_RETENTION_DAYS = 30
_BACKUP_KEY_PREFIX = "platform-backups/"


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


@celery_app.task(name="hr.sync_biotime_devices")
def sync_biotime_devices() -> int:
    """Pulls attendance from every company's own connected BioTime server —
    one isolated sync per device (biotime_sync.sync_biotime_device), never a
    pooled cross-company query. A failure on one company's server must not
    block another's, so each device is wrapped in its own try/except."""
    from app import biotime_sync  # local import: keeps worker.py's import
    # graph light for tasks that don't need it, matching this module's
    # existing pattern of not importing every router/service at top level.

    db = SessionLocal()
    total_synced = 0
    try:
        devices = db.query(BiometricDevice).filter(
            BiometricDevice.device_type == "ZKTeco BioTime Server",
            BiometricDevice.status == "active",
        ).all()
        for device in devices:
            try:
                total_synced += biotime_sync.sync_biotime_device(db, device)
            except Exception as exc:
                db.rollback()
                logger.warning("BioTime sync failed for device %s (company %s): %s", device.id, device.company_id, exc)
        return total_synced
    finally:
        db.close()


@celery_app.task(name="backup.nightly_all_companies")
def nightly_all_companies_backup() -> str:
    """Nightly offsite copy of every company's SQL backup to S3/Spaces,
    on top of the managed Postgres provider's own snapshots. Logs an
    AuditLog against the SUPERADMIN-INTERNAL sentinel company either way."""
    import os
    import tempfile

    from app import storage
    from app.models import AuditLog, Company
    from app.routers.app_data import write_all_companies_backup_zip

    db = SessionLocal()
    try:
        sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
        date_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        key = f"{_BACKUP_KEY_PREFIX}taxflow-all-companies-backup-{date_str}.zip"
        try:
            # Built in a temp file and uploaded from disk, not held in memory.
            tmp = tempfile.NamedTemporaryFile(prefix="taxflow-backup-", suffix=".zip", delete=False)
            try:
                with tmp:
                    write_all_companies_backup_zip(db, "Automated nightly backup", tmp)
                size = os.path.getsize(tmp.name)
                storage.upload_backup_file(key, tmp.name)
            finally:
                os.unlink(tmp.name)
            sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
            deleted = storage.delete_old_backups(_BACKUP_KEY_PREFIX, _BACKUP_RETENTION_DAYS)
            detail = f"Uploaded {key} ({size} bytes); pruned {len(deleted)} backup(s) older than {_BACKUP_RETENTION_DAYS} days"
            logger.info("Nightly all-companies backup succeeded: %s", detail)
            action = "nightly_backup_completed"
        except Exception as exc:
            detail = f"Nightly all-companies backup FAILED: {exc}"
            logger.error(detail)
            action = "nightly_backup_failed"
            if sa_company:
                db.add(AuditLog(company_id=sa_company.id, module="platform", action=action, detail=detail))
                db.commit()
            raise
        if sa_company:
            db.add(AuditLog(company_id=sa_company.id, module="platform", action=action, detail=detail))
            db.commit()
        return key
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
