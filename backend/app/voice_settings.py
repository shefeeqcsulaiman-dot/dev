"""Per-company voice settings (Settings > AI & Voice) and the daily cap on
server transcriptions. Stored in the generic AppDataRecord table so no
migration is needed: one "voiceSettings" row per company, one "voiceUsage"
row per company per day."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import AppDataRecord

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "default_lang": "",  # "" = each browser's own language
    "cloud_transcription": True,
    "daily_transcriptions": 200,
}
_SETTINGS = "voiceSettings"
_USAGE = "voiceUsage"


def _row(db: Session, company_id: str, collection: str, key: str) -> AppDataRecord | None:
    return (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection, AppDataRecord.record_key == key)
        .first()
    )


def get_voice_settings(db: Session, company_id: str) -> dict[str, Any]:
    row = _row(db, company_id, _SETTINGS, "company")
    stored: dict[str, Any] = {}
    if row:
        try:
            stored = json.loads(row.payload) or {}
        except (TypeError, ValueError):
            stored = {}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def save_voice_settings(db: Session, company_id: str, values: dict[str, Any]) -> dict[str, Any]:
    merged = {**get_voice_settings(db, company_id), **{k: v for k, v in values.items() if k in DEFAULTS}}
    row = _row(db, company_id, _SETTINGS, "company")
    if row:
        row.payload = json.dumps(merged)
    else:
        db.add(AppDataRecord(company_id=company_id, collection=_SETTINGS, record_key="company", payload=json.dumps(merged)))
    return merged


def transcriptions_today(db: Session, company_id: str, today: dt.date | None = None) -> int:
    row = _row(db, company_id, _USAGE, (today or dt.date.today()).isoformat())
    try:
        return int(json.loads(row.payload).get("count", 0)) if row else 0
    except (TypeError, ValueError):
        return 0


def require_voice_enabled(db: Session, company_id: str) -> dict[str, Any]:
    settings = get_voice_settings(db, company_id)
    if not settings["enabled"]:
        raise HTTPException(status_code=403, detail="Voice is turned off for your company (Settings > AI & Voice)")
    return settings


def reserve_transcription(db: Session, company_id: str) -> None:
    """Checks the server-transcription switch and daily cap, then counts this
    request. Browser speech recognition never reaches here, so it's uncapped."""
    settings = require_voice_enabled(db, company_id)
    if not settings["cloud_transcription"]:
        raise HTTPException(status_code=403, detail="Server transcription is turned off - use Chrome, Edge or Safari for voice input")
    key = dt.date.today().isoformat()
    used = transcriptions_today(db, company_id)
    if used >= int(settings["daily_transcriptions"]):
        raise HTTPException(status_code=429, detail="Today's voice transcription limit is used up - try again tomorrow or ask an admin to raise it")
    row = _row(db, company_id, _USAGE, key)
    if row:
        row.payload = json.dumps({"count": used + 1})
    else:
        db.add(AppDataRecord(company_id=company_id, collection=_USAGE, record_key=key, payload=json.dumps({"count": 1})))
    db.commit()
