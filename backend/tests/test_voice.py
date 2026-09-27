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
