"""GET /ai/briefing: today's position as speakable text plus page links."""
import datetime as dt

from app.voice_briefing import next_vat_due


def test_next_vat_due_is_28_days_after_quarter_end():
    assert next_vat_due(dt.date(2026, 9, 28)) == (dt.date(2026, 9, 30), dt.date(2026, 10, 28))
    assert next_vat_due(dt.date(2026, 10, 28)) == (dt.date(2026, 9, 30), dt.date(2026, 10, 28))
    assert next_vat_due(dt.date(2026, 10, 29)) == (dt.date(2026, 12, 31), dt.date(2027, 1, 28))
    assert next_vat_due(dt.date(2027, 1, 20)) == (dt.date(2026, 12, 31), dt.date(2027, 1, 28))


def test_briefing_english_and_arabic(client, auth_headers):
    en = client.get("/api/v1/ai/briefing", headers=auth_headers)
    assert en.status_code == 200, en.text
    body = en.json()
    assert body["lang"] == "en" and body["text"].startswith("Good ")
    keys = [i["key"] for i in body["items"]]
    assert "vat" in keys and all(i["page"] for i in body["items"])
    assert "VAT return" in body["text"]
    ar = client.get("/api/v1/ai/briefing?lang=ar", headers=auth_headers).json()
    assert ar["lang"] == "ar" and "ضريبة" in ar["text"]


def test_briefing_requires_login(client):
    assert client.get("/api/v1/ai/briefing").status_code == 401
