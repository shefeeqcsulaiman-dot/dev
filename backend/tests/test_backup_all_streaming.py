"""Super Admin "backup all companies" and the nightly backup build the zip in a temp file (not in
memory): the download must still be a complete zip, and no temp file may be left behind."""
import glob
import io
import os
import tempfile
import zipfile
from uuid import uuid4

from tests.test_module_permissions import _make_restricted_company


def _temp_backups():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "taxflow-backup-*.zip")))


def test_backup_all_downloads_every_company_and_cleans_up(client, db):
    tag = uuid4().hex[:6]
    company_id, _, sa = _make_restricted_company(client, db, f"bk-all-{tag}", ["sales", "backup"])
    before = _temp_backups()
    r = client.get("/api/v1/superadmin/companies/backup-all", headers=sa)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "taxflow-all-companies-backup-" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.startswith(f"Restricted Co bk-all-{tag}/") and n.endswith(".sql") for n in names), names
    assert _temp_backups() == before  # temp file removed after streaming


def test_nightly_backup_uploads_from_a_file(client, db, monkeypatch):
    from app import storage, worker

    uploaded = {}

    def fake_upload(key, path, content_type="application/zip"):
        with open(path, "rb") as f:
            uploaded[key] = zipfile.ZipFile(io.BytesIO(f.read())).namelist()
        return key

    monkeypatch.setattr(storage, "upload_backup_file", fake_upload)
    monkeypatch.setattr(storage, "delete_old_backups", lambda prefix, keep_days: [])
    before = _temp_backups()
    key = worker.nightly_all_companies_backup()
    assert key in uploaded and any(n.endswith(".sql") for n in uploaded[key])
    assert _temp_backups() == before
