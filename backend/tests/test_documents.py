"""upload_fileobj() (app/storage.py) used to build the local-storage save
path directly from the client-supplied filename with no sanitization —
"../../../etc/cron.d/x" would let an uploader write files outside
local-storage/, limited only by the running process's OS permissions. Fixed
by stripping any directory components before the filename touches a path."""
from pathlib import Path

from app.storage import LOCAL_STORAGE_ROOT


def test_document_upload_sanitizes_path_traversal_filename(client, auth_headers):
    malicious_name = "../../../../tmp/taxflow-traversal-test.txt"
    r = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": (malicious_name, b"malicious content", "text/plain")},
    )
    assert r.status_code == 201, r.text
    data = r.json()

    storage_key = data["storage_key"]
    assert ".." not in storage_key

    saved_path = Path(storage_key).resolve()
    root = LOCAL_STORAGE_ROOT.resolve()
    assert root in saved_path.parents, f"{saved_path} escaped {root}"
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"malicious content"

    # The traversal target itself must never have been created.
    escaped = (Path.cwd() / "tmp" / "taxflow-traversal-test.txt").resolve()
    assert not escaped.exists()


def test_document_upload_normal_filename_unaffected(client, auth_headers):
    r = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("invoice-scan.pdf", b"pdf bytes", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["filename"] == "invoice-scan.pdf"
    assert "invoice-scan.pdf" in data["storage_key"]
