"""Voice data entry (Phase 3): turn a dictated sentence into a draft for one
of a few existing forms. Returns field values only; the page fills its own
form and the user presses the normal Save. Nothing here writes data."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai_client import call_llm

# field -> type. "choice" must match a client-supplied option (strict),
# "name" is matched when close and otherwise kept as spoken (soft).
FORMS: dict[str, dict[str, str]] = {
    "expense": {"date": "date", "category": "choice", "vendor": "name", "description": "text", "amount": "money", "vat": "money"},
    "purchase": {"supplier": "choice", "reference": "text", "date": "date", "notes": "text"},
    "sales_invoice": {"customer": "name", "date": "date", "due_date": "date", "po": "text", "reference": "text"},
    "customer": {"name": "text", "trn": "trn", "emirate": "choice", "address": "text", "email": "email", "phone": "phone"},
    "vendor": {"name": "text", "trn": "trn", "category": "choice", "email": "email", "phone": "phone", "address": "text"},
}
# Forms with item lines, and the choice list their product names match against.
LINE_FORMS = {"purchase": "product", "sales_invoice": "product"}
MAX_LINES = 30
MAX_MONEY = Decimal("1000000000")

FORM_HINTS = {
    "expense": "A business expense. amount = amount before VAT; vat = VAT amount only if the user states it.",
    "purchase": "A supplier purchase. lines = items bought with quantity and unit cost.",
    "sales_invoice": "A customer sales invoice. lines = items sold with quantity and unit price.",
    "customer": "A new customer record.",
    "vendor": "A new vendor / supplier record.",
}

SYSTEM_PROMPT = (
    "You extract form fields from a dictated sentence for a UAE accounting app (English or Arabic, "
    "possibly with speech-recognition errors). Only fill a field the user actually said; leave anything "
    "unclear out rather than guessing. Resolve relative dates (today, yesterday, next Monday) against the "
    "given TODAY and output YYYY-MM-DD. Amounts are plain numbers without currency. Spell out spoken "
    "emails/phones normally (\"at\" -> @, \"dot\" -> .). For choice fields pick the closest option from "
    "the list given. Write every other name (vendor, customer, product) exactly as spoken - never replace it "
    "with a different item. Respond with JSON only: {\"fields\": {...}, \"lines\": [{\"product\": \"\", \"quantity\": 0, \"price\": 0}]}"
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


def _clean_text(value: Any, limit: int = 300) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] or None


def _money(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY:
        return None
    return str(amount.quantize(Decimal("0.01")))


def _date(value: Any) -> str | None:
    try:
        return dt.date.fromisoformat(str(value).strip()[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def match_choice(value: str, options: list[str], cutoff: float = 0.72) -> str | None:
    """Best option for a spoken name (case/spacing-insensitive fuzzy match)."""
    if not value or not options:
        return None
    norm = {re.sub(r"\s+", " ", o).strip().lower(): o for o in options if o}
    key = re.sub(r"\s+", " ", value).strip().lower()
    if key in norm:
        return norm[key]
    # A spoken fragment ("Al Noor") that uniquely starts or is contained in one option.
    contained = [o for k, o in norm.items() if len(key) >= 3 and (k.startswith(key) or f" {key}" in f" {k}")]
    if len(contained) == 1:
        return contained[0]
    close = difflib.get_close_matches(key, list(norm), n=1, cutoff=cutoff)
    return norm[close[0]] if close else None


def sanitize_draft(form: str, raw: Any, choices: dict[str, list[str]]) -> dict[str, Any]:
    spec = FORMS[form]
    fields_in = raw.get("fields") if isinstance(raw, dict) and isinstance(raw.get("fields"), dict) else {}
    fields: dict[str, str] = {}
    unmatched: dict[str, str] = {}
    for name, kind in spec.items():
        value = fields_in.get(name)
        if value in (None, ""):
            continue
        if kind == "money":
            cleaned = _money(value)
        elif kind == "date":
            cleaned = _date(value)
        elif kind == "trn":
            digits = re.sub(r"\D", "", str(value))
            cleaned = digits if len(digits) == 15 else None
        elif kind == "email":
            text = (_clean_text(value, 120) or "").replace(" ", "").lower()
            cleaned = text if _EMAIL_RE.match(text) else None
        elif kind == "phone":
            text = re.sub(r"[^\d+]", "", str(value))
            cleaned = text if 7 <= len(text.lstrip("+")) <= 15 else None
        elif kind in ("choice", "name"):
            spoken = _clean_text(value, 160)
            cleaned = match_choice(spoken or "", choices.get(name, []))
            if cleaned is None and spoken:
                if kind == "name":
                    cleaned = spoken  # new name; the form accepts free text
                else:
                    unmatched[name] = spoken
        else:
            cleaned = _clean_text(value)
        if cleaned is not None:
            fields[name] = cleaned

    lines: list[dict[str, str]] = []
    if form in LINE_FORMS and isinstance(raw, dict) and isinstance(raw.get("lines"), list):
        products = choices.get(LINE_FORMS[form], [])
        for line in raw["lines"][:MAX_LINES]:
            if not isinstance(line, dict):
                continue
            spoken = _clean_text(line.get("product"), 160)
            if not spoken:
                continue
            item: dict[str, str] = {"product": match_choice(spoken, products) or spoken}
            qty = _money(line.get("quantity"))
            if qty is not None and Decimal(qty) > 0:
                item["quantity"] = qty
            price = _money(line.get("price"))
            if price is not None:
                item["price"] = price
            item["matched"] = "yes" if item["product"] != spoken or spoken in products else "no"
            lines.append(item)
    return {"form": form, "fields": fields, "lines": lines, "unmatched": unmatched}


def build_draft(form: str, transcript: str, choices: dict[str, list[str]], today: dt.date, lang: str | None) -> dict[str, Any]:
    spec = FORMS[form]
    # Only strict choice lists go to the model; names/products are matched here,
    # so an unknown item is kept as spoken instead of swapped for a lookalike.
    shown = {k: v[:200] for k, v in choices.items() if spec.get(k) == "choice"}
    field_list = ", ".join(f"{k} ({t})" for k, t in spec.items())
    prompt = (
        f"FORM: {form} - {FORM_HINTS[form]}\nFIELDS: {field_list}"
        + (f"\nLINES: yes (product, quantity, price)" if form in LINE_FORMS else "\nLINES: none, return []")
        + f"\nTODAY: {today.isoformat()} ({today.strftime('%A')})"
        + f"\nCHOICES: {json.dumps(shown, ensure_ascii=False)}"
        + f"\nDICTATION ({lang or 'unknown language'}): {json.dumps(transcript, ensure_ascii=False)}"
    )
    result = call_llm(
        prompt,
        SYSTEM_PROMPT,
        openai_model_env="OPENAI_VOICE_MODEL",
        openai_default="gpt-4o-mini",
        anthropic_model_env="ANTHROPIC_VOICE_MODEL",
        anthropic_default="claude-haiku-4-5-20251001",
        max_tokens=900,
        temperature=0,
    )
    if not isinstance(result, dict) or "error" in result:
        return {"error": (result or {}).get("error", "AI extraction failed") if isinstance(result, dict) else "AI extraction failed"}
    return sanitize_draft(form, result, choices)
