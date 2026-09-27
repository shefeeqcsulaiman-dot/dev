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


# ── Phase 3: /ai/voice-draft ─────────────────────────────────────────────
from app import voice_draft as vd  # noqa: E402


def _draft(client, headers, form, transcript, choices=None, llm=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(vd, "call_llm", lambda *a, **k: llm)
    return client.post("/api/v1/ai/voice-draft", headers=headers, json={
        "form": form, "transcript": transcript, "choices": choices or {}, "today": "2026-09-27"})


def test_voice_draft_expense_sanitises_and_matches(client, auth_headers, monkeypatch):
    llm = {"fields": {"date": "2026-09-26", "category": "transport", "vendor": "careem",
                      "description": "Taxi to client", "amount": "85", "vat": "4.25", "bogus": "x"}}
    choices = {"category": ["Supplies", "Transport", "Utilities"], "vendor": ["Careem Networks FZ LLC", "Emirates Taxi"]}
    r = _draft(client, auth_headers, "expense", "taxi yesterday 85 dirhams careem", choices, llm, monkeypatch)
    assert r.status_code == 200, r.text
    f = r.json()["fields"]
    assert f == {"date": "2026-09-26", "category": "Transport", "vendor": "Careem Networks FZ LLC",
                 "description": "Taxi to client", "amount": "85.00", "vat": "4.25"}


def test_voice_draft_drops_invalid_values(client, auth_headers, monkeypatch):
    llm = {"fields": {"name": "Al Noor Trading", "trn": "1002-3456", "emirate": "Mars", "email": "not an email",
                      "phone": "050 123 4567", "address": "Deira"}}
    body = _draft(client, auth_headers, "customer", "...", {"emirate": ["Dubai", "Sharjah"]}, llm, monkeypatch).json()
    assert body["fields"] == {"name": "Al Noor Trading", "phone": "0501234567", "address": "Deira"}
    assert body["unmatched"] == {"emirate": "Mars"}  # strict choice not invented
    llm = {"fields": {"date": "yesterday", "amount": "-5", "vat": "1e20"}}
    assert _draft(client, auth_headers, "expense", "...", {}, llm, monkeypatch).json()["fields"] == {}


def test_voice_draft_lines_match_products(client, auth_headers, monkeypatch):
    llm = {"fields": {"supplier": "gulf steel"},
           "lines": [{"product": "steel rods", "quantity": 10, "price": "12.5"},
                     {"product": "Mystery Widget", "quantity": 0, "price": 3},
                     {"product": ""}, "junk"]}
    choices = {"supplier": ["Gulf Steel LLC", "Dubai Paints"], "product": ["Steel Rods 12mm", "Cement Bag"]}
    body = _draft(client, auth_headers, "purchase", "...", choices, llm, monkeypatch).json()
    assert body["fields"] == {"supplier": "Gulf Steel LLC"}
    assert body["lines"] == [
        {"product": "Steel Rods 12mm", "quantity": "10.00", "price": "12.50", "matched": "yes"},
        {"product": "Mystery Widget", "quantity": None, "price": "3.00", "matched": "no"},
    ]


def test_voice_draft_never_writes_and_503_without_ai(client, auth_headers, monkeypatch):
    from app.database import SessionLocal
    from app.models import AuditLog
    db = SessionLocal()
    before = db.query(AuditLog).count()
    r = _draft(client, auth_headers, "vendor", "new vendor", {}, {"error": "no key"}, monkeypatch)
    assert r.status_code == 503
    llm = {"fields": {"name": "Dubai Paints"}}
    assert _draft(client, auth_headers, "vendor", "vendor dubai paints", {}, llm, monkeypatch).status_code == 200
    assert db.query(AuditLog).count() == before
    db.close()


def test_voice_draft_validates_form(client, auth_headers):
    r = client.post("/api/v1/ai/voice-draft", headers=auth_headers, json={"form": "payroll_run", "transcript": "run it"})
    assert r.status_code == 422


def test_voice_draft_prompt_only_carries_strict_choice_lists(client, auth_headers, monkeypatch):
    prompts = []
    monkeypatch.setattr(vd, "call_llm", lambda prompt, *a, **k: prompts.append(prompt) or {"fields": {}, "lines": []})
    choices = {"supplier": ["Gulf Steel LLC"], "product": ["Safety Helmet Hard Hat"]}
    _draft(client, auth_headers, "purchase", "5 mystery glitter", choices)
    assert "Gulf Steel LLC" in prompts[-1]
    assert "Safety Helmet" not in prompts[-1]


# ── Phase 4: ESS voice ───────────────────────────────────────────────────
from datetime import date, timedelta  # noqa: E402

from app import ess_voice as ev  # noqa: E402
from app.models import AppDataRecord  # noqa: E402
from tests.test_ess_requests import _company_id, _ess_login  # noqa: E402


def _ess_voice(client, headers, text, lang="en"):
    return client.post("/api/v1/ess/voice-intent", headers=headers, json={"transcript": text, "lang": lang})


def test_ess_voice_leave_prefill_is_sanitised_and_writes_nothing(client, db, auth_headers, monkeypatch):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "VOICE-E1", "voice.e1")
    today = date.today()
    start, end = today + timedelta(days=3), today + timedelta(days=5)
    monkeypatch.setattr(ev, "call_llm", lambda *a, **k: {"intent": "apply_leave", "fields": {
        "leave_type": "annual", "start_date": start.isoformat(), "end_date": end.isoformat(),
        "reason": "Family visit", "employee_id": "someone-else"}})
    r = _ess_voice(client, h, "apply annual leave next monday to wednesday for a family visit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "apply_leave" and body["source"] == "llm"
    assert body["fields"] == {"leave_type": "Annual Leave", "start_date": start.isoformat(),
                              "end_date": end.isoformat(), "reason": "Family visit"}
    assert client.get("/api/v1/ess/leave", headers=h).json() == []  # draft only


def test_ess_voice_overtime_rejects_out_of_range_values(client, db, auth_headers, monkeypatch):
    cid = _company_id(client, auth_headers)
    _, h = _ess_login(client, db, auth_headers, cid, "VOICE-E2", "voice.e2")
    future = (date.today() + timedelta(days=2)).isoformat()
    monkeypatch.setattr(ev, "call_llm", lambda *a, **k: {"intent": "request_overtime", "fields": {
        "date": future, "hours": 40, "login": "25:00", "logout": "19:30", "ot_type": "weekend"}})
    body = _ess_voice(client, h, "request overtime").json()
    assert body["fields"] == {"logout": "19:30", "ot_type": "weekend"}


def test_ess_voice_balance_and_shift_answer_from_own_data(client, db, auth_headers, monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "call_llm", lambda *a, **k: calls.append(a) or {"error": "unused"})
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "VOICE-E3", "voice.e3")
    other, _ = _ess_login(client, db, auth_headers, cid, "VOICE-E4", "voice.e4")
    soon = (date.today() + timedelta(days=2)).isoformat()
    sooner = (date.today() + timedelta(days=1)).isoformat()
    db.add(AppDataRecord(company_id=cid, collection="rotaAssignments", record_key="v-e3",
                         payload=json.dumps({"employee_id": "VOICE-E3", "date": soon, "type": "Morning", "start": "09:00", "end": "17:00"})))
    db.add(AppDataRecord(company_id=cid, collection="rotaAssignments", record_key="v-e4",
                         payload=json.dumps({"employee_id": "VOICE-E4", "date": sooner, "type": "Night", "start": "22:00", "end": "06:00"})))
    db.commit()
    import app.cache as cache
    monkeypatch.setattr(cache, "get_json", lambda *a, **k: None, raising=False)

    shift = _ess_voice(client, h, "When is my next shift?").json()
    assert shift["intent"] == "next_shift"
    assert "09:00" in shift["answer"] and "Night" not in shift["answer"]  # never another employee's rota

    bal = _ess_voice(client, h, "What's my annual leave balance?").json()
    assert bal["intent"] == "leave_balance"
    assert bal["answer"].startswith("You have") and "Annual Leave" in bal["answer"]
    assert calls == []  # answered by rules, no LLM call


def test_ess_voice_needs_ess_token_and_ai_module(client, db, auth_headers):
    assert _ess_voice(client, auth_headers, "open payslips").status_code == 401  # main-app token isn't an ESS token
    cid = _company_id(client, auth_headers)
    _, h = _ess_login(client, db, auth_headers, cid, "VOICE-E5", "voice.e5")
    assert _ess_voice(client, h, "open payslips").json() == {
        "intent": "navigate", "page": "payslips", "fields": {}, "source": "rules", "answer": None}
    company = db.get(Company, cid)
    saved = company.modules_enabled
    company.modules_enabled = json.dumps(["hrms", "ess"])
    db.commit()
    try:
        assert _ess_voice(client, h, "open payslips").status_code == 403
    finally:
        company.modules_enabled = saved
        db.commit()


def test_ess_voice_rules_cover_common_phrases():
    today = date(2026, 9, 27)
    assert ev.rule_match("open my payslips")["page"] == "payslips"
    assert ev.rule_match("كم رصيد إجازاتي")["intent"] == "leave_balance"
    assert ev.rule_match("I forgot to punch out yesterday")["intent"] == "request_correction"
    assert ev.sanitize({"intent": "navigate", "page": "superadmin"}, today) is None
    assert ev.sanitize({"intent": "delete_everything"}, today) is None


def test_voice_intent_quick_questions_route_to_query_targets(client, auth_headers, monkeypatch):
    _no_llm(monkeypatch)
    targets = TARGETS + [
        {"id": "q1", "kind": "query", "label": "Who is absent today"},
        {"id": "q2", "kind": "query", "label": "Pending leave requests"},
    ]
    body = _intent(client, auth_headers, "Who is absent today?", targets=targets).json()
    assert (body["intent"], body["target"]) == ("query", "q1")
    body = _intent(client, auth_headers, "show pending leave requests", targets=targets).json()
    assert (body["intent"], body["target"]) == ("query", "q2")
    # A model reply of "query" must name a query target.
    monkeypatch.setattr(vi, "call_llm", lambda *a, **k: {"intent": "query", "target": "p3", "confidence": 90})
    assert _intent(client, auth_headers, "hmm payroll-ish", targets=targets).json()["source"] == "rules"


# ── Phase 5: POS voice cart ──────────────────────────────────────────────
def test_voice_draft_pos_cart_matches_products_and_ignores_prices(client, auth_headers, monkeypatch):
    llm = {"fields": {"customer": "x"}, "lines": [
        {"product": "pepsi", "quantity": "two"}, {"product": "Pepsi", "quantity": 2, "price": 0.01},
        {"product": "tomatoes", "quantity": 0.5}, {"product": "unicorn steak", "quantity": 1}]}
    choices = {"product": ["Pepsi 330ml Can", "Tomatoes (KG)", "Bread"]}
    body = _draft(client, auth_headers, "pos_cart", "add two pepsi and half a kilo of tomatoes", choices, llm, monkeypatch).json()
    assert body["fields"] == {}
    assert [(l["product"], l["quantity"], l["matched"]) for l in body["lines"]] == [
        ("Pepsi 330ml Can", None, "yes"), ("Pepsi 330ml Can", "2.00", "yes"),
        ("Tomatoes (KG)", "0.50", "yes"), ("unicorn steak", "1.00", "no")]
    assert all(l["price"] is None for l in body["lines"])
