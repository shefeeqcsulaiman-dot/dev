"""Trial requests and self-service sign-ups are copied to the outreach dashboard
(app/outreach_forward.py) once INTAKE_SECRET is set - never with the password, and a
failing dashboard never breaks the request."""
import uuid

import httpx
import pytest

from app import outreach_forward
from app.config import get_settings


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(outreach_forward.httpx, "post", fake_post)
    monkeypatch.setattr(get_settings(), "intake_secret", "test-intake-secret")
    return calls


def test_trial_request_is_copied_to_the_dashboard(client, sent):
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/trial-request", json={
        "full_name": "Ahmed Ali", "company_name": "Ali Trading LLC", "email": email,
        "phone": "+971 50 123 4567", "employee_count": "10–20", "interest": "HRMS", "notes": "Call me"})
    assert r.status_code == 201, r.text
    assert len(sent) == 1
    call = sent[0]
    assert call["url"] == "https://outreach.etaxflow.com/api/public/request"
    assert call["headers"] == {"X-Intake-Secret": "test-intake-secret"}
    assert call["json"] == {"full_name": "Ahmed Ali", "company_name": "Ali Trading LLC", "email": email,
                            "phone": "+971 50 123 4567", "employee_count": "10–20", "interest": "HRMS",
                            "notes": "Call me", "kind": "trial", "page": "app.etaxflow.com trial request"}


def test_signup_is_copied_without_the_password(client, sent):
    email = f"trial-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Trial-pass-1", "full_name": "Trial Owner", "company_name": "Trial Co",
        "country": "United Arab Emirates", "employee_count": "1–10"})
    assert r.status_code == 201, r.text
    assert len(sent) == 1
    body = sent[0]["json"]
    assert body["email"] == email and body["kind"] == "trial" and body["page"] == "app.etaxflow.com/signup"
    assert body["plan"] == "3-day trial account" and body["country"] == "United Arab Emirates"
    assert "password" not in body and "Trial-pass-1" not in str(body)


def test_nothing_is_sent_without_the_secret(client, monkeypatch):
    calls = []
    monkeypatch.setattr(outreach_forward.httpx, "post", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(get_settings(), "intake_secret", "")
    r = client.post("/api/v1/auth/trial-request", json={
        "full_name": "No Copy", "company_name": "No Copy LLC", "email": f"n-{uuid.uuid4().hex[:8]}@example.com"})
    assert r.status_code == 201
    assert calls == []


def test_unreachable_dashboard_does_not_break_the_request(client, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(outreach_forward.httpx, "post", down)
    monkeypatch.setattr(get_settings(), "intake_secret", "test-intake-secret")
    r = client.post("/api/v1/auth/trial-request", json={
        "full_name": "Still Saved", "company_name": "Saved LLC", "email": f"s-{uuid.uuid4().hex[:8]}@example.com"})
    assert r.status_code == 201
