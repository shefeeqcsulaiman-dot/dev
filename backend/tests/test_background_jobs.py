"""Invoice reading as a background job (?background=1): the upload returns a job id at once
and GET /app-data/jobs/{id} gives the result, so a long batch can't run into the load
balancer's request timeout. Without the flag the actions answer in the request, as before."""
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import app.routers.app_data as app_data
from app import background
from app.models import Job
from tests.test_ai_extraction_hardening import _csv_b64, _png_b64
from tests.test_module_permissions import _make_restricted_company

CSV = "Invoice No,Supplier,Date,Product,Qty,Unit Price\nBG-1,Job Supplier,2026-09-01,Item,2,10\n"


def _start(client, headers, action, body):
    return client.post("/api/v1/app-data", headers=headers, params={"action": action, "background": "1"}, json=body)


def _wait(client, headers, job_id, timeout=20):
    deadline = time.time() + timeout
    while True:
        body = client.get(f"/api/v1/app-data/jobs/{job_id}", headers=headers).json()
        if body["status"] in ("completed", "failed") or time.time() > deadline:
            return body
        time.sleep(0.2)


def test_purchase_reading_runs_as_a_job(client, auth_headers):
    r = _start(client, auth_headers, "documents.extract", {"file": {"name": "p.csv", "base64": _csv_b64(CSV)}})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    body = _wait(client, auth_headers, job_id)
    assert body["status"] == "completed", body
    invoices = body["result"]["invoices"]
    assert [inv["invoice_no"] for inv in invoices] == ["BG-1"]
    # Same answer as the in-request path.
    direct = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "documents.extract"},
                         json={"file": {"name": "p.csv", "base64": _csv_b64(CSV)}}).json()
    assert [inv["invoice_no"] for inv in direct["invoices"]] == ["BG-1"]


def test_job_runs_on_a_thread_when_not_eager(client, auth_headers, monkeypatch):
    """Production path: the request returns before the work is done; the job finishes on
    the pool in its own session, with the caller rebuilt from their token."""
    monkeypatch.setattr(background.get_settings(), "celery_task_always_eager", False)
    started = []

    real = app_data.ingest_purchase_document

    def slow(db, principal, file):
        started.append(principal.company_id)
        time.sleep(0.5)
        return real(db, principal, file)

    monkeypatch.setattr(app_data, "ingest_purchase_document", slow)
    r = _start(client, auth_headers, "documents.extract", {"file": {"name": "p.csv", "base64": _csv_b64(CSV)}})
    assert r.status_code == 200 and r.json()["status"] in ("queued", "running"), r.text
    body = _wait(client, auth_headers, r.json()["job_id"])
    assert body["status"] == "completed", body
    assert body["result"]["invoices"][0]["invoice_no"] == "BG-1"
    assert started  # ran with a principal resolved inside the job


def test_failed_job_reports_the_error(client, auth_headers, monkeypatch):
    def boom(*_a):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(app_data, "ingest_sales_invoice_document", boom)
    r = _start(client, auth_headers, "invoices.import", {"file": {"name": "s.png", "base64": _png_b64()}})
    body = _wait(client, auth_headers, r.json()["job_id"])
    assert body["status"] == "failed" and "provider unavailable" in body["error"], body
    assert body["result"] is None


def test_jobs_are_private_to_their_company(client, db, auth_headers):
    r = _start(client, auth_headers, "documents.extract", {"file": {"name": "p.csv", "base64": _csv_b64(CSV)}})
    job_id = r.json()["job_id"]
    _, other, _ = _make_restricted_company(client, db, f"bgjob-{uuid4().hex[:4]}", ["purchase"])
    assert client.get(f"/api/v1/app-data/jobs/{job_id}", headers=other).status_code == 404
    assert client.get("/api/v1/app-data/jobs/nope", headers=auth_headers).status_code == 404


def test_module_gate_still_applies_to_jobs(client, db, monkeypatch):
    monkeypatch.setattr(app_data, "ingest_purchase_document", lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    _, headers, _ = _make_restricted_company(client, db, f"bggate-{uuid4().hex[:4]}", ["hr"])
    r = _start(client, headers, "documents.extract", {"file": {"name": "x.png", "base64": _png_b64()}})
    assert r.status_code == 403


def test_long_job_keeps_its_heartbeat(client, auth_headers, monkeypatch):
    """A job still working past the stale limit isn't reported as interrupted: it is
    touched every HEARTBEAT_SECONDS while it runs."""
    monkeypatch.setattr(background, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(background, "STALE_MINUTES", 0.01)  # 0.6 s
    monkeypatch.setattr(background.get_settings(), "celery_task_always_eager", False)
    real = app_data.ingest_purchase_document

    def slow(db, principal, file):
        time.sleep(1.5)
        return real(db, principal, file)

    monkeypatch.setattr(app_data, "ingest_purchase_document", slow)
    job_id = _start(client, auth_headers, "documents.extract", {"file": {"name": "p.csv", "base64": _csv_b64(CSV)}}).json()["job_id"]
    time.sleep(1.0)  # past the (shortened) stale limit, still working
    assert client.get(f"/api/v1/app-data/jobs/{job_id}", headers=auth_headers).json()["status"] == "running"
    assert _wait(client, auth_headers, job_id)["status"] == "completed"


def test_interrupted_job_is_reported_failed(db, auth_headers):
    from app.models import User

    company_id = db.query(User.company_id).filter(User.email == "qa-admin@taxflowqa.com").scalar()
    job = Job(company_id=company_id, kind="documents.extract", status="running")
    db.add(job)
    db.commit()
    assert background.view(job)["status"] == "running"
    job.updated_at = datetime.now(UTC) - timedelta(minutes=background.STALE_MINUTES + 1)
    db.commit()
    view = background.view(job)
    assert view["status"] == "failed" and "interrupted" in view["error"]
