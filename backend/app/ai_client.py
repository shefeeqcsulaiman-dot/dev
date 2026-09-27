"""Shared OpenAI/Anthropic JSON-mode chat completion helper.

Used by ai.py (main AI Assistant) and hr_ai.py (HRMS AI features) — both
call this with their own model env var names/defaults so either can be
tuned independently without touching the other.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from typing import Any


def _openai_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _anthropic_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def call_llm(
    prompt: str,
    system: str,
    *,
    openai_model_env: str,
    openai_default: str,
    anthropic_model_env: str,
    anthropic_default: str,
    max_tokens: int = 2500,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call OpenAI (preferred) or Anthropic in JSON mode. Returns parsed dict,
    or {"error": "..."} if no key is configured or the request fails."""
    openai_key = _openai_key()
    anthropic_key = _anthropic_key()

    if openai_key:
        payload = {
            "model": os.environ.get(openai_model_env, openai_default),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"error": f"OpenAI request failed ({exc.code}): {exc.reason}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"error": f"OpenAI request failed: {exc}"}
        raw = result["choices"][0]["message"]["content"].strip()
    elif anthropic_key:
        payload = {
            "model": os.environ.get(anthropic_model_env, anthropic_default),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"error": f"Anthropic request failed ({exc.code}): {exc.reason}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"error": f"Anthropic request failed: {exc}"}
        raw = result["content"][0]["text"].strip()
    else:
        return {"error": "No AI API key configured. Add OPENAI_API_KEY or ANTHROPIC_API_KEY to .env"}

    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Previously returned {"raw": raw} with no "error" key — every caller
        # that checks `if result.get("error")` (both ai.py's assist() and
        # every hr_ai.py endpoint that forwards this dict straight to the
        # frontend) would treat a truncated/malformed response as a normal,
        # successful, empty-shaped result. For hr_ai.py's compliance_check in
        # particular, that meant a truncated response silently rendered as
        # "0 compliance issues found" — false negative on a real check. The
        # most common cause is max_tokens cutting the JSON off mid-object.
        return {"error": "AI response was not valid JSON (it may have been truncated) — try again or reduce the amount of data being analyzed.", "raw": raw}


def transcribe_audio(audio: bytes, filename: str, content_type: str, lang: str | None = None) -> dict[str, Any]:
    """Speech-to-text via OpenAI's transcription API. Returns {"text": ...}
    or {"error": ...}; audio is only forwarded, never stored."""
    openai_key = _openai_key()
    if not openai_key:
        return {"error": "Server transcription needs OPENAI_API_KEY; use a browser with built-in speech recognition (Chrome, Edge, Safari)."}
    boundary = "----taxflowvoice" + os.urandom(8).hex()
    fields = {"model": os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")}
    if lang:
        fields["language"] = lang
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "audio.webm")
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    body += audio + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={"Authorization": f"Bearer {openai_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"Transcription failed ({exc.code}): {exc.reason}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"error": f"Transcription failed: {exc}"}
    return {"text": str(result.get("text", "")).strip()}
