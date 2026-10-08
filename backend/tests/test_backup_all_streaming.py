"""Nightly backup: one gzip SQL file per company under platform-backups/<date>/, written in
batches (backup.company_batch), built in temp files that are always removed. The old
"download every company as one zip" endpoint is gone: at thousands of companies it can't
finish inside a request."""
import glob
import gzip
import os
import tempfile
from uuid import uuid4

from tests.test_module_permissions import _make_restricted_company


def _temp_backups():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "taxflow-backup-*")))


def test_nightly_backup_writes_one_gzip_per_company_in_batches(client, db, monkeypatch):
    from app import storage, worker

    tag = uuid4().hex[:4]
    company_id, _, _ = _make_restricted_company(client, db, f"bkn-{tag}", ["sales", "backup"])
    uploaded = {}

    def fake_upload(key, path, content_type="application/zip"):
        with open(path, "rb") as f:
            uploaded[key] = (content_type, gzip.decompress(f.read()).decode("utf-8"))
        return key

    batches = []
    real_batch = worker.backup_company_batch.run

    def spy_batch(company_ids, date_str):
        batches.append(list(company_ids))
        return real_batch(company_ids, date_str)

    monkeypatch.setattr(storage, "upload_backup_file", fake_upload)
    monkeypatch.setattr(storage, "delete_old_backups", lambda prefix, keep_days: [])
    monkeypatch.setattr(worker.backup_company_batch, "run", spy_batch)
    monkeypatch.setattr(worker, "_BACKUP_BATCH_SIZE", 2)
    before = _temp_backups()

    folder = worker.nightly_all_companies_backup()

    assert folder.startswith("platform-backups/") and folder.endswith("/")
    key = f"{folder}{company_id}.sql.gz"
    assert key in uploaded
    content_type, sql = uploaded[key]
    assert content_type == "application/gzip"
    assert f"-- Company ID : {company_id}" in sql and "COMMIT;" in sql
    assert batches and all(len(b) <= 2 for b in batches)
    assert sum(len(b) for b in batches) == len(uploaded)
    from app.models import Company

    internal = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
    if internal:
        assert f"{folder}{internal.id}.sql.gz" not in uploaded
    assert _temp_backups() == before


def test_one_failing_company_does_not_stop_the_batch(client, db, monkeypatch):
    from app import storage, worker

    a, _, _ = _make_restricted_company(client, db, f"bkfa-{uuid4().hex[:4]}", ["sales"])
    b, _, _ = _make_restricted_company(client, db, f"bkfb-{uuid4().hex[:4]}", ["sales"])
    uploaded = []

    def flaky_upload(key, path, content_type="application/zip"):
        if a in key:
            raise RuntimeError("storage unavailable")
        uploaded.append(key)
        return key

    monkeypatch.setattr(storage, "upload_backup_file", flaky_upload)
    before = _temp_backups()
    result = worker.backup_company_batch([a, b], "20990101")
    assert result == {"done": 1, "failed": [a]}
    assert uploaded == ["platform-backups/20990101/" + b + ".sql.gz"]
    assert _temp_backups() == before


def test_backup_all_zip_endpoint_is_gone(client, db):
    from tests.test_superadmin_backup import _client_ref, _make_superadmin

    _client_ref["client"] = client
    r = client.get("/api/v1/superadmin/companies/backup-all", headers=_make_superadmin(db))
    assert r.status_code == 404
