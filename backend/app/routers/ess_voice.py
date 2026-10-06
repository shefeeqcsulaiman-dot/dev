"""ESS voice assistant: settings, server-side transcription fallback, and
transcript -> intent. Nothing is ever submitted from here -- the frontend opens
the matching form pre-filled for the employee to check and submit."""

import re
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import app.timezone_utils as timezone_utils
from app import voice
from app.ai_client import call_llm
from app.database import get_db
from app.limiter import limiter
from app.models import Company, Employee
from app.routers.ess import _employee_app_data_records, ess_bearer, ess_leave_balance

router = APIRouter(prefix="/ess", tags=["ess voice"])

PAGES = ["dashboard", "profile", "attendance", "leave", "requests", "rota", "tasks", "gps", "payslips", "team"]
LEAVE_TYPES = [
    "Annual Leave", "Sick Leave", "Casual Leave", "Emergency Leave", "Maternity Leave",
    "Paternity Leave", "Unpaid Leave", "Lieu Days", "Hajj Leave", "Work From Home",
]
OT_TYPES = ["normal", "ramadan", "weekend", "holiday"]
LOAN_TYPES = ["Personal Loan", "Emergency Loan", "Medical Loan", "Home Furnishing Loan", "Education Loan", "Vehicle Loan"]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _local_today(db: Session, emp: Employee) -> date:
    country = db.query(Company.country).filter(Company.id == emp.company_id).scalar()
    return (datetime.now(UTC) + timezone_utils.company_utc_offset(country)).date()


@router.get("/voice-settings")
def voice_settings(request: Request, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    s = voice.voice_state(db, emp.company_id)
    return {"enabled": s["available"], "default_lang": s["default_lang"]}


@router.post("/voice-transcribe")
@limiter.limit("20/minute")
async def voice_transcribe(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("en-US"),
    db: Session = Depends(get_db),
) -> dict:
    """Fallback for browsers without built-in speech recognition (e.g. Firefox)."""
    emp = ess_bearer(request, db)
    return {"text": await voice.transcribe_upload(db, emp.company_id, file, lang)}


class VoiceIntentIn(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=1000)
    lang: str = "en"


_SYSTEM = (
    "You turn an employee's spoken request in an HR self-service app into JSON. "
    "Respond with JSON only: {\"intent\": ..., \"page\": ..., \"fields\": {...}}. Intents:\n"
    "- navigate: open a page. page is one of: " + ", ".join(PAGES) + ".\n"
    "- apply_leave: fields start_date, end_date (YYYY-MM-DD), leave_type (one of: " + ", ".join(LEAVE_TYPES) + "), reason.\n"
    "- request_overtime: fields date (YYYY-MM-DD, not in the future), hours (number), login, logout (HH:MM 24h), "
    "ot_type (one of: " + ", ".join(OT_TYPES) + "), reason.\n"
    "- request_correction: fields date, checkin, checkout (HH:MM 24h), reason.\n"
    "- request_advance: fields amount (number), reason.\n"
    "- request_loan: fields loan_type (one of: " + ", ".join(LOAN_TYPES) + "), amount, months (1-60), reason.\n"
    "- leave_balance: the employee asks how much leave they have; fields leave_type if one is named.\n"
    "- next_shift: the employee asks when they work next.\n"
    "- unknown: anything else.\n"
    "Only include fields the employee actually said or clearly implied; resolve relative dates "
    "(today, yesterday, next Monday) against the date given. The employee may speak English or Arabic; "
    "field values stay in the English forms listed above except reason, which keeps the employee's words."
)


def _clean_fields(intent: str, raw: dict) -> dict:
    """Keep only well-formed values; the form validates again before submit."""
    out: dict = {}
    for k, v in (raw or {}).items():
        if v is None or v == "":
            continue
        if k in ("date", "start_date", "end_date") and isinstance(v, str) and _DATE.match(v):
            out[k] = v
        elif k in ("login", "logout", "checkin", "checkout") and isinstance(v, str) and _TIME.match(v):
            out[k] = v
        elif k in ("hours", "amount", "months"):
            try:
                n = float(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[k] = int(n) if k == "months" else n
        elif k == "leave_type" and v in LEAVE_TYPES:
            out[k] = v
        elif k == "ot_type" and v in OT_TYPES:
            out[k] = v
        elif k == "loan_type" and v in LOAN_TYPES:
            out[k] = v
        elif k == "reason" and isinstance(v, str):
            out[k] = v.strip()[:500]
    return out


def _balance_answer(request: Request, db: Session, leave_type: str | None, arabic: bool) -> str:
    by_type = ess_leave_balance(request, db).get("by_type", {})
    lt = leave_type if leave_type in by_type else "Annual Leave"
    b = by_type.get(lt)
    if not b:
        return "لا يوجد رصيد إجازات مسجل لك." if arabic else "No leave balance is set up for you yet."
    if arabic:
        return f"رصيدك من {lt}: {b['remaining']} يوم متبقٍ من أصل {b['entitlement']}."
    return f"You have {b['remaining']} of {b['entitlement']} days of {lt} left."


def _next_shift_answer(db: Session, emp: Employee, today: date, arabic: bool) -> str:
    end = (today + timedelta(days=30)).isoformat()
    rows = sorted(
        (a for a in _employee_app_data_records(db, emp.company_id, "rotaAssignments")
         if a.get("employee_id") == emp.employee_no and today.isoformat() <= (a.get("date") or "") <= end
         and not re.match(r"^(off|leave|holiday)$", str(a.get("type") or a.get("code") or ""), re.I)),
        key=lambda a: a.get("date") or "",
    )
    if not rows:
        return "لا توجد مناوبات مجدولة خلال الثلاثين يومًا القادمة." if arabic else "You have no shifts scheduled in the next 30 days."
    s = rows[0]
    when = "today" if s["date"] == today.isoformat() else date.fromisoformat(s["date"]).strftime("%A %d %B")
    times = f" from {s['start']} to {s['end']}" if s.get("start") and s.get("end") else ""
    label = s.get("type") or s.get("code") or "shift"
    if arabic:
        return f"مناوبتك القادمة ({label}) بتاريخ {s['date']}" + (f" من {s['start']} إلى {s['end']}" if times else "") + "."
    return f"Your next shift ({label}) is {when}{times}."


@router.post("/voice-intent")
@limiter.limit("30/minute")
def voice_intent(request: Request, payload: VoiceIntentIn, db: Session = Depends(get_db)) -> dict:
    emp = ess_bearer(request, db)
    voice.assert_voice_on(db, emp.company_id)
    today = _local_today(db, emp)
    yesterday = today - timedelta(days=1)
    prompt = (
        f"Today is {today.strftime('%A')} {today.isoformat()}; yesterday was {yesterday.strftime('%A')} {yesterday.isoformat()}.\n"
        f"Employee said: {payload.transcript.strip()}"
    )
    result = call_llm(
        prompt, _SYSTEM,
        openai_model_env="OPENAI_VOICE_MODEL", openai_default="gpt-4o-mini",
        anthropic_model_env="ANTHROPIC_VOICE_MODEL", anthropic_default="claude-haiku-4-5-20251001",
        max_tokens=400, temperature=0,
    )
    if result.get("error"):
        raise HTTPException(status_code=502, detail="The voice assistant couldn't be reached — please try again")

    intent = str(result.get("intent") or "unknown")
    raw_fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
    arabic = payload.lang == "ar"
    if intent == "leave_balance":
        return {"intent": intent, "answer": _balance_answer(request, db, raw_fields.get("leave_type"), arabic)}
    if intent == "next_shift":
        return {"intent": intent, "answer": _next_shift_answer(db, emp, today, arabic)}
    if intent == "navigate":
        page = result.get("page")
        return {"intent": intent, "page": page} if page in PAGES else {"intent": "unknown"}
    if intent in ("apply_leave", "request_overtime", "request_correction", "request_advance", "request_loan"):
        return {"intent": intent, "fields": _clean_fields(intent, raw_fields)}
    return {"intent": "unknown"}
