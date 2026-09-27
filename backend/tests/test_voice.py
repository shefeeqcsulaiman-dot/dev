"""Voice Phase 1: /ai/transcribe (server STT fallback) and /ai/assist's
answer_lang. STT and LLM calls are mocked - no real API traffic."""
import json

from app.models import Company
from app.routers import ai as ai_router
from tests.conftest import ensure_user


def _upload(client, headers, data=b"fake-webm-audio", lang="en-US"):
    return client.post(
        "/api/v1/ai/transcribe",
        headers=headers,
        files={"file": ("voice.webm", data, "audio/webm")},
        data={"lang": lang},
    )


def test_transcribe_returns_503_without_openai_key(client, auth_headers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = _upload(client, auth_headers)
    assert r.status_code == 503
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_transcribe_rejects_empty_and_oversize_audio(client, auth_headers, monkeypatch):
    called = []
    monkeypatch.setattr(ai_router, "transcribe_audio", lambda *a, **k: called.append(a) or {"text": "x"})
    assert _upload(client, auth_headers, data=b"").status_code == 422
    too_big = b"0" * (ai_router.MAX_VOICE_AUDIO_BYTES + 1)
    assert _upload(client, auth_headers, data=too_big).status_code == 413
    assert called == []


def test_transcribe_returns_text_and_normalises_language(client, auth_headers, monkeypatch):
    seen = {}

    def fake(audio, filename, content_type, lang):
        seen.update(audio=audio, lang=lang, content_type=content_type)
        return {"text": "ما هو رصيد ضريبة القيمة المضافة"}

    monkeypatch.setattr(ai_router, "transcribe_audio", fake)
    r = _upload(client, auth_headers, lang="ar-AE")
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "ما هو رصيد ضريبة القيمة المضافة", "lang": "ar"}
    assert seen == {"audio": b"fake-webm-audio", "lang": "ar", "content_type": "audio/webm"}

    # Unsupported language hints are dropped rather than forwarded.
    _upload(client, auth_headers, lang="fr-FR")
    assert seen["lang"] is None


def test_transcribe_requires_login(client):
    r = client.post("/api/v1/ai/transcribe", files={"file": ("v.webm", b"a", "audio/webm")})
    assert r.status_code == 401


def test_transcribe_blocked_when_ai_module_disabled(client, db, monkeypatch):
    monkeypatch.setattr(ai_router, "transcribe_audio", lambda *a, **k: {"text": "hi"})
    user = ensure_user(db, "voice-noai@taxflowqa.com", "900000000000077")
    company = db.get(Company, user.company_id)
    company.modules_enabled = json.dumps(["sales"])
    db.commit()
    login = client.post("/api/v1/auth/login", json={"email": "voice-noai@taxflowqa.com", "password": "admin123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert _upload(client, headers).status_code == 403


def test_assist_answer_lang_ar_asks_for_arabic(client, auth_headers, monkeypatch):
    prompts = []

    def fake_llm(prompt, system, **kwargs):
        prompts.append(prompt)
        return {"answer": "راجع الرقم الضريبي", "confidence": 90, "suggested_actions": ["أ", "ب"]}

    monkeypatch.setattr(ai_router, "call_llm", fake_llm)
    r = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "VAT?", "answer_lang": "ar"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "راجع الرقم الضريبي"
    assert "in Arabic" in prompts[-1]

    client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "VAT?"})
    assert "in Arabic" not in prompts[-1]

    bad = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "VAT?", "answer_lang": "de"})
    assert bad.status_code == 422


# ── Phase 2: /ai/voice-intent ────────────────────────────────────────────
from app import voice_intent as vi  # noqa: E402

TARGETS = [
    {"id": "p1", "kind": "page", "label": "Dashboard"},
    {"id": "p2", "kind": "page", "label": "Sales & Invoices"},
    {"id": "p3", "kind": "page", "label": "Payroll"},
    {"id": "p4", "kind": "page", "label": "Reports"},
    {"id": "t1", "kind": "tab", "label": "Reports › Trial Balance"},
    {"id": "t2", "kind": "tab", "label": "Sales & Invoices › Invoices"},
    {"id": "a1", "kind": "action", "label": "+ New Invoice", "alt": "Sales & Invoices"},
    {"id": "a2", "kind": "action", "label": "+ Add Employee", "alt": "HRMS Dashboard"},
]


def _intent(client, headers, transcript, targets=TARGETS, lang="en"):
    return client.post("/api/v1/ai/voice-intent", headers=headers,
                       json={"transcript": transcript, "lang": lang, "targets": targets})


def _no_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: calls.append(a) or {"error": "no key"})
    return calls


def test_voice_intent_exact_page_skips_llm(client, auth_headers, monkeypatch):
    calls = _no_llm(monkeypatch)
    r = _intent(client, auth_headers, "Open payroll")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["intent"], body["target"], body["source"]) == ("navigate", "p3", "rules")
    assert calls == []


def test_voice_intent_rules_tab_form_and_search(client, auth_headers, monkeypatch):
    _no_llm(monkeypatch)
    assert _intent(client, auth_headers, "show me the trial balance").json()["target"] == "t1"
    form = _intent(client, auth_headers, "create a new invoice").json()
    assert (form["intent"], form["target"]) == ("open_form", "a1")
    search = _intent(client, auth_headers, "search for Al Noor in sales and invoices").json()
    assert (search["intent"], search["target"], search["query"]) == ("search", "p2", "al noor")


def test_voice_intent_question_falls_back_to_answer(client, auth_headers, monkeypatch):
    _no_llm(monkeypatch)
    body = _intent(client, auth_headers, "How much VAT do I owe this quarter?").json()
    assert body["intent"] == "answer"
    assert body["query"] == "How much VAT do I owe this quarter?"


def test_voice_intent_rejects_llm_targets_outside_the_list(client, auth_headers, monkeypatch):
    # An LLM reply naming a target the client never offered is discarded.
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {"intent": "navigate", "target": "superadmin-delete", "confidence": 99})
    body = _intent(client, auth_headers, "wipe everything").json()
    assert body["source"] == "rules"
    assert body["target"] in (None, *[t["id"] for t in TARGETS])
    # Kind mismatch (open_form on a page) is also rejected.
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {"intent": "open_form", "target": "p3", "confidence": 90})
    assert _intent(client, auth_headers, "payroll thing please").json()["source"] == "rules"


def test_voice_intent_uses_valid_llm_reply(client, auth_headers, monkeypatch):
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {
        "intent": "navigate", "target": "t1", "confidence": 88, "alternatives": ["p4", "bogus", "t1"]})
    body = _intent(client, auth_headers, "ميزان المراجعة", lang="ar").json()
    assert (body["intent"], body["target"], body["source"]) == ("navigate", "t1", "llm")
    assert body["alternatives"] == ["p4"]


def test_voice_intent_fuzz_only_whitelisted_targets(client, auth_headers, monkeypatch):
    _no_llm(monkeypatch)
    ids = {t["id"] for t in TARGETS}
    for text in ["", " ", "asdf qwer", "open open open", "DROP TABLE users;", "new", "search", "افتح", "?" * 50, "go to superadmin"]:
        r = _intent(client, auth_headers, text or "x")
        assert r.status_code == 200, (text, r.text)
        body = r.json()
        assert body["intent"] in vi.INTENTS
        assert body["target"] is None or body["target"] in ids
        assert set(body["alternatives"]) <= ids


def test_voice_intent_validates_payload(client, auth_headers):
    assert _intent(client, auth_headers, "open payroll", targets=[]).status_code == 422
    bad = [{"id": "x", "kind": "delete", "label": "Delete all"}]
    assert _intent(client, auth_headers, "open payroll", targets=bad).status_code == 422
    assert client.post("/api/v1/ai/voice-intent", json={"transcript": "hi", "targets": TARGETS}).status_code == 401


def test_voice_intent_llm_cannot_search_or_answer_unprompted(client, auth_headers, monkeypatch):
    # "leave requests" names a screen: an LLM "search"/"answer" is discarded for the rule match.
    targets = TARGETS + [{"id": "t9", "kind": "tab", "label": "Leave › Leave Requests"}]
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {"intent": "search", "target": "p2", "query": "leave requests", "confidence": 90})
    body = _intent(client, auth_headers, "leave requests", targets=targets).json()
    assert (body["intent"], body["target"], body["source"]) == ("navigate", "t9", "rules")
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {"intent": "answer", "query": "payroll", "confidence": 90})
    body = _intent(client, auth_headers, "payroll run", targets=targets).json()
    assert body["source"] == "rules" and body["intent"] == "navigate"
    # A real question is still allowed through as an answer.
    body = _intent(client, auth_headers, "what is my payroll cost?", targets=targets).json()
    assert (body["intent"], body["source"]) == ("answer", "llm")
