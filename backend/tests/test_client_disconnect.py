"""A browser that goes away while its request body is still arriving (tab closed, phone lost
signal, the page's own timeout) raises ClientDisconnect: that's 499, not a server error, and
isn't listed in Super Admin > Errors."""
from starlette.requests import ClientDisconnect
from starlette.testclient import TestClient

from app import server_errors
from app.main import app


def test_client_disconnect_is_499_and_not_logged():
    path = "/api/v1/_test-client-disconnect"
    if not any(getattr(r, "path", None) == path for r in app.routes):
        def _gone():
            raise ClientDisconnect()
        app.add_api_route(path, _gone, methods=["POST"])
        app.router.routes.insert(0, app.router.routes.pop())   # ahead of the catch-all page route

    before = len(server_errors.recent(1000)[0])
    r = TestClient(app, raise_server_exceptions=False).post(path)
    assert r.status_code == 499
    assert len(server_errors.recent(1000)[0]) == before
