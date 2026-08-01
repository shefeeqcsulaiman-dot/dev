"""Thin REST client for ZKTeco BioTime 9.5 servers (customer-hosted device
management software — see docs the user provided: Get JWT Auth Token, Use
Auth Token, Device/Terminal API).

Stateless by design: every call takes base_url + token explicitly so this
module never has to know which company or device it's serving — that
scoping lives entirely in biotime_sync.py / the calling endpoint.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

_TIMEOUT = 15.0


class BioTimeError(Exception):
    """Raised for any BioTime request failure — message is safe to show
    the user verbatim in the "Test Connection" UI."""


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def get_token(base_url: str, username: str, password: str) -> str:
    """POST /jwt-api-token-auth/ — returns the bare JWT string (no "JWT "
    prefix; that's added by callers when building the Authorization header,
    matching the docs' "Authorization: JWT ey....." convention)."""
    url = f"{_base(base_url)}/jwt-api-token-auth/"
    try:
        resp = httpx.post(
            url,
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as exc:
        raise BioTimeError(f"Could not reach BioTime server at {base_url}: {exc}") from exc
    if resp.status_code != 200:
        raise BioTimeError(f"BioTime login failed ({resp.status_code}): {resp.text[:300]}")
    token = resp.json().get("token")
    if not token:
        raise BioTimeError("BioTime login succeeded but returned no token")
    return token


def list_terminals(base_url: str, token: str) -> list[dict[str, Any]]:
    """GET /iclock/api/terminals/ — follows the `next` pagination link
    until exhausted."""
    url: str | None = f"{_base(base_url)}/iclock/api/terminals/"
    headers = {"Authorization": f"JWT {token}", "Content-Type": "application/json"}
    terminals: list[dict[str, Any]] = []
    while url:
        try:
            resp = httpx.get(url, headers=headers, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            raise BioTimeError(f"Could not reach BioTime server: {exc}") from exc
        if resp.status_code != 200:
            raise BioTimeError(f"BioTime terminal list failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        terminals.extend(body.get("data") or [])
        url = body.get("next") or None
    return terminals


def list_transactions(
    base_url: str, token: str, start_time: datetime, end_time: datetime,
) -> list[dict[str, Any]]:
    """GET /iclock/api/transactions/ — pulls punch records in [start_time,
    end_time). Field names (emp_code, punch_time, terminal_sn/terminal_alias)
    follow BioTime's documented convention for the sibling Terminal/Personnel
    endpoints; the Transaction API's own doc page wasn't available when this
    was written, so field names here should be reconciled against a real
    server response the first time this runs against production data."""
    url: str | None = f"{_base(base_url)}/iclock/api/transactions/"
    headers = {"Authorization": f"JWT {token}", "Content-Type": "application/json"}
    params: dict[str, str] | None = {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "page_size": "200",
    }
    transactions: list[dict[str, Any]] = []
    while url:
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            raise BioTimeError(f"Could not reach BioTime server: {exc}") from exc
        if resp.status_code != 200:
            raise BioTimeError(f"BioTime transaction pull failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        transactions.extend(body.get("data") or [])
        url = body.get("next") or None
        params = None  # `next` already carries the query string
    return transactions


def get_valid_token(
    base_url: str, username: str, password: str,
    cached_token: str | None, cached_expires_at: datetime | None,
) -> tuple[str, datetime]:
    """Returns (token, expires_at), re-authenticating only if the cached
    token is missing or within 5 minutes of expiry. BioTime JWTs don't
    advertise their own TTL in the auth response, so a conservative fixed
    lifetime is assumed and refreshed proactively rather than reactively
    retried on 401 (keeps the sync path linear)."""
    now = datetime.now(UTC)
    if cached_token and cached_expires_at:
        expires_at = cached_expires_at if cached_expires_at.tzinfo else cached_expires_at.replace(tzinfo=UTC)
        if expires_at - now > timedelta(minutes=5):
            return cached_token, expires_at
    token = get_token(base_url, username, password)
    expires_at = now + timedelta(hours=6)
    return token, expires_at
