"""ESS voice endpoints: /ess/voice-settings, /ess/voice-intent,
/ess/voice-transcribe. The LLM and OpenAI transcription are stubbed -- these
tests cover gating, field sanitising and the server-answered questions."""
import io
import json
import uuid
from datetime import timedelta

import pytest

import app.cache as cache
import app.routers.ess_voice as ess_voice
import app.voice as voice_mod
from app.models import AppDataRecord, Company, Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                   basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": f"{username}pw123", "is_active": True},
    )
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    assert login.status_code == 200, login.text
    return emp, {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture()
def voice(client, db, auth_headers, monkeypatch):
    """An ESS session with an AI key configured and the LLM stubbed via voice['llm']."""
    company_id = _company_id(client, auth_headers)
    company = db.get(Company, company_id)
    saved_modules = company.modules_enabled
    company.modules_enabled = None  # unrestricted
    db.commit()
    suffix = uuid.uuid4().hex[:8]
    emp, headers = _ess_login(client, db, auth_headers, company_id, f"VOICE-{suffix}", f"voice.{suffix}")
    state = {"llm": {"intent": "unknown"}, "prompts": [], "emp": emp, "headers": headers, "company_id": company_id}
    monkeypatch.setattr(voice_mod, "_openai_key", lambda: "sk-test")
    monkeypatch.setattr(voice_mod, "_anthropic_key", lambda: "")

    def fake_llm(prompt, system, **kwargs):
        state["prompts"].append(prompt)
        return state["llm"]

    monkeypatch.setattr(ess_voice, "call_llm", fake_llm)
    yield state
    company = db.get(Company, company_id)
    company.modules_enabled = saved_modules
    db.commit()


def _intent(client, headers, text, lang="en"):
    return client.post("/api/v1/ess/voice-intent", headers=headers, json={"transcript": text, "lang": lang})


# ── settings / gating ──

def test_voice_settings_enabled_with_key_and_ai_module(client, voice):
    r = client.get("/api/v1/ess/voice-settings", headers=voice["headers"])
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "default_lang": ""}


def test_voice_disabled_without_any_ai_key(client, voice, monkeypatch):
    monkeypatch.setattr(voice_mod, "_openai_key", lambda: "")
    r = client.get("/api/v1/ess/voice-settings", headers=voice["headers"])
    assert r.json()["enabled"] is False
    r = _intent(client, voice["headers"], "show my payslips")
    assert r.status_code == 403
    assert "isn't enabled" in r.json()["detail"]  # frontend hides the mic on this wording


def test_voice_disabled_when_company_ai_module_off(client, db, voice):
    company = db.get(Company, voice["company_id"])
    company.modules_enabled = json.dumps(["ess", "hrms"])
    db.commit()
    assert client.get("/api/v1/ess/voice-settings", headers=voice["headers"]).json()["enabled"] is False
    assert _intent(client, voice["headers"], "show my payslips").status_code == 403


def test_voice_endpoints_require_ess_token(client):
    assert client.get("/api/v1/ess/voice-settings").status_code == 401
    assert client.post("/api/v1/ess/voice-intent", json={"transcript": "hi"}).status_code == 401


# ── intent ──

def test_intent_prompt_includes_company_local_today_and_yesterday(client, db, voice):
    voice["llm"] = {"intent": "unknown"}
    _intent(client, voice["headers"], "request overtime yesterday")
    today = ess_voice._local_today(db, voice["emp"])
    prompt = voice["prompts"][-1]
    assert today.isoformat() in prompt
    assert (today - timedelta(days=1)).isoformat() in prompt
    assert "request overtime yesterday" in prompt


def test_intent_navigate_only_to_known_pages(client, voice):
    voice["llm"] = {"intent": "navigate", "page": "payslips"}
    assert _intent(client, voice["headers"], "show payslips").json() == {"intent": "navigate", "page": "payslips"}
    voice["llm"] = {"intent": "navigate", "page": "../admin"}
    assert _intent(client, voice["headers"], "open admin").json() == {"intent": "unknown"}


def test_intent_leave_fields_are_sanitised(client, voice):
    voice["llm"] = {"intent": "apply_leave", "fields": {
        "start_date": "2026-10-05", "end_date": "next week", "leave_type": "Annual Leave",
        "reason": "Family trip", "bogus": "x",
    }}
    r = _intent(client, voice["headers"], "annual leave from the fifth")
    assert r.status_code == 200, r.text
    assert r.json() == {"intent": "apply_leave", "fields": {
        "start_date": "2026-10-05", "leave_type": "Annual Leave", "reason": "Family trip",
    }}


def test_intent_rejects_unknown_enum_values_and_bad_numbers(client, voice):
    voice["llm"] = {"intent": "request_overtime", "fields": {
        "date": "2026-09-27", "hours": "two", "login": "25:00", "logout": "18:30", "ot_type": "double",
    }}
    assert _intent(client, voice["headers"], "overtime").json()["fields"] == {"date": "2026-09-27", "logout": "18:30"}

    voice["llm"] = {"intent": "request_loan", "fields": {
        "loan_type": "Car Loan", "amount": 5000, "months": "10.0", "reason": "x" * 800,
    }}
    fields = _intent(client, voice["headers"], "loan").json()["fields"]
    assert fields["amount"] == 5000 and fields["months"] == 10
    assert "loan_type" not in fields
    assert len(fields["reason"]) == 500

    voice["llm"] = {"intent": "request_advance", "fields": {"amount": -50}}
    assert _intent(client, voice["headers"], "advance").json()["fields"] == {}


def test_intent_unrecognised_intent_is_unknown(client, voice):
    voice["llm"] = {"intent": "delete_everything", "fields": {"all": True}}
    assert _intent(client, voice["headers"], "delete everything").json() == {"intent": "unknown"}


def test_intent_llm_error_is_a_502_not_a_fake_answer(client, voice):
    voice["llm"] = {"error": "OpenAI request failed (500)"}
    r = _intent(client, voice["headers"], "what's my balance")
    assert r.status_code == 502
    assert "OpenAI" not in r.json()["detail"]


def test_intent_rejects_empty_transcript(client, voice):
    assert _intent(client, voice["headers"], "").status_code == 422


# ── server-answered questions ──

def test_leave_balance_answer_uses_employees_own_balance(client, voice):
    voice["llm"] = {"intent": "leave_balance", "fields": {}}
    r = _intent(client, voice["headers"], "what's my leave balance")
    assert r.json() == {"intent": "leave_balance", "answer": "You have 21 of 21 days of Annual Leave left."}

    r = _intent(client, voice["headers"], "كم رصيد إجازتي", lang="ar")
    assert "21" in r.json()["answer"] and "Annual Leave" in r.json()["answer"]


def test_next_shift_answer_skips_off_days_and_other_employees(client, db, voice):
    emp = voice["emp"]
    today = ess_voice._local_today(db, emp)

    def seed(emp_no, day, code, start="09:00", end="17:00"):
        key = f"{emp_no}-{day.isoformat()}"
        payload = {"id": key, "employee_id": emp_no, "date": day.isoformat(), "code": code, "start": start, "end": end}
        db.add(AppDataRecord(company_id=emp.company_id, collection="rotaAssignments", record_key=key, payload=json.dumps(payload)))

    voice["llm"] = {"intent": "next_shift"}
    r = _intent(client, voice["headers"], "when do I work next")
    assert r.json()["answer"] == "You have no shifts scheduled in the next 30 days."

    seed(emp.employee_no, today + timedelta(days=1), "OFF")
    seed("SOMEONE-ELSE", today + timedelta(days=1), "M", "06:00", "14:00")
    seed(emp.employee_no, today + timedelta(days=2), "Evening", "14:00", "22:00")
    db.commit()
    cache.delete(f"ess_appdata:{emp.company_id}:rotaAssignments")  # short-TTL scan cache from the first call

    answer = _intent(client, voice["headers"], "when do I work next").json()["answer"]
    assert "Evening" in answer and "14:00" in answer and "22:00" in answer
    assert "06:00" not in answer


# ── transcribe ──

class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_transcribe_forwards_audio_and_returns_text(client, voice, monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent["url"], sent["body"], sent["auth"] = req.full_url, req.data, req.headers.get("Authorization")
        return _FakeResp(json.dumps({"text": "  show my payslips "}).encode())

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", fake_urlopen)
    r = client.post(
        "/api/v1/ess/voice-transcribe", headers=voice["headers"],
        files={"file": ("voice.webm", b"fake-audio-bytes", "audio/webm")}, data={"lang": "ar-AE"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "show my payslips"}
    assert sent["url"].endswith("/audio/transcriptions")
    assert sent["auth"] == "Bearer sk-test"
    assert b"fake-audio-bytes" in sent["body"]
    assert b'name="language"\r\n\r\nar' in sent["body"]


def test_transcribe_needs_openai_key(client, voice, monkeypatch):
    monkeypatch.setattr(voice_mod, "_openai_key", lambda: "")
    monkeypatch.setattr(voice_mod, "_anthropic_key", lambda: "sk-ant-test")  # voice on, but no Whisper
    r = client.post("/api/v1/ess/voice-transcribe", headers=voice["headers"],
                    files={"file": ("voice.webm", b"x", "audio/webm")})
    assert r.status_code == 503


def test_transcribe_rejects_empty_and_oversized_audio(client, voice, monkeypatch):
    monkeypatch.setattr(voice_mod, "MAX_AUDIO_BYTES", 10)
    empty = client.post("/api/v1/ess/voice-transcribe", headers=voice["headers"],
                        files={"file": ("voice.webm", b"", "audio/webm")})
    assert empty.status_code == 400
    big = client.post("/api/v1/ess/voice-transcribe", headers=voice["headers"],
                      files={"file": ("voice.webm", b"x" * 11, "audio/webm")})
    assert big.status_code == 413


def test_transcribe_upstream_failure_is_502(client, voice, monkeypatch):
    def boom(req, timeout=0):
        raise voice_mod.urllib.error.URLError("down")

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", boom)
    r = client.post("/api/v1/ess/voice-transcribe", headers=voice["headers"],
                    files={"file": ("voice.webm", b"abc", "audio/webm")})
    assert r.status_code == 502


def test_transcribe_runs_off_the_event_loop(client, voice, monkeypatch):
    import asyncio

    def fake_urlopen(req, timeout=0):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        assert not on_loop, "Whisper call blocked the event loop"
        return _FakeResp(json.dumps({"text": "hi"}).encode())

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", fake_urlopen)
    r = client.post(
        "/api/v1/ess/voice-transcribe", headers=voice["headers"],
        files={"file": ("voice.webm", b"fake-audio-bytes", "audio/webm")}, data={"lang": "en-US"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "hi"


def _post_audio(client, voice):
    return client.post(
        "/api/v1/ess/voice-transcribe", headers=voice["headers"],
        files={"file": ("voice.webm", b"fake-audio-bytes", "audio/webm")}, data={"lang": "en-US"},
    )


def test_transcribe_sends_company_names_and_new_model(client, db, voice, monkeypatch):
    monkeypatch.setattr(voice_mod, "_vocab_cache", voice_mod.cache.LocalTTLCache(max_entries=10))
    monkeypatch.delenv("OPENAI_TRANSCRIBE_MODEL", raising=False)
    sent = []

    def fake_urlopen(req, timeout=0):
        sent.append(req.data)
        return _FakeResp(json.dumps({"text": "ok"}).encode())

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", fake_urlopen)
    assert _post_audio(client, voice).status_code == 200
    body = sent[0]
    assert b'name="model"\r\n\r\ngpt-4o-mini-transcribe' in body
    # The shared test company has many employees, so which names fit the hint varies; check it's the built hint.
    prompt = voice_mod.transcription_prompt(db, voice["company_id"])
    assert "Names:" in prompt and b'name="prompt"\r\n\r\n' + prompt.encode() in body


def test_transcribe_falls_back_when_model_is_refused(client, voice, monkeypatch):
    import urllib.error
    monkeypatch.setattr(voice_mod, "_vocab_cache", voice_mod.cache.LocalTTLCache(max_entries=10))
    monkeypatch.delenv("OPENAI_TRANSCRIBE_MODEL", raising=False)
    models = []

    def fake_urlopen(req, timeout=0):
        model = req.data.split(b'name="model"\r\n\r\n')[1].split(b"\r\n")[0].decode()
        models.append(model)
        if model != "whisper-1":
            raise urllib.error.HTTPError(req.full_url, 404, "model_not_found", {}, None)
        return _FakeResp(json.dumps({"text": "fallback worked"}).encode())

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", fake_urlopen)
    r = _post_audio(client, voice)
    assert r.status_code == 200 and r.json()["text"] == "fallback worked"
    assert models == ["gpt-4o-mini-transcribe", "whisper-1"]
