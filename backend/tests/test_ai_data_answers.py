"""The AI Assistant answers questions from the company's live data: the LLM
prompt carries real figures, and without an AI key common questions are
still answered from the same snapshot instead of generic text."""
import json

from app.routers import ai as ai_router


def _seed_sale(client, headers, no, customer, total):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "salesInvoices", "record": {
        "invoice_no": no, "document_type": "Sales Invoice", "customer": customer, "date": "2026-09-01",
        "due_date": "2026-09-05", "subtotal": total, "vat_amount": round(total * 0.05, 2), "total": round(total * 1.05, 2),
        "status": "Sent", "source": "Manual",
        "lines": [{"description": "Consulting", "qty": 1, "price": total, "amount": total, "tax_rate": 5}]}})
    assert r.status_code == 200, r.text


def test_assist_prompt_contains_live_company_figures(client, auth_headers, monkeypatch):
    _seed_sale(client, auth_headers, "AIQ-0001", "Data Question Trading LLC", 12000)
    prompts = []

    def fake_llm(prompt, system, **kwargs):
        prompts.append((prompt, system))
        return {"answer": "Revenue is ...", "confidence": 90, "suggested_actions": ["a", "b"]}

    monkeypatch.setattr(ai_router, "call_llm", fake_llm)
    r = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "Who owes me the most?"})
    assert r.status_code == 200, r.text
    prompt, system = prompts[-1]
    data = json.loads(prompt.split("\n", 1)[1].split("\n\nUSER QUESTION")[0])
    assert "Data Question Trading LLC" in json.dumps(data)
    for key in ("company", "totals_to_date", "vat_return", "receivables_summary"):
        assert key in data, key
    assert "never invent numbers" in system


def test_assist_answers_from_data_without_ai_key(client, auth_headers, monkeypatch):
    monkeypatch.setattr(ai_router, "call_llm", lambda *a, **k: {"error": "No AI API key configured"})
    r = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "How much VAT do I owe?"})
    body = r.json()
    assert r.status_code == 200
    assert "VAT" in body["answer"] and "due on" in body["answer"]
    staff = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "How many staff are present today?"}).json()
    assert "staff are present today" in staff["answer"]
    # A question the snapshot can't answer keeps the generic fallback.
    other = client.post("/api/v1/ai/assist", headers=auth_headers, json={"question": "Explain the approval workflow"}).json()
    assert other["answer"] and "staff are present" not in other["answer"]
