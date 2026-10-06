"""Main app + HRMS voice: settings (Settings > AI & Voice), transcription
fallback, voice commands (pick a page/tab/action from what the user can see),
Dictate on forms (transcript -> field values), and the spoken daily briefing.
Voice only opens or fills things -- nothing here saves or submits."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import voice
from app.ai_client import call_llm
from app.auth_principal import Principal, get_current_principal, require_principal_permission, resolve_active_branch
from app.database import get_db
from app.dependencies import require_module
from app.limiter import limiter
from app.models import Company

# Voice is a helper in every app (main, HRMS, ESS), not the AI Assistant: company module switch
# only -- its routes check their own access (settings admin-only, briefing needs reports:view), and
# anything it drafts is saved through the normal role-checked paths.
router = APIRouter(prefix="/ai", tags=["voice"], dependencies=[Depends(require_module("ai", check_role=False))])


def _llm(prompt: str, system: str, max_tokens: int = 600) -> dict:
    result = call_llm(
        prompt, system,
        openai_model_env="OPENAI_VOICE_MODEL", openai_default="gpt-4o-mini",
        anthropic_model_env="ANTHROPIC_VOICE_MODEL", anthropic_default="claude-haiku-4-5-20251001",
        max_tokens=max_tokens, temperature=0,
    )
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(status_code=502, detail="The voice assistant couldn't be reached — please try again")
    return result


# ── settings ──

class VoiceSettingsIn(BaseModel):
    enabled: bool
    default_lang: Literal["", "en-US", "ar-AE"] = ""
    cloud_transcription: bool = True
    daily_transcriptions: int = Field(200, ge=0, le=10000)


@router.get("/voice-settings")
def get_voice_settings(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict:
    return voice.voice_state(db, principal.company_id)


@router.put("/voice-settings")
def put_voice_settings(payload: VoiceSettingsIn, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can change voice settings")
    voice.save_settings(db, principal.company_id, payload.model_dump())
    return voice.voice_state(db, principal.company_id)


@router.post("/transcribe")
@limiter.limit("20/minute")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("en-US"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    return {"text": await voice.transcribe_upload(db, principal.company_id, file, lang)}


# ── voice commands ──

class VoiceTarget(BaseModel):
    id: str = Field(..., max_length=40)
    kind: Literal["page", "tab", "action", "query"]
    label: str = Field(..., max_length=160)
    alt: str | None = Field(None, max_length=160)


class VoiceIntentIn(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=1000)
    lang: Literal["en", "ar"] = "en"
    targets: list[VoiceTarget] = Field(default_factory=list, max_length=400)


_INTENT_SYSTEM = (
    "You route a spoken command in a UAE accounting/HR web app to one of the on-screen targets listed. "
    "Respond with JSON only: {\"intent\": ..., \"target\": <target id or null>, \"query\": <string or null>, "
    "\"confidence\": 0-100, \"alternatives\": [up to 3 other target ids]}. Intents:\n"
    "- navigate: open a page or tab target.\n"
    "- open_form: start creating something; target must be an 'action' target.\n"
    "- search: find something by name/number inside a page; target is the page or tab, query is the search text.\n"
    "- query: the user asks one of the 'query' targets (e.g. who is absent, daily briefing).\n"
    "- answer: a general question about their data or how to do something that no target answers; query is the question.\n"
    "- unknown: none of the above.\n"
    "When the user names a report, page or tab that is listed, navigate to that most specific tab — only use search "
    "for finding a particular record (a customer, supplier, invoice number or name). If nothing listed really "
    "matches what they asked for, give a low confidence rather than a loosely related page.\n"
    "\"target\" and \"alternatives\" are bare ids exactly as listed before the [kind], e.g. \"p2\" or \"a19\". "
    "The user may speak English or Arabic; targets are in English. "
    "Keep search queries in the user's words."
)


@router.post("/voice-intent")
@limiter.limit("30/minute")
def voice_intent(request: Request, payload: VoiceIntentIn, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict:
    voice.assert_voice_on(db, principal.company_id)
    targets = {t.id: t for t in payload.targets}
    listing = "\n".join(f"{t.id} [{t.kind}] {t.label}" + (f" ({t.alt})" if t.alt else "") for t in payload.targets)
    result = _llm(f"Targets:\n{listing or '(none)'}\n\nUser said: {payload.transcript.strip()}", _INTENT_SYSTEM)

    by_label = {t.label.lower(): t.id for t in payload.targets}

    def resolve(value: Any) -> str | None:
        # Models sometimes echo "a19 [action] …" or the label instead of the bare id.
        s = str(value or "").strip()
        if s in targets:
            return s
        m = re.match(r"([a-z]\d+)\b", s)
        if m and m.group(1) in targets:
            return m.group(1)
        return by_label.get(s.lower())

    intent = result.get("intent") if result.get("intent") in ("navigate", "open_form", "search", "query", "answer") else "unknown"
    target = resolve(result.get("target"))
    try:
        confidence = max(0, min(100, int(result.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0
    alternatives = []
    for a in result.get("alternatives") or []:
        rid = resolve(a)
        if rid and rid != target and rid not in alternatives:
            alternatives.append(rid)
    alternatives = alternatives[:3]
    query = str(result.get("query") or "").strip()[:200] or None
    if intent == "answer" and not query:
        query = payload.transcript.strip()[:500]
    # The frontend runs a target by its kind, so keep intent and kind consistent.
    if target:
        kind = targets[target].kind
        if kind == "query":
            intent = "query"
        elif intent == "query" or (intent == "open_form" and kind != "action"):
            intent = "navigate"
    elif intent in ("navigate", "open_form", "search", "query"):
        confidence = 0
    return {"intent": intent, "target": target, "query": query, "confidence": confidence, "alternatives": alternatives}


# ── Dictate on forms ──

_TEXT, _DATE, _NUM, _CHOICE, _TRN, _EMAIL, _PHONE = "text", "date", "number", "choice", "trn", "email", "phone"
FORMS: dict[str, dict[str, Any]] = {
    "expense": {"fields": {"date": _DATE, "category": _CHOICE, "vendor": _CHOICE, "description": _TEXT, "amount": _NUM, "vat": _NUM}, "lines": False,
                "about": "an expense (amount excludes VAT; vat is the VAT amount, not a rate)"},
    "purchase": {"fields": {"supplier": _CHOICE, "reference": _TEXT, "date": _DATE, "notes": _TEXT}, "lines": True,
                 "about": "a purchase from a supplier"},
    "sales_invoice": {"fields": {"customer": _CHOICE, "date": _DATE, "due_date": _DATE, "po": _TEXT, "reference": _TEXT}, "lines": True,
                      "about": "a sales invoice (convert 'due in N days' into due_date)"},
    "customer": {"fields": {"name": _TEXT, "trn": _TRN, "emirate": _CHOICE, "address": _TEXT, "email": _EMAIL, "phone": _PHONE}, "lines": False,
                 "about": "a new customer (spoken emails like 'info at alnoor dot ae' become info@alnoor.ae)"},
    "vendor": {"fields": {"name": _TEXT, "trn": _TRN, "category": _CHOICE, "email": _EMAIL, "phone": _PHONE, "address": _TEXT}, "lines": False,
               "about": "a new vendor/supplier (spoken emails become normal email addresses)"},
}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER = re.compile(r"^(please\s+)?(select|choose)\b|^$", re.I)  # "Please Select" rows are not real options


class VoiceDraftIn(BaseModel):
    form: str
    transcript: str = Field(..., min_length=1, max_length=2000)
    lang: Literal["en", "ar"] = "en"
    choices: dict[str, list[str]] = Field(default_factory=dict)
    today: str | None = None


def _num(v: Any) -> float | None:
    try:
        n = Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return float(n) if n > 0 else None


_PARTY_FIELDS = {"customer", "supplier", "vendor"}
_NAME_NOISE = {"llc", "fze", "fzc", "fzco", "co", "company", "trading", "the", "and", "est", "ltd", "limited", "group", "general"}


def _said(name: str, transcript: str) -> bool:
    """A customer/supplier/vendor must actually be named — models otherwise pick one from the list."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in _NAME_NOISE]
    spoken = set(re.findall(r"[a-z0-9]+", transcript.lower()))
    return any(w in spoken for w in words)


def _match(value: str, options: list[str]) -> str | None:
    v = value.strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    return None


@router.post("/voice-draft")
@limiter.limit("30/minute")
def voice_draft(request: Request, payload: VoiceDraftIn, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> dict:
    voice.assert_voice_on(db, principal.company_id)
    spec = FORMS.get(payload.form)
    if not spec:
        raise HTTPException(status_code=400, detail="Unknown form")
    choices = {
        k: [str(o).strip()[:120] for o in v if str(o).strip() and not _PLACEHOLDER.match(str(o).strip(" -—"))][:300]
        for k, v in payload.choices.items() if isinstance(v, list)
    }
    today = payload.today if payload.today and _ISO.match(payload.today) else date.today().isoformat()

    field_lines = []
    for name, kind in spec["fields"].items():
        hint = {"date": "YYYY-MM-DD", "number": "number", "trn": "15-digit UAE TRN", "email": "email", "phone": "phone"}.get(kind, "text")
        if kind == _CHOICE and choices.get(name):
            hint = "one of: " + "; ".join(choices[name])
        field_lines.append(f"- {name}: {hint}")
    lines_rule = ""
    if spec["lines"]:
        products = choices.get("product") or []
        lines_rule = ("\nAlso \"lines\": [{\"product\", \"quantity\", \"price\"}] for each item mentioned"
                      + (" — use the exact product name from: " + "; ".join(products) if products else "") + ".")
    system = (
        f"Fill {spec['about']} from what the user said. Today is {today}. Respond with JSON only: "
        "{\"fields\": {...}" + (", \"lines\": [...]" if spec["lines"] else "") + "}. Fields:\n" + "\n".join(field_lines)
        + lines_rule + "\nOnly include values the user actually said or clearly implied — leave a field out entirely "
        "if they didn't mention it, and never pick an option just because it is listed. For choice fields the user "
        "did mention, use the matching listed option exactly; if none matches, give what they said. The user may "
        "speak English or Arabic."
    )
    result = _llm(f"User said: {payload.transcript.strip()}", system, max_tokens=900)

    fields: dict[str, Any] = {}
    unmatched: dict[str, str] = {}
    for name, value in (result.get("fields") or {}).items():
        kind = spec["fields"].get(name)
        if kind is None or value is None or str(value).strip() == "":
            continue
        s = str(value).strip()
        if kind == _DATE:
            if _ISO.match(s):
                fields[name] = s
        elif kind == _NUM:
            n = _num(value)
            if n is not None:
                fields[name] = n
        elif kind == _TRN:
            digits = re.sub(r"\D", "", s)
            if len(digits) == 15:
                fields[name] = digits
        elif kind == _EMAIL:
            s = s.replace(" ", "").lower()
            if _EMAIL_RE.match(s):
                fields[name] = s
        elif kind == _CHOICE:
            if name in _PARTY_FIELDS and payload.lang == "en" and not _said(s, payload.transcript):
                continue
            options = choices.get(name)
            hit = _match(s, options) if options else None
            if hit:
                fields[name] = hit
            elif options:
                unmatched[name] = s[:120]
            else:
                fields[name] = s[:200]
        else:
            fields[name] = s[:300]

    lines = []
    if spec["lines"]:
        products = choices.get("product") or []
        for row in (result.get("lines") or [])[:30]:
            if not isinstance(row, dict) or not str(row.get("product") or "").strip():
                continue
            name = str(row["product"]).strip()[:160]
            hit = _match(name, products)
            lines.append({
                "product": hit or name,
                "quantity": _num(row.get("quantity")) or 1,
                "price": _num(row.get("price")),
                "matched": "yes" if hit else "no",
            })
    return {"fields": fields, "lines": lines, "unmatched": unmatched}


# ── daily briefing ──

def _money(v: Any, currency: str) -> str:
    try:
        return f"{currency} {Decimal(str(v)):,.2f}"
    except (InvalidOperation, ValueError):
        return f"{currency} 0.00"


def _next_vat_due(today: date) -> date:
    """Standard calendar-quarter VAT period: return due on the 28th of Jan/Apr/Jul/Oct."""
    for year in (today.year, today.year + 1):
        for month in (1, 4, 7, 10):
            if date(year, month, 28) >= today:
                return date(year, month, 28)
    return date(today.year + 1, 1, 28)


@router.get("/briefing")
@limiter.limit("30/minute")
def briefing(
    request: Request,
    lang: Literal["en", "ar"] = Query("en"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_principal_permission("reports:view")),
) -> dict:
    from app.routers.reports import _build_dashboard, _cached_or_build

    voice.assert_voice_on(db, principal.company_id)
    company = db.get(Company, principal.company_id)
    currency = (company.currency if company else None) or "AED"
    branch_id = None if principal.can_cross_branch("reports") else resolve_active_branch(principal, None)
    d = _cached_or_build(f"dashboard:{principal.company_id}:{branch_id or 'all'}", 60,
                         lambda: _build_dashboard(db, principal.company_id, branch_id))
    k, status = d.get("kpis", {}), d.get("invoice_status", {})
    purchases, staff = d.get("purchase_summary", {}), d.get("staff_today", {})
    overdue = status.get("overdue", {})
    due = _next_vat_due(date.today())
    ar = lang == "ar"

    items = []
    open_n = int(k.get("open_invoice_count") or 0)
    if ar:
        text = f"{open_n} فاتورة مفتوحة بقيمة {_money(k.get('open_invoice_amount'), currency)}"
        if overdue.get("count"):
            text += f"، منها {overdue['count']} متأخرة بقيمة {_money(overdue.get('amount'), currency)}"
    else:
        text = f"{open_n} open invoice{'s' if open_n != 1 else ''} worth {_money(k.get('open_invoice_amount'), currency)}"
        if overdue.get("count"):
            text += f", {overdue['count']} overdue worth {_money(overdue.get('amount'), currency)}"
    items.append({"key": "overdue", "page": "sales", "text": text + "."})

    unpaid = int(purchases.get("pending_count") or 0)
    items.append({"key": "purchases", "page": "purchase", "text": (
        f"{unpaid} فاتورة مشتريات غير مدفوعة." if ar else f"{unpaid} purchase{'s' if unpaid != 1 else ''} still unpaid.")})

    vat = Decimal(str(k.get("vat_payable") or 0))
    if ar:
        vat_text = (f"صافي ضريبة القيمة المضافة المستحقة {_money(vat, currency)}" if vat >= 0 else f"رصيد ضريبة قابل للاسترداد {_money(-vat, currency)}")
        vat_text += f"، والإقرار القادم مستحق في {due.isoformat()}."
    else:
        vat_text = (f"Net VAT payable is {_money(vat, currency)}" if vat >= 0 else f"You have {_money(-vat, currency)} of VAT to reclaim")
        vat_text += f"; the next return is due by {due.strftime('%d %B')}."
    items.append({"key": "vat", "page": "reports", "text": vat_text})

    total = int(staff.get("total") or 0)
    if total:
        present, leave, absent = int(staff.get("present") or 0), int(staff.get("leave") or 0), int(staff.get("absent") or 0)
        items.append({"key": "staff", "page": "hrms", "text": (
            f"الموظفون: {present} حاضر من {total}، {leave} في إجازة، {absent} غائب." if ar
            else f"Staff: {present} of {total} in today, {leave} on leave, {absent} absent.")})

    exceptions = int((d.get("module_counts") or {}).get("exception_count") or 0)
    if exceptions:
        items.append({"key": "exceptions", "page": "exceptions", "text": (
            f"{exceptions} استثناء مفتوح بحاجة إلى مراجعة." if ar
            else f"{exceptions} open exception{'s' if exceptions != 1 else ''} need review.")})

    name = (principal.display_name or "").split(" ")[0]
    intro = (f"صباح الخير {name}، هذا ملخص اليوم:" if ar else f"Good morning{', ' + name if name else ''}. Here's your day:")
    return {"intro": intro, "items": items, "text": " ".join([intro] + [i["text"] for i in items])}
