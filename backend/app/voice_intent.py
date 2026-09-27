"""Voice commands (Phase 2): map a spoken transcript onto one of the navigation
targets the browser says the user can currently see. The target list comes
from the client and every result is checked against it, so voice can only
reach what a click could; it never saves or posts anything."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from app.ai_client import call_llm

INTENTS = ("navigate", "open_form", "search", "answer", "query", "unknown")
# Rule matches at or above this skip the LLM call entirely.
RULES_CONFIDENT = 90
KIND_FOR_INTENT = {"navigate": {"page", "tab"}, "open_form": {"action"}, "search": {"page", "tab"}, "query": {"query"}}

_FILLER = (
    r"\b(please|can you|could you|i want to|i'd like to|i would like to|let me|for me|the|a|an|my|to|page|screen|section|tab|module)\b"
)
_NAV_VERBS = r"^(open|go to|go|show me|show|take me to|navigate to|switch to|view|display|bring up)\b"
_AR_NAV_VERBS = r"^(افتح|اذهب إلى|اذهب الى|انتقل إلى|انتقل الى|اعرض|أظهر|اظهر|روح|ودني)\s*"
_FORM_WORDS = re.compile(r"\b(new|add|create|record|make|start)\b|جديد|أضف|اضف|إنشاء|انشاء")
_SEARCH_RE = re.compile(r"^(?:search|find|look up|lookup|ابحث عن|ابحث|دور على)\s+(?:for\s+)?(.+?)(?:\s+in\s+(.+))?$", re.IGNORECASE)
_SEARCH_WORDS = re.compile(r"(search|find|look up|lookup|filter)|ابحث|دور على", re.IGNORECASE)
_QUESTION_RE = re.compile(r"^(what|how|why|when|who|which|is|are|do|does|can|should|explain|tell me)\b|^(ما|ماذا|كيف|لماذا|متى|من|هل|كم)\b|\?$|؟$", re.IGNORECASE)

VOICE_INTENT_SYSTEM_PROMPT = (
    "You route spoken commands inside a UAE accounting / HR web app. You are given the user's transcript "
    "(English or Arabic, possibly with speech-recognition errors) and the ONLY targets they may open. "
    "Pick the single best intent:\n"
    '- "navigate": open a page or tab (target kind "page" or "tab").\n'
    '- "open_form": open a blank create form (target kind "action"), for "new/add/create/record ..." requests.\n'
    '- "search": open a page/tab and type a search term into its search box; put the term in "query".\n'
    '- "query": the user asks one of the listed quick questions (target kind "query").\n'
    '- "answer": the user is asking some other question rather than asking to go somewhere; put the question in "query".\n'
    '- "unknown": nothing fits.\n'
    "A short phrase that names a screen (e.g. \"leave requests\", \"overtime eligibility\") is navigate, not search or answer; "
    "use search only when the user says search/find/look up, and answer only for real questions. "
    "Never invent a target id; use only ids from the list. Put up to 3 other plausible target ids in "
    '"alternatives". "confidence" is 0-100. Respond with JSON only, exactly: '
    '{"intent": "", "target": null, "query": null, "confidence": 0, "alternatives": []}'
)


def _norm(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[›>»·|/\\,.!?؟،:;()\"'+\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_command(text: str) -> str:
    text = _norm(text)
    text = re.sub(_NAV_VERBS, "", text).strip()
    text = re.sub(_AR_NAV_VERBS, "", text).strip()
    text = re.sub(_FILLER, " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(phrase: str, target: dict[str, Any]) -> int:
    """0-100 similarity between the spoken phrase and a target's labels."""
    if not phrase:
        return 0
    best = 0
    words = set(phrase.split())
    for raw in (target.get("label"), target.get("alt")):
        if not raw:
            continue
        label = _norm(raw)
        # Tab labels look like "Page › Tab"; the tab name alone is what people say.
        candidates = {label, label.split("  ")[-1]}
        if "›" in raw:
            candidates.add(_norm(raw.split("›")[-1]))
        for cand in candidates:
            if not cand:
                continue
            if cand == phrase:
                return 100
            ratio = difflib.SequenceMatcher(None, phrase, cand).ratio()
            cand_words = set(cand.split())
            overlap = len(words & cand_words) / max(len(cand_words), 1)
            covered = len(words & cand_words) / max(len(words), 1)
            score = max(ratio, 0.55 * overlap + 0.45 * covered)
            if phrase in cand or cand in phrase:
                score = max(score, 0.85 if len(min(phrase, cand, key=len)) >= 4 else 0.6)
            best = max(best, int(score * 100))
    return best


def rule_match(transcript: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Keyword/fuzzy matcher; also the fallback when no LLM is configured."""
    raw = transcript.strip()
    search = _SEARCH_RE.match(_norm(raw))
    if search:
        query, where = search.group(1).strip(), (search.group(2) or "").strip()
        pool = [t for t in targets if t["kind"] in KIND_FOR_INTENT["search"]]
        ranked = sorted(pool, key=lambda t: _score(_strip_command(where), t), reverse=True) if where else []
        if ranked and _score(_strip_command(where), ranked[0]) >= 60:
            return _result("search", ranked[0]["id"], query, _score(_strip_command(where), ranked[0]), [t["id"] for t in ranked[1:4]])
        return _result("unknown", None, query, 30, [t["id"] for t in ranked[:3]])

    phrase = _strip_command(raw)
    wants_form = bool(_FORM_WORDS.search(_norm(raw)))
    scored = sorted(((_score(phrase, t), t) for t in targets), key=lambda st: st[0], reverse=True)
    if wants_form:
        forms = [(s, t) for s, t in scored if t["kind"] == "action"]
        if forms and forms[0][0] >= 50:
            scored = forms + [st for st in scored if st[1]["kind"] != "action"]
    else:
        # Prefer pages/tabs over create-forms when the user didn't ask to create.
        scored = [(s - 10 if t["kind"] == "action" else s, t) for s, t in scored]
        scored.sort(key=lambda st: st[0], reverse=True)

    alternatives = [t["id"] for s, t in scored[1:4] if s >= 40]
    if not scored or scored[0][0] < 55:
        if _QUESTION_RE.search(raw):
            return _result("answer", None, raw, 70, alternatives)
        return _result("unknown", None, None, scored[0][0] if scored else 0, [t["id"] for s, t in scored[:3] if s >= 35])
    best_score, best = scored[0]
    if best["kind"] == "query":
        return _result("query", best["id"], None, best_score, alternatives)
    if _QUESTION_RE.search(raw) and best_score < 80:
        return _result("answer", None, raw, 65, [best["id"]] + alternatives[:2])
    intent = "open_form" if best["kind"] == "action" else "navigate"
    return _result(intent, best["id"], None, best_score, alternatives)


def _result(intent: str, target: str | None, query: str | None, confidence: int, alternatives: list[str]) -> dict[str, Any]:
    return {
        "intent": intent,
        "target": target,
        "query": query,
        "confidence": max(0, min(100, int(confidence))),
        "alternatives": alternatives,
    }


def sanitize(result: Any, targets: list[dict[str, Any]], transcript: str = "") -> dict[str, Any] | None:
    """Coerces an LLM reply onto the whitelist; None if it is unusable."""
    if not isinstance(result, dict) or "error" in result:
        return None
    by_id = {t["id"]: t for t in targets}
    intent = result.get("intent")
    if intent not in INTENTS:
        return None
    target = result.get("target")
    target = target if isinstance(target, str) and target in by_id else None
    if intent in KIND_FOR_INTENT:
        if target is None or by_id[target]["kind"] not in KIND_FOR_INTENT[intent]:
            return None
    else:
        target = None
    query = result.get("query")
    query = str(query).strip()[:200] if isinstance(query, (str, int, float)) and str(query).strip() else None
    if intent in ("search", "answer") and not query:
        return None
    # Don't let the model type into search boxes or divert to Q&A unless the
    # user actually asked to search / asked a question.
    if intent == "search" and not _SEARCH_WORDS.search(transcript):
        return None
    if intent == "answer" and not _QUESTION_RE.search(transcript.strip()):
        return None
    if intent in ("navigate", "open_form", "query"):
        query = None
    try:
        confidence = int(result.get("confidence", 70))
    except (TypeError, ValueError):
        confidence = 70
    alts = result.get("alternatives")
    alternatives = [a for a in alts if isinstance(a, str) and a in by_id and a != target][:3] if isinstance(alts, list) else []
    return _result(intent, target, query, confidence, list(dict.fromkeys(alternatives)))


def resolve_intent(transcript: str, targets: list[dict[str, Any]], lang: str | None = None) -> dict[str, Any]:
    rules = rule_match(transcript, targets)
    if rules["confidence"] >= RULES_CONFIDENT and rules["intent"] in ("navigate", "open_form", "query"):
        return {**rules, "source": "rules"}
    catalog = "\n".join(
        f'{t["id"]} | {t["kind"]} | {t["label"]}' + (f' ({t["alt"]})' if t.get("alt") else "") for t in targets
    )
    prompt = f"TARGETS (id | kind | label):\n{catalog}\n\nTRANSCRIPT ({lang or 'unknown language'}): {json.dumps(transcript, ensure_ascii=False)}"
    llm = sanitize(
        call_llm(
            prompt,
            VOICE_INTENT_SYSTEM_PROMPT,
            openai_model_env="OPENAI_VOICE_MODEL",
            openai_default="gpt-4o-mini",
            anthropic_model_env="ANTHROPIC_VOICE_MODEL",
            anthropic_default="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0,
        ),
        targets,
        transcript,
    )
    if llm is not None:
        return {**llm, "source": "llm"}
    return {**rules, "source": "rules"}
