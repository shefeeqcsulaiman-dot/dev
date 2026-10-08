"""Slow work (AI invoice reading, batch uploads) as background jobs, so the request that
starts it returns at once instead of running into the load balancer's request timeout.

start() saves a Job row and runs the work on a thread pool in this server process, in
its own database session, with the caller's Principal rebuilt from their login token.
The browser polls GET /api/v1/app-data/jobs/{id} until the job is completed or failed.

Threads, not Celery: the work is mostly waiting on the AI provider, and production has
no Redis broker today. A job lost to a restart (deploy, worker recycling) is reported as
failed once it has gone STALE_MINUTES without finishing, instead of "running" forever.
With CELERY_TASK_ALWAYS_EAGER (tests, local dev) the work runs inline before start()
returns, so results are deterministic.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Job, JobStatus

log = logging.getLogger("taxflow.background")

STALE_MINUTES = 30
_MAX_THREADS = 4
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

# fn(db, principal) -> JSON-serialisable result
Work = Callable[[Session, Any], Any]


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=_MAX_THREADS, thread_name_prefix="taxflow-job")
        return _executor


def start(db: Session, company_id: str, kind: str, token: str, work: Work) -> Job:
    job = Job(company_id=company_id, kind=kind, status=JobStatus.queued.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    if get_settings().celery_task_always_eager:
        _run(job.id, token, work)
        db.refresh(job)
    else:
        _pool().submit(_run, job.id, token, work)
    return job


HEARTBEAT_SECONDS = 60


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    """Touch the running job's updated_at every HEARTBEAT_SECONDS, so a long batch isn't
    mistaken for one lost to a restart (view() treats STALE_MINUTES of silence as lost)."""
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with SessionLocal() as db:
                db.query(Job).filter(Job.id == job_id, Job.status == JobStatus.running.value).update(
                    {Job.updated_at: datetime.now(UTC)}, synchronize_session=False
                )
                db.commit()
        except Exception:  # noqa: BLE001 -- a missed beat only matters after STALE_MINUTES
            log.warning("heartbeat for job %s failed", job_id, exc_info=True)


def _run(job_id: str, token: str, work: Work) -> None:
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True, name=f"taxflow-job-beat-{job_id[:8]}")
    beat.start()
    try:
        _run_work(job_id, token, work)
    finally:
        stop.set()


def _run_work(job_id: str, token: str, work: Work) -> None:
    from app.auth_principal import principal_from_token

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.running.value
        db.commit()
        try:
            principal = principal_from_token(token, db)
            result = work(db, principal)
            job = db.get(Job, job_id)
            job.status = JobStatus.completed.value
            job.result = json.dumps(result, default=str)
            db.commit()
        except Exception as exc:  # noqa: BLE001 -- recorded on the job for the browser
            db.rollback()
            log.exception("background job %s (%s) failed", job_id, getattr(job, "kind", "?"))
            job = db.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.failed.value
                job.result = json.dumps({"error": _message(exc)})
                db.commit()


def _message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail or exc) or type(exc).__name__


def view(job: Job) -> dict[str, Any]:
    """What GET /app-data/jobs/{id} returns."""
    status = job.status
    result: Any = None
    error = None
    if job.result:
        try:
            result = json.loads(job.result)
        except ValueError:
            result = None
    if status == JobStatus.failed.value:
        error = (result or {}).get("error") if isinstance(result, dict) else None
        result = None
    elif status in (JobStatus.queued.value, JobStatus.running.value):
        updated = job.updated_at or job.created_at
        if updated is not None:
            updated = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
            if datetime.now(UTC) - updated > timedelta(minutes=STALE_MINUTES):
                status, error = JobStatus.failed.value, "The job was interrupted (the server restarted). Please try again."
    return {"id": job.id, "kind": job.kind, "status": status, "result": result, "error": error}
