"""AI extraction hardening: module/permission gate on the billed extract calls, real AI
error reasons instead of "no data found", computed confidence, and invoice dates
normalised to ISO (so the period lock can read them)."""
import base64
import io
import urllib.error
from uuid import uuid4

from PIL import Image

import app.routers.app_data as app_data
from tests.test_ess_requests import _company_id, _ess_login
from tests.test_module_permissions import _make_restricted_company


def _png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _csv_b64(text):
    return "data:text/csv;base64," + base64.b64encode(text.encode()).decode()


def _post(client, headers, action, body):
    return client.post("/api/v1/app-data", headers=headers, params={"action": action}, json=body)


# ── gate ─────────────────────────────────────────────────────────────────────

def test_extraction_blocked_when_module_disabled(client, db, monkeypatch):
    monkeypatch.setattr(app_data, "ingest_purchase_document", lambda *a: (_ for _ in ()).throw(AssertionError("AI must not run")))
    monkeypatch.setattr(app_data, "ingest_sales_invoice_document", lambda *a: (_ for _ in ()).throw(AssertionError("AI must not run")))
    _, headers, _ = _make_restricted_company(client, db, f"aigate-{uuid4().hex[:6]}", ["hr"])
    file = {"name": "x.png", "base64": _png_b64()}
    assert _post(client, headers, "documents.extract", {"file": file}).status_code == 403
    assert _post(client, headers, "documents.extract", {"file": file, "documentType": "expense"}).status_code == 403
    assert _post(client, headers, "invoices.import", {"file": file}).status_code == 403


def test_extraction_allowed_for_the_enabled_module_only(client, db):
    _, headers, _ = _make_restricted_company(client, db, f"aigate-{uuid4().hex[:6]}", ["purchase"])
    csv = _csv_b64("Invoice No,Supplier,Date,Product,Qty,Unit Price\nG-1,Gate Supplier,2026-09-01,Item,1,10\n")
    assert _post(client, headers, "documents.extract", {"file": {"name": "p.csv", "base64": csv}}).status_code == 200
    assert _post(client, headers, "invoices.import", {"file": {"name": "s.csv", "base64": csv}}).status_code == 403


def test_employee_portal_login_cannot_run_extraction(client, db, auth_headers):
    _, ess_headers = _ess_login(client, db, auth_headers, _company_id(client, auth_headers), f"AIG-{uuid4().hex[:5]}", f"aig.{uuid4().hex[:6]}")
    assert _post(client, ess_headers, "documents.extract", {"file": {"name": "x.png", "base64": _png_b64()}}).status_code == 403


# ── real error reasons ──────────────────────────────────────────────────────

def _rate_limited(*_a, **_k):
    raise urllib.error.HTTPError("https://api.anthropic.com/v1/messages", 429, "Too Many Requests", {}, None)


def test_purchase_ai_error_reason_is_shown(client, auth_headers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(app_data, "call_claude_invoice_extractor", _rate_limited)
    monkeypatch.setattr(app_data, "extract_image_text_with_tesseract", lambda *a: "")
    r = _post(client, auth_headers, "documents.extract", {"file": {"name": "busy.png", "base64": _png_b64()}})
    assert r.status_code == 200, r.text
    inv = r.json()["invoices"][0]
    assert inv.get("extraction_error")
    assert "rate limit" in inv["issues"], inv["issues"]


def test_sales_ai_error_reason_is_shown(client, auth_headers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(app_data, "_claude_messages", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.HTTPError("https://api.anthropic.com/v1/messages", 401, "Unauthorized", {}, None)))
    r = _post(client, auth_headers, "invoices.import", {"file": {"name": "s.png", "base64": _png_b64()}})
    inv = r.json()["invoices"][0]
    assert inv.get("extraction_error")
    assert "API key" in inv["error_message"], inv["error_message"]


# ── confidence ──────────────────────────────────────────────────────────────

def test_confidence_reflects_what_was_extracted():
    good = dict(invoice_no_found=True, party="Acme LLC", date="2026-09-01", subtotal=100, vat=5, total=105, line_count=1, lines_sum=100)
    assert app_data.extraction_confidence(**good) == 100
    assert app_data.extraction_confidence(**{**good, "invoice_no_found": False}) == 70          # one problem: still usable
    weak = {**good, "invoice_no_found": False, "party": "", "total": 300}                     # several problems
    assert app_data.extraction_confidence(**weak) < 70


def test_sales_ai_confidence_is_computed_not_fixed(client, auth_headers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reply = '{"invoice_number": "", "customer": "", "invoice_date": "", "subtotal_excl_vat": "100", "vat_amount": "5", "total_payable": "300", "line_items": []}'
    monkeypatch.setattr(app_data, "_claude_messages", lambda *a, **k: {"content": [{"text": reply}]})
    inv = _post(client, auth_headers, "invoices.import", {"file": {"name": "blurry.png", "base64": _png_b64()}}).json()["invoices"][0]
    assert inv["confidence"] < 70, inv


# ── dates ───────────────────────────────────────────────────────────────────

def test_imported_dates_become_iso(client, auth_headers):
    tag = uuid4().hex[:6]
    p = _post(client, auth_headers, "documents.extract", {"file": {"name": "p.csv", "base64": _csv_b64(
        f"Invoice No,Supplier,Date,Product,Qty,Unit Price\nPD-{tag},Date Supplier,20-02-2023,Item,1,10\n")}}).json()["invoices"][0]
    assert p["date"] == "2023-02-20", p["date"]
    s = _post(client, auth_headers, "invoices.import", {"file": {"name": "s.csv", "base64": _csv_b64(
        f"invoice_no,customer,date,description,qty,unit_price\nSD-{tag},Date Customer,20/02/2023,Item,1,10\n")}}).json()["invoices"][0]
    assert s["date"] == "2023-02-20", s["date"]


def test_period_lock_applies_to_non_iso_dates(client, auth_headers):
    r = client.post("/api/v1/period-locks", headers=auth_headers,
                    json={"module": "purchase", "period": "2019-03", "status": "locked", "reason": "Filed"})
    assert r.status_code in (200, 201), r.text
    blocked = _post(client, auth_headers, "save", {"collection": "purchaseRecords", "record": {
        "ref": f"PL-{uuid4().hex[:6]}", "supplier": "Lock Supplier", "date": "15-03-2019", "total": 10}})
    assert blocked.status_code == 423, blocked.text
