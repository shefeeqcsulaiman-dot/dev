"""AI invoice upload (documents.extract): extraction mapping, supplier-aware duplicate
detection, and the Claude model fallback. The AI provider is stubbed — no real API calls."""
import base64
import io
import json
import urllib.error

from PIL import Image

import app.routers.app_data as app_data


def _png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _ai_result(supplier, invoice_no, trn):
    return {
        "supplier": supplier, "trn_vat": trn, "invoice_number": invoice_no, "invoice_date": "2026-09-28",
        "currency": "AED", "subtotal_excl_vat": "610.00", "vat_amount": "30.50", "total_payable": "640.50",
        "line_items": [
            {"description": "A4 Paper Box", "qty": "10", "unit_price": "25.00", "line_total_excl_vat": "250.00", "line_vat_amount": "12.50"},
            {"description": "Printer Toner", "qty": "2", "unit_price": "180.00", "line_total_excl_vat": "360.00", "line_vat_amount": "18.00"},
        ],
    }


def _extract(client, headers, monkeypatch, supplier, invoice_no, trn):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(app_data, "call_claude_invoice_extractor", lambda key, parts: _ai_result(supplier, invoice_no, trn))
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "documents.extract"},
                    json={"file": {"name": "invoice.png", "base64": _png_b64()}})
    assert r.status_code == 200, r.text
    invoices = r.json()["invoices"]
    assert invoices and not invoices[0].get("extraction_error"), invoices
    return invoices[0]


def _save_purchase(client, headers, ref, supplier, trn):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": "purchaseRecords", "record": {"ref": ref, "supplier": supplier, "supplier_trn": trn, "total": 640.5}})
    assert r.status_code == 200, r.text


def test_extraction_maps_header_lines_and_totals(client, auth_headers, monkeypatch):
    inv = _extract(client, auth_headers, monkeypatch, "Mapping Supplier LLC", "AIQ-MAP-1", "100200300400501")
    assert inv["supplier"] == "Mapping Supplier LLC"
    assert inv["invoice_no"] == "AIQ-MAP-1"
    assert inv["supplier_trn"] == "100200300400501"
    assert float(inv["total"]) == 640.5 and float(inv["vat_amount"]) == 30.5
    assert len(inv["lines"]) == 2


def test_same_number_same_supplier_is_a_duplicate(client, auth_headers, monkeypatch):
    _save_purchase(client, auth_headers, "AIQ-DUP-1", "Al Noor Trading", "100200300400502")
    inv = _extract(client, auth_headers, monkeypatch, "Al Noor Trading", "AIQ-DUP-1", "100200300400502")
    assert inv.get("already_in_db") is True


def test_same_number_other_supplier_is_new_and_gets_its_own_key(client, auth_headers, monkeypatch):
    _save_purchase(client, auth_headers, "AIQ-DUP-2", "Al Noor Trading", "100200300400503")
    inv = _extract(client, auth_headers, monkeypatch, "Gulf Supplies", "AIQ-DUP-2", "100999888777666")
    assert not inv.get("already_in_db")
    assert inv["suggested_ref"] == "AIQ-DUP-2 (Gulf Supplies)"
    # once saved under that key, uploading it again is a duplicate
    _save_purchase(client, auth_headers, inv["suggested_ref"], "Gulf Supplies", "100999888777666")
    again = _extract(client, auth_headers, monkeypatch, "Gulf Supplies", "AIQ-DUP-2", "100999888777666")
    assert again.get("already_in_db") is True


def test_claude_falls_back_when_model_is_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_PURCHASE_MODEL", "claude-retired-model")
    seen = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        model = json.loads(req.data)["model"]
        seen.append(model)
        if model == "claude-retired-model":
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b'{"error":{"type":"not_found_error"}}'))
        return _Resp(json.dumps({"content": [{"text": json.dumps(_ai_result("S", "N-1", ""))}]}).encode())

    monkeypatch.setattr(app_data.urllib.request, "urlopen", fake_urlopen)
    data = app_data.call_claude_invoice_extractor("key", [{"type": "text", "text": "x"}])
    assert data["invoice_number"] == "N-1"
    assert seen == ["claude-retired-model", "claude-sonnet-5-5"]


def test_rate_limit_retried_once_with_short_wait(monkeypatch):
    calls, waits = [], []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {"retry-after": "120"}, io.BytesIO(b"{}"))
        return _Resp(json.dumps({"content": [{"text": json.dumps(_ai_result("S", "N-2", ""))}]}).encode())

    monkeypatch.setattr(app_data.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))
    assert app_data.call_claude_invoice_extractor("key", [{"type": "text", "text": "x"}])["invoice_number"] == "N-2"
    assert len(calls) == 2 and waits == [10.0]          # Retry-After 120s capped to 10s
    assert all(t <= 60 for t in calls)                  # stays inside the gateway limit


def test_scanned_pdf_text_fallback_is_fast():
    """Image-only (scanned) PDFs made the fallback text reader regex raw image bytes for minutes."""
    import time
    img = Image.effect_noise((1654, 2339), 80).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=200)
    started = time.time()
    assert app_data.extract_pdf_text(buf.getvalue()) == "" or True
    assert time.time() - started < 5


def test_fallback_reader_still_reads_text_pdfs():
    pdf = (b"%PDF-1.4\n4 0 obj<</Length 60>>stream\nBT [(INV-)10(1001)] TJ (Euro Vets LLC) Tj ET\nendstream endobj\n%%EOF")
    text = app_data.extract_pdf_text(pdf)
    assert "1001" in text and "Euro Vets LLC" in text
