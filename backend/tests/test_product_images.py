"""Product images (Item Master uploads) leave the bootstrap/list payloads as cacheable
image URLs, like employee photos, so the products cap could go from 500 to 2000."""
import base64
import json
from uuid import uuid4

import pytest

from app.models import AppDataRecord
from tests.conftest import ensure_user

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG product bytes").decode()


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"pi-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"94{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _save(client, headers, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "products", "record": record})
    assert r.status_code == 200, r.text


def _products(client, headers):
    body = client.get("/api/v1/app-data", headers=headers).json()
    return {p["code"]: p for p in body["data"]["products"]}, body


def test_bootstrap_sends_an_image_url_that_serves_the_image(client, tenant):
    _, headers = tenant
    _save(client, headers, {"code": "IMG-1", "name": "Pictured", "image": PNG})
    image = _products(client, headers)[0]["IMG-1"]["image"]
    assert image.startswith("/api/v1/app-data/product-image/") and "?v=" in image
    r = client.get(image)  # no login header, as an <img> would request it
    assert r.status_code == 200 and r.content == b"\x89PNG product bytes"
    assert client.get(image.split("?")[0]).status_code == 404  # needs the right version
    catalog = client.get("/api/v1/app-data/catalog/products", headers=headers, params={"q": "pictured"}).json()
    assert catalog["records"][0]["image"] == image


def test_saving_the_url_back_keeps_the_stored_image(client, tenant):
    _, headers = tenant
    _save(client, headers, {"code": "IMG-2", "name": "Edit Me", "image": PNG})
    url = _products(client, headers)[0]["IMG-2"]["image"]
    _save(client, headers, {"code": "IMG-2", "name": "Edited", "image": url})  # Item Master edit form
    after = _products(client, headers)[0]["IMG-2"]
    assert after["name"] == "Edited" and after["image"] == url and client.get(url).status_code == 200


def test_products_cap_is_now_2000(client, db, tenant):
    company_id, headers = tenant
    db.add_all([AppDataRecord(company_id=company_id, collection="products", record_key=f"CAP-{i:04d}",
                              payload=json.dumps({"code": f"CAP-{i:04d}", "name": f"Item {i}"})) for i in range(1200)])
    db.commit()
    products, body = _products(client, headers)
    assert len(products) == 1200
    assert "products" not in body.get("truncated_collections", [])
