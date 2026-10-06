"""Main app + HRMS voice endpoints (/ai/voice-settings, /ai/transcribe,
/ai/voice-intent, /ai/voice-draft, /ai/briefing) and the shared company voice
switch that the ESS voice endpoints also obey. The LLM and Whisper are stubbed."""
import io
import json
import uuid
from datetime import date

import pytest

import app.routers.ai_voice as ai_voice
import app.voice as voice_mod
from app.models import AppDataRecord, Company, Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _employee_headers(client, db, admin_headers, company_id, permission_keys=()):
    suffix = uuid.uuid4().hex[:8]
    emp = Employee(company_id=company_id, employee_no=f"AIV-{suffix}", full_name=f"Voice {suffix}", status="active")
    db.add(emp)
    db.commit()
    body = {"username": f"aiv.{suffix}", "password": "voice12345", "is_active": True}
    if permission_keys:
        r = client.post("/api/v1/hr/admin/roles", headers=admin_headers,
                        json={"role_name": f"Voice role {suffix}", "description": "t", "permission_keys": list(permission_keys)})
        assert r.status_code == 201, r.text
        body["role_id"] = r.json()["id"]
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers, json=body)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/ess/login", json={"username": body["username"], "password": "voice12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def v(client, db, auth_headers, monkeypatch):
    """AI module on, an AI key configured, clean voice settings, LLM stubbed via v['llm']."""
    company_id = _company_id(client, auth_headers)
    company = db.get(Company, company_id)
    saved_modules = company.modules_enabled
    company.modules_enabled = None
    db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == voice_mod.SETTINGS_COLLECTION).delete()
    db.commit()
    state = {"llm": {}, "prompts": [], "company_id": company_id, "headers": auth_headers}
    monkeypatch.setattr(voice_mod, "_openai_key", lambda: "sk-test")
    monkeypatch.setattr(voice_mod, "_anthropic_key", lambda: "")

    def fake_llm(prompt, system, **kwargs):
        state["prompts"].append((prompt, system))
        return state["llm"]

    monkeypatch.setattr(ai_voice, "call_llm", fake_llm)
    yield state
    db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == voice_mod.SETTINGS_COLLECTION).delete()
    company = db.get(Company, company_id)
    company.modules_enabled = saved_modules
    db.commit()


# ── settings ──

def test_settings_defaults_and_admin_save(client, v):
    r = client.get("/api/v1/ai/voice-settings", headers=v["headers"])
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "default_lang": "", "cloud_transcription": True,
                        "daily_transcriptions": 200, "transcriptions_today": 0, "available": True}

    r = client.put("/api/v1/ai/voice-settings", headers=v["headers"],
                   json={"enabled": True, "default_lang": "ar-AE", "cloud_transcription": False, "daily_transcriptions": 5})
    assert r.status_code == 200, r.text
    assert r.json()["default_lang"] == "ar-AE" and r.json()["daily_transcriptions"] == 5
    assert client.get("/api/v1/ai/voice-settings", headers=v["headers"]).json()["cloud_transcription"] is False


def test_settings_validation(client, v):
    bad = [{"enabled": True, "default_lang": "fr-FR"}, {"enabled": True, "daily_transcriptions": 10001}]
    for body in bad:
        assert client.put("/api/v1/ai/voice-settings", headers=v["headers"], json=body).status_code == 422


def test_non_admin_employee_can_read_but_not_change_settings(client, db, v):
    emp_headers = _employee_headers(client, db, v["headers"], v["company_id"])
    assert client.get("/api/v1/ai/voice-settings", headers=emp_headers).status_code == 200
    r = client.put("/api/v1/ai/voice-settings", headers=emp_headers, json={"enabled": False})
    assert r.status_code == 403


def test_company_switch_off_disables_main_and_ess_voice(client, db, v):
    client.put("/api/v1/ai/voice-settings", headers=v["headers"], json={"enabled": False})
    s = client.get("/api/v1/ai/voice-settings", headers=v["headers"]).json()
    assert s["enabled"] is False and s["available"] is False
    r = client.post("/api/v1/ai/voice-intent", headers=v["headers"], json={"transcript": "open sales", "targets": []})
    assert r.status_code == 403 and "isn't enabled" in r.json()["detail"]

    emp_headers = _employee_headers(client, db, v["headers"], v["company_id"])
    assert client.get("/api/v1/ess/voice-settings", headers=emp_headers).json()["enabled"] is False
    assert client.post("/api/v1/ess/voice-intent", headers=emp_headers, json={"transcript": "payslips"}).status_code == 403


def test_ess_uses_company_default_language(client, db, v):
    client.put("/api/v1/ai/voice-settings", headers=v["headers"], json={"enabled": True, "default_lang": "ar-AE"})
    emp_headers = _employee_headers(client, db, v["headers"], v["company_id"])
    assert client.get("/api/v1/ess/voice-settings", headers=emp_headers).json() == {"enabled": True, "default_lang": "ar-AE"}


def test_unavailable_without_ai_key(client, v, monkeypatch):
    monkeypatch.setattr(voice_mod, "_openai_key", lambda: "")
    s = client.get("/api/v1/ai/voice-settings", headers=v["headers"]).json()
    assert s["enabled"] is True and s["available"] is False


# ── voice commands ──

TARGETS = [
    {"id": "p1", "kind": "page", "label": "Sales & Invoices"},
    {"id": "t2", "kind": "tab", "label": "Reports › VAT 201"},
    {"id": "a3", "kind": "action", "label": "+ New Invoice", "alt": "Sales & Invoices"},
    {"id": "q4", "kind": "query", "label": "Daily briefing"},
]


def _intent(client, v, text="x", targets=TARGETS):
    r = client.post("/api/v1/ai/voice-intent", headers=v["headers"], json={"transcript": text, "lang": "en", "targets": targets})
    assert r.status_code == 200, r.text
    return r.json()


def test_intent_lists_targets_in_prompt(client, v):
    v["llm"] = {"intent": "navigate", "target": "p1", "confidence": 90}
    assert _intent(client, v, "open sales")["target"] == "p1"
    prompt = v["prompts"][-1][0]
    assert "a3 [action] + New Invoice (Sales & Invoices)" in prompt and "open sales" in prompt


def test_intent_resolves_decorated_ids_and_labels(client, v):
    v["llm"] = {"intent": "open_form", "target": "a3 [action] + New Invoice", "confidence": 85, "alternatives": ["Sales & Invoices", "zz9", "a3"]}
    r = _intent(client, v, "new invoice")
    assert r["target"] == "a3" and r["intent"] == "open_form"
    assert r["alternatives"] == ["p1"]  # label resolved, unknown id and the target itself dropped


def test_intent_kind_consistency(client, v):
    v["llm"] = {"intent": "navigate", "target": "q4", "confidence": 90}
    assert _intent(client, v)["intent"] == "query"  # a query target always runs as a query
    v["llm"] = {"intent": "open_form", "target": "t2", "confidence": 90}
    assert _intent(client, v)["intent"] == "navigate"  # open_form needs an action target
    v["llm"] = {"intent": "navigate", "target": "nope", "confidence": 99}
    r = _intent(client, v)
    assert r["target"] is None and r["confidence"] == 0


def test_intent_answer_keeps_question(client, v):
    v["llm"] = {"intent": "answer", "target": None, "query": None, "confidence": 80}
    r = _intent(client, v, "how much VAT do I owe")
    assert r["intent"] == "answer" and r["query"] == "how much VAT do I owe"


def test_intent_rejects_bad_payload(client, v):
    too_many = [{"id": f"p{i}", "kind": "page", "label": "x"} for i in range(401)]
    assert client.post("/api/v1/ai/voice-intent", headers=v["headers"], json={"transcript": "x", "targets": too_many}).status_code == 422
    assert client.post("/api/v1/ai/voice-intent", headers=v["headers"],
                       json={"transcript": "x", "targets": [{"id": "p1", "kind": "script", "label": "x"}]}).status_code == 422


def test_intent_llm_failure_is_502(client, v):
    v["llm"] = {"error": "boom"}
    r = client.post("/api/v1/ai/voice-intent", headers=v["headers"], json={"transcript": "x", "targets": TARGETS})
    assert r.status_code == 502


def test_hrms_employee_principal_can_use_voice(client, db, v):
    emp_headers = _employee_headers(client, db, v["headers"], v["company_id"])
    v["llm"] = {"intent": "navigate", "target": "p1", "confidence": 90}
    r = client.post("/api/v1/ai/voice-intent", headers=emp_headers, json={"transcript": "open sales", "targets": TARGETS})
    assert r.status_code == 200, r.text


# ── Dictate ──

def _draft(client, v, form, choices=None, transcript="x"):
    r = client.post("/api/v1/ai/voice-draft", headers=v["headers"],
                    json={"form": form, "transcript": transcript, "choices": choices or {}, "today": "2026-09-28"})
    assert r.status_code == 200, r.text
    return r.json()


def test_draft_sales_invoice_matches_choices_and_lines(client, v):
    v["llm"] = {"fields": {"customer": "al noor trading llc", "date": "2026-09-28", "due_date": "in 30 days", "po": "PO-7", "bogus": "x"},
                "lines": [{"product": "laptop", "quantity": "3", "price": "2,500"}, {"product": "Gaming Chair", "quantity": 0, "price": None}, {"product": ""}]}
    d = _draft(client, v, "sales_invoice", {"customer": ["Al Noor Trading LLC"], "product": ["Laptop"]}, "invoice al noor, 3 laptops")
    assert d["fields"] == {"customer": "Al Noor Trading LLC", "date": "2026-09-28", "po": "PO-7"}
    assert d["lines"] == [
        {"product": "Laptop", "quantity": 3.0, "price": 2500.0, "matched": "yes"},
        {"product": "Gaming Chair", "quantity": 1, "price": None, "matched": "no"},
    ]
    assert "Today is 2026-09-28" in v["prompts"][-1][1]


def test_draft_unmatched_choice_is_reported_not_filled(client, v):
    v["llm"] = {"fields": {"supplier": "Unknown Metals"}}
    d = _draft(client, v, "purchase", {"supplier": ["Gulf Steel"]}, "bought from unknown metals")
    assert d["fields"] == {} and d["unmatched"] == {"supplier": "Unknown Metals"}


def test_draft_customer_validates_trn_email(client, v):
    v["llm"] = {"fields": {"name": "Al Noor", "trn": "100-234-567-800-003", "email": "Info@AlNoor.ae ", "emirate": "Sharjah"}}
    assert _draft(client, v, "customer", {"emirate": ["Dubai", "Sharjah"]})["fields"] == {
        "name": "Al Noor", "trn": "100234567800003", "email": "info@alnoor.ae", "emirate": "Sharjah"}
    v["llm"] = {"fields": {"trn": "12345", "email": "not an email"}}
    assert _draft(client, v, "customer")["fields"] == {}


def test_draft_placeholder_choices_are_never_offered(client, v):
    v["llm"] = {"fields": {"vendor": "Please Select", "amount": "85", "vat": -1}}
    d = _draft(client, v, "expense", {"vendor": ["Please Select", "Careem"], "category": ["-- choose --", "Transport"]})
    system = v["prompts"][-1][1]
    assert "Please Select" not in system and "choose" not in system.split("Fields:")[1]
    assert d["fields"] == {"amount": 85.0} and d["unmatched"] == {}  # never filled, never offered


def test_draft_drops_party_names_the_user_never_said(client, v):
    v["llm"] = {"fields": {"vendor": "Vendor B", "category": "Transport", "amount": 85}}
    r = client.post("/api/v1/ai/voice-draft", headers=v["headers"], json={
        "form": "expense", "transcript": "Taxi to the client, 85 dirhams",
        "choices": {"vendor": ["Vendor B", "Careem"], "category": ["Transport"]}})
    assert r.json()["fields"] == {"category": "Transport", "amount": 85.0}  # category may be inferred, a vendor may not
    v["llm"] = {"fields": {"vendor": "Careem"}}
    r = client.post("/api/v1/ai/voice-draft", headers=v["headers"], json={
        "form": "expense", "transcript": "careem ride 40 dirhams", "choices": {"vendor": ["Vendor B", "Careem"]}})
    assert r.json()["fields"] == {"vendor": "Careem"}


def test_draft_unknown_form_is_400(client, v):
    r = client.post("/api/v1/ai/voice-draft", headers=v["headers"], json={"form": "payroll", "transcript": "x"})
    assert r.status_code == 400


# ── briefing ──

def test_briefing_uses_company_currency_and_real_counts(client, db, v):
    company = db.get(Company, v["company_id"])
    saved = company.currency
    company.currency = "SAR"
    db.commit()
    try:
        r = client.get("/api/v1/ai/briefing?lang=en", headers=v["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        keys = [i["key"] for i in data["items"]]
        assert keys[:3] == ["overdue", "purchases", "vat"]
        assert "SAR" in data["items"][0]["text"] and "AED" not in data["text"]
        assert data["text"].startswith(data["intro"])
        ar = client.get("/api/v1/ai/briefing?lang=ar", headers=v["headers"]).json()
        assert "صباح الخير" in ar["intro"]
    finally:
        company.currency = saved
        db.commit()


def test_briefing_requires_reports_permission(client, db, v):
    emp_headers = _employee_headers(client, db, v["headers"], v["company_id"], ["employees:view"])
    assert client.get("/api/v1/ai/briefing", headers=emp_headers).status_code == 403


def test_next_vat_due():
    assert ai_voice._next_vat_due(date(2026, 9, 28)) == date(2026, 10, 28)
    assert ai_voice._next_vat_due(date(2026, 10, 28)) == date(2026, 10, 28)
    assert ai_voice._next_vat_due(date(2026, 10, 29)) == date(2027, 1, 28)
    assert ai_voice._next_vat_due(date(2026, 12, 31)) == date(2027, 1, 28)


# ── transcription ──

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _upload(client, headers, data=b"audio"):
    return client.post("/api/v1/ai/transcribe", headers=headers, files={"file": ("voice.webm", data, "audio/webm")}, data={"lang": "en-US"})


def test_transcribe_counts_against_daily_cap(client, v, monkeypatch):
    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", lambda req, timeout=0: _Resp(json.dumps({"text": " hello "}).encode()))
    client.put("/api/v1/ai/voice-settings", headers=v["headers"], json={"enabled": True, "daily_transcriptions": 2})
    assert _upload(client, v["headers"]).json() == {"text": "hello"}
    assert _upload(client, v["headers"]).status_code == 200
    assert _upload(client, v["headers"]).status_code == 429
    assert client.get("/api/v1/ai/voice-settings", headers=v["headers"]).json()["transcriptions_today"] == 2


def test_transcribe_off_when_cloud_transcription_disabled(client, v):
    client.put("/api/v1/ai/voice-settings", headers=v["headers"], json={"enabled": True, "cloud_transcription": False})
    assert _upload(client, v["headers"]).status_code == 403
