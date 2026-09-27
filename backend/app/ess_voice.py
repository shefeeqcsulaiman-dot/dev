"""ESS voice (Phase 4): classify an employee's spoken request into a small,
fixed set of self-service intents and extract form values. The caller's
identity always comes from the ESS token (see routers/ess.py); nothing here
takes an employee id, reads data, or writes anything."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai_client import call_llm

PAGES = ("dashboard", "attendance", "gps", "rota", "tasks", "leave", "requests", "payslips", "team", "profile")
FORM_INTENTS = ("apply_leave", "request_overtime", "request_correction", "request_advance", "request_loan")
ANSWER_INTENTS = ("leave_balance", "next_shift")
INTENTS = ("navigate", *FORM_INTENTS, *ANSWER_INTENTS, "unknown")

LEAVE_TYPES = (
    "Annual Leave", "Sick Leave", "Casual Leave", "Emergency Leave", "Maternity Leave",
    "Paternity Leave", "Unpaid Leave", "Lieu Days", "Hajj Leave", "Work From Home",
)
OT_TYPES = ("normal", "ramadan", "weekend", "holiday")
LOAN_TYPES = ("Personal Loan", "Emergency Loan", "Medical Loan", "Home Furnishing Loan", "Education Loan", "Vehicle Loan")

_PAGE_WORDS = {
    "dashboard": r"dashboard|home|الرئيسية|لوحة",
    "attendance": r"attendance|punch|الحضور",
    "gps": r"gps|check ?in|clock ?in|تسجيل الدخول",
    "rota": r"rota|roster|schedule|shifts?|جدول|المناوبات",
    "tasks": r"tasks?|to ?do|المهام",
    "leave": r"leaves?|time off|الإجازات|الاجازات",
    "requests": r"requests?|approvals?|الطلبات",
    "payslips": r"payslips?|pay ?slip|salary slip|قسيمة|الراتب",
    "team": r"team|colleagues|الفريق",
    "profile": r"profile|my details|password|الملف",
}
_BALANCE_RE = re.compile(r"balance|how many (leave|days)|days (left|remaining)|remaining leave|رصيد|كم يوم", re.IGNORECASE)
_SHIFT_RE = re.compile(r"next shift|my shift|when (do|am) i work|when is my (next )?shift|مناوبتي|دوامي|متى دوامي", re.IGNORECASE)
_FORM_HINTS = {
    "apply_leave": re.compile(r"\b(apply|request|take|book)\b.*\b(leave|off|holiday|vacation|sick)\b|إجازة|اجازة", re.IGNORECASE),
    "request_overtime": re.compile(r"overtime|\bot\b|extra hours|عمل إضافي|ساعات إضافية", re.IGNORECASE),
    "request_correction": re.compile(r"correct|forgot to (punch|check)|missing (punch|check)|تصحيح", re.IGNORECASE),
    "request_advance": re.compile(r"advance|سلفة", re.IGNORECASE),
    "request_loan": re.compile(r"\bloan\b|قرض", re.IGNORECASE),
}

SYSTEM_PROMPT = (
    "You handle spoken requests in an employee self-service HR portal (English or Arabic). Pick ONE intent:\n"
    '- "navigate": open a page; "page" is one of: ' + ", ".join(PAGES) + ".\n"
    '- "apply_leave": fields leave_type (one of: ' + ", ".join(LEAVE_TYPES) + "), start_date, end_date, reason.\n"
    '- "request_overtime": fields date, hours, login (HH:MM), logout (HH:MM), ot_type (normal|ramadan|weekend|holiday), reason.\n'
    '- "request_correction": fields date, checkin (HH:MM), checkout (HH:MM), reason.\n'
    '- "request_advance": fields amount, reason.\n'
    '- "request_loan": fields loan_type (one of: ' + ", ".join(LOAN_TYPES) + "), amount, months, reason.\n"
    '- "leave_balance": asking how much leave they have left; optional field leave_type.\n'
    '- "next_shift": asking when they work next.\n'
    '- "unknown".\n'
    "Resolve relative dates against TODAY and output YYYY-MM-DD (\"next Monday to Wednesday\" = those dates). "
    "Only include fields the user actually said. Respond with JSON only: "
    '{"intent": "", "page": null, "fields": {}}'
)

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _time(value: Any) -> str | None:
    m = _HHMM.match(str(value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _number(value: Any, lo: Decimal, hi: Decimal) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return n if n.is_finite() and lo <= n <= hi else None


def _pick(value: Any, options: tuple[str, ...]) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    by_lower = {o.lower(): o for o in options}
    if text in by_lower:
        return by_lower[text]
    for o in options:  # "annual" -> "Annual Leave"
        if o.lower().startswith(text) or text in o.lower():
            return o
    close = difflib.get_close_matches(text, list(by_lower), n=1, cutoff=0.7)
    return by_lower[close[0]] if close else None


def _text(value: Any, limit: int = 300) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()[:limit] or None


def sanitize(raw: Any, today: dt.date) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or "error" in raw or raw.get("intent") not in INTENTS:
        return None
    intent = raw["intent"]
    f = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    out: dict[str, str] = {}
    if intent == "navigate":
        page = raw.get("page")
        return {"intent": "navigate", "page": page, "fields": {}} if page in PAGES else None
    if intent == "apply_leave":
        if (lt := _pick(f.get("leave_type"), LEAVE_TYPES)):
            out["leave_type"] = lt
        start, end = _date(f.get("start_date")), _date(f.get("end_date"))
        # Leave is for now or later; a year out is the sanity limit.
        if start and today - dt.timedelta(days=31) <= start <= today + dt.timedelta(days=366):
            out["start_date"] = start.isoformat()
            if end and start <= end <= start + dt.timedelta(days=180):
                out["end_date"] = end.isoformat()
    elif intent in ("request_overtime", "request_correction"):
        d = _date(f.get("date"))
        window = 31 if intent == "request_overtime" else 60
        if d and today - dt.timedelta(days=window) <= d <= today:
            out["date"] = d.isoformat()
        keys = ("login", "logout") if intent == "request_overtime" else ("checkin", "checkout")
        for key in keys:
            if (t := _time(f.get(key))):
                out[key] = t
        if intent == "request_overtime":
            if (h := _number(f.get("hours"), Decimal("0.25"), Decimal("12"))) is not None:
                out["hours"] = str(h.quantize(Decimal("0.01")))
            if (ot := _pick(f.get("ot_type"), OT_TYPES)):
                out["ot_type"] = ot
    elif intent in ("request_advance", "request_loan"):
        if (amt := _number(f.get("amount"), Decimal("1"), Decimal("10000000"))) is not None:
            out["amount"] = str(amt.quantize(Decimal("0.01")))
        if intent == "request_loan":
            if (lt := _pick(f.get("loan_type"), LOAN_TYPES)):
                out["loan_type"] = lt
            if (mo := _number(f.get("months"), Decimal("1"), Decimal("60"))) is not None:
                out["months"] = str(int(mo))
    elif intent == "leave_balance":
        if (lt := _pick(f.get("leave_type"), LEAVE_TYPES)):
            out["leave_type"] = lt
    if intent in FORM_INTENTS and (reason := _text(f.get("reason"))):
        out["reason"] = reason
    return {"intent": intent, "page": None, "fields": out}


def rule_match(transcript: str) -> dict[str, Any]:
    text = transcript.strip()
    if _SHIFT_RE.search(text):
        return {"intent": "next_shift", "page": None, "fields": {}}
    if _BALANCE_RE.search(text):
        lt = next((t for t in LEAVE_TYPES if t.split()[0].lower() in text.lower()), None)
        return {"intent": "leave_balance", "page": None, "fields": {"leave_type": lt} if lt else {}}
    for intent, rx in _FORM_HINTS.items():
        if rx.search(text):
            return {"intent": intent, "page": None, "fields": {}}
    for page, words in _PAGE_WORDS.items():
        if re.search(rf"\b({words})\b", text, re.IGNORECASE) or re.search(words, text):
            return {"intent": "navigate", "page": page, "fields": {}}
    return {"intent": "unknown", "page": None, "fields": {}}


def resolve(transcript: str, today: dt.date, lang: str | None) -> dict[str, Any]:
    rules = rule_match(transcript)
    # Questions and page jumps need no extraction; forms do (dates, hours...).
    if rules["intent"] in ("navigate", *ANSWER_INTENTS) and not any(rx.search(transcript) for rx in _FORM_HINTS.values()):
        return {**rules, "source": "rules"}
    prompt = (
        f"TODAY: {today.isoformat()} ({today.strftime('%A')})\n"
        f"UTTERANCE ({lang or 'unknown language'}): {json.dumps(transcript, ensure_ascii=False)}"
    )
    llm = sanitize(
        call_llm(
            prompt,
            SYSTEM_PROMPT,
            openai_model_env="OPENAI_VOICE_MODEL",
            openai_default="gpt-4o-mini",
            anthropic_model_env="ANTHROPIC_VOICE_MODEL",
            anthropic_default="claude-haiku-4-5-20251001",
            max_tokens=400,
            temperature=0,
        ),
        today,
    )
    if llm is not None:
        return {**llm, "source": "llm"}
    return {**rules, "source": "rules"}
