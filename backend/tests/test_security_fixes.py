"""Security review 2026-09-29: upload limits, security headers (CSP etc.) and
the legacy plain-text password column being wiped on startup."""
import app.routers.documents as documents
from sqlalchemy import inspect, text

from app.database import engine


def test_document_upload_rejects_script_capable_types(client, auth_headers):
    for name in ("page.html", "logo.svg", "run.js", "noext"):
        r = client.post("/api/v1/documents", headers=auth_headers, files={"file": (name, b"<script>x</script>", "text/html")})
        assert r.status_code == 415, (name, r.text)


def test_document_upload_size_limit(client, auth_headers, monkeypatch):
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 10)
    r = client.post("/api/v1/documents", headers=auth_headers, files={"file": ("big.pdf", b"x" * 11, "application/pdf")})
    assert r.status_code == 413
    ok = client.post("/api/v1/documents", headers=auth_headers, files={"file": ("small.pdf", b"x" * 10, "application/pdf")})
    assert ok.status_code == 201, ok.text


def test_attendance_csv_import_size_limit(client, auth_headers):
    big = b"employee_id,punch_time\n" + b"E1,2026-09-01 09:00:00\n" * 240000  # > 5 MB
    r = client.post("/api/v1/attendance/import-csv", headers=auth_headers, files={"file": ("a.csv", big, "text/csv")})
    assert r.status_code == 413


def test_security_headers_present(client):
    r = client.get("/health")
    h = r.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in h["Permissions-Policy"] and "geolocation=(self)" in h["Permissions-Policy"]
    csp = h["Content-Security-Policy"]
    for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "https://fonts.gstatic.com"):
        assert directive in csp


def test_legacy_plaintext_passwords_are_wiped_on_startup():
    from app.main import ensure_schema_updates

    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("users")}
        if "password_plain" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_plain VARCHAR(255)"))
        conn.execute(text("UPDATE users SET password_plain = 'hunter2'"))
    ensure_schema_updates()
    with engine.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM users WHERE password_plain IS NOT NULL")).scalar()
    assert left == 0
