"""Copies each trial request (POST /auth/trial-request) to the outreach dashboard
(https://outreach.etaxflow.com, Website Requests page), so the sales team sees it there too.

Off unless INTAKE_SECRET is set (the same value as on the dashboard, which uses it to
trust this server's requests). Runs after the response as a FastAPI background task:
a slow or failing dashboard never delays or breaks the request; failures are only logged.
Self-service sign-ups (/auth/register) are not copied.
"""
import logging

import httpx

from app.config import get_settings

log = logging.getLogger("taxflow.outreach_forward")

_TIMEOUT = 5.0


def forward_trial(fields: dict, page: str) -> None:
    settings = get_settings()
    if not settings.intake_secret or not settings.outreach_intake_url:
        return
    body = {k: v for k, v in fields.items() if v not in (None, "")}
    body.update(kind="trial", page=page)
    try:
        resp = httpx.post(
            settings.outreach_intake_url,
            json=body,
            headers={"X-Intake-Secret": settings.intake_secret},
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 300:
            log.warning("outreach dashboard refused a trial request (%s): %s", resp.status_code, resp.text[:300])
    except httpx.HTTPError as exc:
        log.warning("could not reach the outreach dashboard: %s", exc)
