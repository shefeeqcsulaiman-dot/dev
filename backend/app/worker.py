import logging
from datetime import UTC, datetime, timedelta
from time import sleep

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.database import SessionLocal
from app.models import AttendanceSession, BiometricDevice, EmployeeLocationLog, Job, JobStatus

logger = logging.getLogger("taxflow.worker")


def celery_redis_url(url: str) -> str:
    """Managed Redis (DigitalOcean) gives a TLS rediss:// URL; Celery's Redis backend refuses
    one without ssl_cert_reqs, so add the safe default (verify the certificate) when missing."""
    if url.startswith("rediss://") and "ssl_cert_reqs=" not in url:
        return url + ("&" if "?" in url else "?") + "ssl_cert_reqs=required"
    return url


settings = get_settings()
broker_url = celery_redis_url(settings.redis_url)
result_backend = "cache+memory://" if settings.redis_url == "memory://" else broker_url
celery_app = Celery("taxflow", broker=broker_url, backend=result_backend)
celery_app.conf.task_always_eager = settings.celery_task_always_eager
celery_app.conf.task_eager_propagates = True

# Sweeps every 5 minutes for GPS sessions left open far too long (a forgotten check-out).
# ESS is a web page: it can only send location pings while it is open, so a missing ping
# does NOT mean the employee left -- they usually just closed the app. Leaving the site
# with the app open is caught by hr_access.ping_location instead. Requires a Celery beat
# process running app.worker as the schedule source; the API process alone does not run it.
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
    # After the nightly backup: permanently deletes companies 7 days in the recycle bin.
    "purge-recycle-bin": {
        "task": "companies.purge_recycle_bin",
        "schedule": crontab(hour=3, minute=0),
    },
    "ess-cleanup-refresh-tokens": {
        "task": "ess.cleanup_refresh_tokens",
        "schedule": crontab(hour=3, minute=30),
    },
}

_STALE_SESSION_HOURS = 16  # longer than any shift: the check-out was forgotten
_BACKUP_RETENTION_DAYS = 30
_BACKUP_KEY_PREFIX = "platform-backups/"


def _checkout_sessions_left_outside(db, now) -> int:
    """Open GPS sessions whose latest pings were all outside the radius for the grace period
    are closed at the moment the employee was first seen outside (same rule as the live
    ping, hr_access._maybe_auto_checkout). Covers a phone that stopped sending after leaving."""
    from app.models import AttendanceSession, CompanyLocation, Employee
    from app.routers.hr_access import _AUTO_CHECKOUT_GRACE_MINUTES, _maybe_auto_checkout, _record_gps_punch

    from sqlalchemy import and_, func

    closed = 0
    cutoff = now - timedelta(minutes=_AUTO_CHECKOUT_GRACE_MINUTES)
    # Only sessions whose most recent ping was outside: one indexed query, not a scan of
    # every open session's log.
    last_ping = (
        db.query(EmployeeLocationLog.session_id.label("session_id"), func.max(EmployeeLocationLog.created_at).label("at"))
        .join(AttendanceSession, AttendanceSession.id == EmployeeLocationLog.session_id)
        .filter(AttendanceSession.status == "open")
        .group_by(EmployeeLocationLog.session_id)
        .subquery()
    )
    sessions = (
        db.query(AttendanceSession)
        .join(last_ping, last_ping.c.session_id == AttendanceSession.id)
        .join(EmployeeLocationLog, and_(EmployeeLocationLog.session_id == last_ping.c.session_id,
                                        EmployeeLocationLog.created_at == last_ping.c.at))
        .filter(AttendanceSession.status == "open", AttendanceSession.check_in < cutoff,
                EmployeeLocationLog.inside_geofence.is_(False))
        .all()
    )
    for session in sessions:
        try:
            if _maybe_auto_checkout(db, session):
                emp = db.get(Employee, session.employee_id)
                loc = db.get(CompanyLocation, session.location_id) if session.location_id else None
                if emp:
                    _record_gps_punch(db, emp, session.check_out, "out", loc.location_name if loc else None)
                closed += 1
        except Exception:
            db.rollback()
            logger.exception("Geofence check-out failed for session %s", session.id)
    return closed


@celery_app.task(name="hr.auto_checkout_stale_sessions")
def auto_checkout_stale_sessions() -> int:
    """Closes GPS sessions with no ping for _STALE_SESSION_HOURS, so a forgotten check-out
    doesn't block the next day's check-in. It used to close any session silent for 10
    minutes -- i.e. everyone who checked in and then closed ESS -- who then couldn't check
    out. No check-out time is invented for attendance: the day keeps its check-in and shows
    "No check-out", which the employee fixes with a correction request."""
    from sqlalchemy import func, update

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        try:
            left = _checkout_sessions_left_outside(db, now)
            if left:
                logger.info("Checked out %s GPS session(s) last seen outside their work location", left)
        except Exception:
            db.rollback()
            logger.exception("Geofence check-out sweep failed")
        cutoff = now - timedelta(hours=_STALE_SESSION_HOURS)
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


# Signed-out ESS app sign-ins are kept a while so HR still sees them under Signed-in devices.
_REFRESH_TOKEN_KEEP_DAYS = 30


def delete_dead_refresh_tokens(db, now: datetime) -> int:
    """Removes ESS refresh tokens that can never work again: signed out over a month ago,
    or unused for ESS_REFRESH_IDLE_DAYS plus that month."""
    from sqlalchemy import or_

    from app.models import ESS_REFRESH_IDLE_DAYS, EssRefreshToken

    keep = timedelta(days=_REFRESH_TOKEN_KEEP_DAYS)
    count = (
        db.query(EssRefreshToken)
        .filter(or_(EssRefreshToken.revoked_at < now - keep,
                    EssRefreshToken.last_used_at < now - timedelta(days=ESS_REFRESH_IDLE_DAYS) - keep))
        .delete(synchronize_session=False)
    )
    db.commit()
    return count


@celery_app.task(name="ess.cleanup_refresh_tokens")
def cleanup_refresh_tokens() -> dict:
    db = SessionLocal()
    try:
        deleted = delete_dead_refresh_tokens(db, datetime.now(UTC))
        if deleted:
            logger.info("Deleted %s dead ESS refresh tokens", deleted)
        return {"deleted": deleted}
    finally:
        db.close()


@celery_app.task(name="companies.purge_recycle_bin")
def purge_recycle_bin() -> dict:
    """Permanently deletes each company that has been in Super Admin's recycle bin for
    RECYCLE_BIN_DAYS, taking one more backup just before. If that backup fails the company
    is kept (and audited) and tried again the next night."""
    from app.models import Company
    from app.routers.superadmin import RECYCLE_BIN_DAYS, backup_company_before_delete, purge_company

    db = SessionLocal()
    purged, kept = [], []
    try:
        cutoff = datetime.now(UTC) - timedelta(days=RECYCLE_BIN_DAYS)
        due = [cid for (cid,) in db.query(Company.id).filter(Company.deleted_at.isnot(None), Company.deleted_at <= cutoff)]
        for company_id in due:
            company = db.get(Company, company_id)
            label = f"{company.name} ({company_id})"
            try:
                key = backup_company_before_delete(db, company, "Final backup before permanent delete")
            except Exception as exc:
                db.rollback()
                kept.append(company_id)
                logger.error("Recycle bin: final backup FAILED for %s, not deleted: %s", label, exc)
                _platform_audit(db, "purge_company_skipped", f"{label}: final backup failed, kept in recycle bin ({exc})")
                continue
            try:
                purge_company(db, company)
                db.commit()
                purged.append(company_id)
                _platform_audit(db, "purge_company", f"{label}: permanently deleted after {RECYCLE_BIN_DAYS} days in recycle bin; final backup {key}")
            except Exception as exc:
                db.rollback()
                kept.append(company_id)
                logger.error("Recycle bin: purge FAILED for %s: %s", label, exc)
                _platform_audit(db, "purge_company_failed", f"{label}: {exc}")
            db.expunge_all()
        return {"purged": purged, "kept": kept}
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
