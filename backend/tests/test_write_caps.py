"""Server guards against request floods: a bulk-save / bulk-delete carries at most 1,000
records, and one login may have at most 8 writes in progress at once per server copy
(the 9th gets 429 + Retry-After, other logins and reads are unaffected)."""
import asyncio
import uuid

from app.http_middleware import AppMiddleware, _writes_in_flight
from app.security import create_access_token


def _rows(n, tag):
    return [{"code": f"U{tag}{i}", "name": f"Unit {tag} {i}", "type": "Quantity", "decimals": "2",
             "status": "Active"} for i in range(n)]


def test_bulk_save_and_delete_cap_at_1000_records(client, auth_headers):
    tag = uuid.uuid4().hex[:5]
    too_many = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"},
                           json={"collection": "salesUnits", "records": _rows(1001, tag)})
    assert too_many.status_code == 413
    assert "1,000" in too_many.json()["detail"]

    ok = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"},
                     json={"collection": "salesUnits", "records": _rows(1000, tag)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["saved"] == 1000

    keys = [{"code": r["code"]} for r in _rows(1001, tag)]
    assert client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-delete"},
                       json={"collection": "salesUnits", "records": keys}).status_code == 413
    gone = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-delete"},
                       json={"collection": "salesUnits", "records": keys[:1000]})
    assert gone.status_code == 200 and gone.json()["deleted"] == 1000


def _call(mw, method, token):
    """Runs one request through the middleware; returns (status, headers)."""
    out = {}
    scope = {"type": "http", "method": method, "path": "/api/v1/app-data", "query_string": b"",
             "headers": [(b"authorization", f"Bearer {token}".encode())]}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = dict(message.get("headers") or [])

    async def run():
        await mw(scope, receive, send)
        return out["status"], out["headers"]
    return run()


def test_ninth_write_from_one_login_waits_others_do_not():
    release = asyncio.Event()

    async def slow_app(scope, receive, send):
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def on_write(_auth):
        return None

    mw = AppMiddleware(slow_app, csp="", csp_mode="off", on_write=on_write)
    alice = create_access_token(f"cap-a-{uuid.uuid4().hex[:6]}")
    bob = create_access_token(f"cap-b-{uuid.uuid4().hex[:6]}")

    async def scenario():
        held = [asyncio.create_task(_call(mw, "POST", alice)) for _ in range(8)]
        await asyncio.sleep(0.05)
        ninth_status, ninth_headers = await _call(mw, "POST", alice)   # answered at once
        bob_write = asyncio.create_task(_call(mw, "POST", bob))
        alice_read = asyncio.create_task(_call(mw, "GET", alice))
        await asyncio.sleep(0.05)
        assert not bob_write.done() and not alice_read.done()            # both let through
        release.set()
        results = await asyncio.gather(*held, bob_write, alice_read)
        return ninth_status, ninth_headers, [s for s, _ in results]

    ninth_status, ninth_headers, others = asyncio.run(scenario())
    assert ninth_status == 429
    assert ninth_headers.get(b"retry-after") == b"1"
    assert others == [200] * 10
    assert not _writes_in_flight   # every slot given back

    async def after():
        return await _call(mw, "POST", alice)
    assert asyncio.run(after())[0] == 200   # free again once the others finished
