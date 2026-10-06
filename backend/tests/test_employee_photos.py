"""Employee photos leave the bootstrap/list payloads as cacheable image URLs."""
import base64
from uuid import uuid4

import pytest

from tests.conftest import ensure_user

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake image bytes").decode()


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"ph-{tag}@taxflowqa.com"
    ensure_user(db, email, f"93{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _save(client, headers, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "employees", "record": record})
    assert r.status_code == 200, r.text


def _listed(client, headers, emp_id):
    data = client.get("/api/v1/app-data", headers=headers).json()["data"]
    return next(e for e in data["employees"] if e["id"] == emp_id)


def test_bootstrap_and_list_send_a_photo_url_that_serves_the_image(client, tenant):
    _save(client, tenant, {"id": "PH-1", "name": "Photo Person", "photo": PNG})
    photo = _listed(client, tenant, "PH-1")["photo"]
    assert photo.startswith("/api/v1/app-data/employee-photo/") and "?v=" in photo
    listed = client.get("/api/v1/app-data/records/employees", headers=tenant).json()["records"]
    assert next(e for e in listed if e["id"] == "PH-1")["photo"] == photo

    r = client.get(photo)  # no login header, as an <img> would request it
    assert r.status_code == 200
    assert r.content == b"\x89PNG fake image bytes" and r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert client.get(photo, headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_photo_url_needs_the_right_version(client, tenant):
    _save(client, tenant, {"id": "PH-2", "name": "Guess Me", "photo": PNG})
    path = _listed(client, tenant, "PH-2")["photo"].split("?")[0]
    assert client.get(path).status_code == 404
    assert client.get(path, params={"v": "0000000000000000"}).status_code == 404


def test_saving_the_url_back_keeps_the_stored_photo(client, tenant):
    _save(client, tenant, {"id": "PH-3", "name": "Edit Me", "photo": PNG})
    url = _listed(client, tenant, "PH-3")["photo"]
    # The edit form sends back whatever its preview <img> shows: the URL.
    _save(client, tenant, {"id": "PH-3", "name": "Edited", "photo": url})
    after = _listed(client, tenant, "PH-3")
    assert after["name"] == "Edited" and after["photo"] == url
    assert client.get(url).status_code == 200

    # A new photo gets a new URL.
    new_png = "data:image/png;base64," + base64.b64encode(b"another image").decode()
    _save(client, tenant, {"id": "PH-3", "name": "Edited", "photo": new_png})
    assert _listed(client, tenant, "PH-3")["photo"] != url
