"""Shared voice plumbing for the main app, HRMS and ESS: per-company voice
settings (Settings > AI & Voice), the daily server-transcription cap, and
OpenAI Whisper transcription for browsers without built-in speech recognition."""

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

import app.timezone_utils as timezone_utils
from app.ai_client import _anthropic_key, _openai_key
from app.dependencies import company_allows_module
from app.models import AppDataRecord, Company, Employee

SETTINGS_COLLECTION = "voiceSettings"
SETTINGS_KEY = "settings"
LANGS = ("", "en-US", "ar-AE")
DEFAULTS = {"enabled": True, "default_lang": "", "cloud_transcription": True, "daily_transcriptions": 200}
MAX_AUDIO_BYTES = 10 * 1024 * 1024
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
FALLBACK_TRANSCRIBE_MODEL = "whisper-1"
logger = logging.getLogger(__name__)


def ai_key_available() -> bool:
    return bool(_openai_key() or _anthropic_key())


def _record(db: Session, company_id: str) -> AppDataRecord | None:
    return db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id,
        AppDataRecord.collection == SETTINGS_COLLECTION,
        AppDataRecord.record_key == SETTINGS_KEY,
    ).first()


def _local_date(db: Session, company_id: str) -> str:
    country = db.query(Company.country).filter(Company.id == company_id).scalar()
    return (datetime.now(UTC) + timezone_utils.company_utc_offset(country)).date().isoformat()


def load_settings(db: Session, company_id: str) -> dict:
    """Stored settings merged over the defaults, plus today's transcription count."""
    rec = _record(db, company_id)
    data = {}
    if rec:
        try:
            data = json.loads(rec.payload) or {}
        except (TypeError, ValueError):
            data = {}
    out = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    usage = data.get("usage") or {}
    out["transcriptions_today"] = int(usage.get("count", 0)) if usage.get("date") == _local_date(db, company_id) else 0
    return out


def save_settings(db: Session, company_id: str, values: dict) -> dict:
    rec = _record(db, company_id)
    data = {}
    if rec:
        try:
            data = json.loads(rec.payload) or {}
        except (TypeError, ValueError):
            data = {}
    data.update({k: values[k] for k in DEFAULTS if k in values})
    if rec:
        rec.payload = json.dumps(data)
    else:
        db.add(AppDataRecord(company_id=company_id, collection=SETTINGS_COLLECTION, record_key=SETTINGS_KEY, payload=json.dumps(data)))
    db.commit()
    return load_settings(db, company_id)


def voice_state(db: Session, company_id: str) -> dict:
    """What the frontend needs to show or hide mics: `enabled` is the company
    switch, `available` also requires the AI module and an AI key."""
    s = load_settings(db, company_id)
    modules = db.query(Company.modules_enabled).filter(Company.id == company_id).scalar()
    s["available"] = bool(s["enabled"]) and company_allows_module(modules, "ai") and ai_key_available()
    return s


def assert_voice_on(db: Session, company_id: str) -> dict:
    s = voice_state(db, company_id)
    if not s["available"]:
        raise HTTPException(status_code=403, detail="Voice isn't enabled for your company")
    return s


def _consume_transcription(db: Session, company_id: str, cap: int) -> None:
    rec = _record(db, company_id)
    data = json.loads(rec.payload) if rec else {}
    today = _local_date(db, company_id)
    usage = data.get("usage") or {}
    count = int(usage.get("count", 0)) if usage.get("date") == today else 0
    if count >= cap:
        raise HTTPException(status_code=429, detail="Today's voice transcription limit is used up — try Chrome, Edge or Safari, which don't need it")
    data["usage"] = {"date": today, "count": count + 1}
    if rec:
        rec.payload = json.dumps(data)
    else:
        db.add(AppDataRecord(company_id=company_id, collection=SETTINGS_COLLECTION, record_key=SETTINGS_KEY, payload=json.dumps(data)))
    db.commit()


_VOCAB_TTL = 600
_vocab_cache: dict[str, tuple[float, str]] = {}


def _names(db: Session, company_id: str, collection: str, limit: int) -> list[str]:
    rows = (
        db.query(AppDataRecord.payload)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
        .limit(limit).all()
    )
    out = []
    for (payload,) in rows:
        try:
            name = str((json.loads(payload or "{}") or {}).get("name") or "").strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if name:
            out.append(name)
    return out


def transcription_prompt(db: Session, company_id: str) -> str:
    """Names the transcriber should expect (staff, customers, suppliers, shifts) -- the main source of misheard words."""
    cached = _vocab_cache.get(company_id)
    if cached and cached[0] > time.time():
        return cached[1]
    staff = [r[0] for r in db.query(Employee.full_name).filter(Employee.company_id == company_id, Employee.status == "active").limit(150).all() if r[0]]
    names: list[str] = []
    seen: set[str] = set()
    for name in staff + _names(db, company_id, "customers", 100) + _names(db, company_id, "vendors", 100) + _names(db, company_id, "rotaShifts", 40):
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    prompt = "Business app for a UAE company: VAT, TRN, WPS, payroll, payslip, rota, shift, overtime, leave, invoice."
    budget = 800 - len(prompt)
    picked = []
    for name in names:
        if len(name) + 2 > budget:
            break
        picked.append(name)
        budget -= len(name) + 2
    if picked:
        prompt += " Names: " + ", ".join(picked) + "."
    _vocab_cache[company_id] = (time.time() + _VOCAB_TTL, prompt)
    return prompt


async def transcribe_upload(db: Session, company_id: str, file: UploadFile, lang: str) -> str:
    """Checks the company switch, the cloud-transcription toggle and daily cap,
    then sends the audio to Whisper. Audio is never stored."""
    settings = assert_voice_on(db, company_id)
    if not settings["cloud_transcription"]:
        raise HTTPException(status_code=403, detail="Voice needs Chrome, Edge or Safari here — server transcription is turned off")
    key = _openai_key()
    if not key:
        raise HTTPException(status_code=503, detail="Voice input isn't available in this browser — try Chrome, Edge or Safari")
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if not audio:
        raise HTTPException(status_code=400, detail="No audio received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too long")
    _consume_transcription(db, company_id, int(settings["daily_transcriptions"]))

    filename = re.sub(r"[^A-Za-z0-9._-]", "", file.filename or "voice.webm") or "voice.webm"
    content_type = file.content_type or "application/octet-stream"
    fields = {"language": "ar" if lang.startswith("ar") else "en", "prompt": transcription_prompt(db, company_id)}

    def _send(model: str) -> dict:
        boundary = uuid.uuid4().hex
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, v in {"model": model, **fields}.items()
        ) + (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + audio + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode())

    model = os.environ.get("OPENAI_TRANSCRIBE_MODEL") or TRANSCRIBE_MODEL
    try:
        # Off the event loop: a slow transcription must not stall every other request on this worker.
        try:
            result = await asyncio.to_thread(_send, model)
        except urllib.error.HTTPError as exc:
            # Model refused (bad name, no access for this key): fall back rather than break voice.
            if exc.code not in (400, 403, 404) or model == FALLBACK_TRANSCRIBE_MODEL:
                raise
            logger.warning("Transcription model %s refused (%s); falling back to %s", model, exc.code, FALLBACK_TRANSCRIBE_MODEL)
            result = await asyncio.to_thread(_send, FALLBACK_TRANSCRIBE_MODEL)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="Transcription failed — please try again")
    return str(result.get("text") or "").strip()
