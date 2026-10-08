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
    the per-ping check in hr_access.py never sees.

    One query finds the stale sessions: only open sessions checked in before the
    cutoff can be stale, and their latest ping comes from a grouped subquery (it
    used to be one query per open session across every company)."""
    from sqlalchemy import func, update

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=_STALE_PING_MINUTES)
        last_ping = (
            db.query(EmployeeLocationLog.session_id.label("session_id"), func.max(EmployeeLocationLog.created_at).label("at"))
            .join(AttendanceSession, AttendanceSession.id == EmployeeLocationLog.session_id)
            .filter(AttendanceSession.status == "open", AttendanceSession.check_in < cutoff)
            .group_by(EmployeeLocationLog.session_id)
            .subquery()
        )
        stale_ids = [
            sid
            for (sid,) in db.query(AttendanceSession.id)
            .outerjoin(last_ping, last_ping.c.session_id == AttendanceSession.id)
            .filter(
                AttendanceSession.status == "open",
                AttendanceSession.check_in < cutoff,
                func.coalesce(last_ping.c.at, AttendanceSession.check_in) < cutoff,
            )
        ]
        for i in range(0, len(stale_ids), 500):
            db.execute(
                update(AttendanceSession)
                .where(AttendanceSession.id.in_(stale_ids[i:i + 500]), AttendanceSession.status == "open")
                .values(check_out=now, status="closed", auto_checkout=True)
            )
        if stale_ids:
            db.commit()
        return len(stale_ids)
    finally:
        db.close()


@celery_app.task(name="hr.sync_biotime_devices")
def sync_biotime_devices() -> int:
    """Queues one sync task per active BioTime server, so only companies that have one
    are touched, a slow or unreachable server doesn't hold up the others, and the
    syncs spread across worker processes. Returns how many devices were queued."""
    db = SessionLocal()
    try:
        device_ids = [
            device_id
            for (device_id,) in db.query(BiometricDevice.id).filter(
                BiometricDevice.device_type == "ZKTeco BioTime Server",
                BiometricDevice.status == "active",
            )
        ]
    finally:
        db.close()
    for device_id in device_ids:
        sync_biotime_device.delay(device_id)
    return len(device_ids)


@celery_app.task(name="hr.sync_biotime_device")
def sync_biotime_device(device_id: str) -> int:
    """Pulls attendance from one company's own BioTime server
    (biotime_sync.sync_biotime_device) — never a pooled cross-company query."""
    from app import biotime_sync  # local import: keeps worker.py's import graph light

    db = SessionLocal()
    try:
        device = db.get(BiometricDevice, device_id)
        if not device or device.status != "active":
            return 0
        try:
            return biotime_sync.sync_biotime_device(db, device)
        except Exception as exc:
            db.rollback()
            logger.warning("BioTime sync failed for device %s (company %s): %s", device.id, device.company_id, exc)
            return 0
    finally:
        db.close()


_BACKUP_BATCH_SIZE = 50


def _platform_audit(db, action: str, detail: str) -> None:
    from app.models import AuditLog, Company

    sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
    if sa_company:
        db.add(AuditLog(company_id=sa_company.id, module="platform", action=action, detail=detail))
        db.commit()


def nightly_backup_key(date_str: str, company_id: str) -> str:
    return f"{_BACKUP_KEY_PREFIX}{date_str}/{company_id}.sql.gz"


@celery_app.task(name="backup.nightly_all_companies")
def nightly_all_companies_backup() -> str:
    """Nightly offsite copy of every company's SQL backup to S3/Spaces, on top of the
    managed Postgres provider's own snapshots.

    One gzip file per company under platform-backups/<date>/, written by
    backup.company_batch tasks of _BACKUP_BATCH_SIZE companies each, so the work
    spreads across worker processes and one failing company doesn't lose the rest.
    This task only queues the batches and prunes old backups. Returns the dated
    folder."""
    from sqlalchemy import or_

    from app import storage
    from app.models import Company

    db = SessionLocal()
    try:
        date_str = datetime.now(UTC).strftime("%Y%m%d")
        company_ids = [
            cid
            for (cid,) in db.query(Company.id)
            .filter(or_(Company.trn.is_(None), Company.trn != "SUPERADMIN-INTERNAL"))
            .order_by(Company.id)
        ]
        batches = [company_ids[i:i + _BACKUP_BATCH_SIZE] for i in range(0, len(company_ids), _BACKUP_BATCH_SIZE)]
        for batch in batches:
            backup_company_batch.delay(batch, date_str)
        try:
            deleted = storage.delete_old_backups(_BACKUP_KEY_PREFIX, _BACKUP_RETENTION_DAYS)
        except Exception as exc:
            deleted = []
            logger.error("Pruning old backups failed: %s", exc)
        detail = (
            f"Queued {len(company_ids)} companies in {len(batches)} batch(es) to {_BACKUP_KEY_PREFIX}{date_str}/; "
            f"pruned {len(deleted)} backup file(s) older than {_BACKUP_RETENTION_DAYS} days"
        )
        logger.info("Nightly backup: %s", detail)
        _platform_audit(db, "nightly_backup_queued", detail)
        return f"{_BACKUP_KEY_PREFIX}{date_str}/"
    finally:
        db.close()


@celery_app.task(name="backup.company_batch")
def backup_company_batch(company_ids: list[str], date_str: str) -> dict:
    """Backs up each company in the batch to its own gzip file. A failure is logged and
    audited per company; the rest of the batch carries on."""
    import gzip
    import os
    import tempfile

    from app import storage
    from app.routers.app_data import write_company_sql_dump

    done, failed = 0, []
    db = SessionLocal()
    try:
        for company_id in company_ids:
            tmp = tempfile.NamedTemporaryFile(prefix="taxflow-backup-", suffix=".sql.gz", delete=False)
            try:
                with tmp, gzip.GzipFile(fileobj=tmp, mode="wb") as gz:
                    write_company_sql_dump(db, company_id, "Automated nightly backup", gz)
                storage.upload_backup_file(nightly_backup_key(date_str, company_id), tmp.name, content_type="application/gzip")
                done += 1
            except Exception as exc:
                db.rollback()
                failed.append(company_id)
                logger.error("Nightly backup FAILED for company %s: %s", company_id, exc)
            finally:
                os.unlink(tmp.name)
                db.expunge_all()
        if failed:
            _platform_audit(db, "nightly_backup_failed", f"{date_str}: backup failed for {len(failed)} compan(ies): {', '.join(failed[:20])}")
        return {"done": done, "failed": failed}
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
