"""GET /companies/current used to embed the full base64 logo directly in its
JSON body (~780KB), fetched fresh on every single page load (index.html/
hrms.html are separate documents, not an SPA) and re-invalidated on ANY
company field edit since its ETag was keyed on company.updated_at. The same
raw logo also rode along in GET /app-data?action=bootstrap's company blob.
Both now expose has_logo/logo_version only; GET /companies/{id}/logo serves
the actual bytes, independently and immutably cacheable."""
import base64

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TINY_PNG_DATA_URL = f"data:image/png;base64,{TINY_PNG_B64}"


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def test_current_company_no_longer_embeds_raw_logo(client, auth_headers):
    r = client.put("/api/v1/companies/current", headers=auth_headers, json={"logo": TINY_PNG_DATA_URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "logo" not in body
    assert body["has_logo"] is True
    assert body["logo_version"]

    r2 = client.get("/api/v1/companies/current", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert "logo" not in body2
    assert body2["has_logo"] is True
    assert body2["logo_version"] == body["logo_version"]


def test_logo_endpoint_serves_decoded_image_and_is_public(client, auth_headers):
    company_id = _company_id(client, auth_headers)
    r = client.put("/api/v1/companies/current", headers=auth_headers, json={"logo": TINY_PNG_DATA_URL})
    assert r.status_code == 200, r.text

    # No Authorization header at all -- must still work (an <img src> can't send one).
    r2 = client.get(f"/api/v1/companies/{company_id}/logo")
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"] == "image/png"
    assert r2.content == base64.b64decode(TINY_PNG_B64)
    assert "immutable" in r2.headers["cache-control"]
    etag = r2.headers["etag"]
    assert etag

    r3 = client.get(f"/api/v1/companies/{company_id}/logo", headers={"if-none-match": etag})
    assert r3.status_code == 304, r3.text


def test_logo_endpoint_404_when_no_logo(client, auth_headers):
    company_id = _company_id(client, auth_headers)
    r = client.put("/api/v1/companies/current", headers=auth_headers, json={"logo": None})
    assert r.status_code == 200, r.text
    assert r.json()["has_logo"] is False

    r2 = client.get(f"/api/v1/companies/{company_id}/logo")
    assert r2.status_code == 404, r2.text


def test_bootstrap_company_blob_no_longer_embeds_raw_logo(client, auth_headers):
    r = client.put("/api/v1/companies/current", headers=auth_headers, json={"logo": TINY_PNG_DATA_URL})
    assert r.status_code == 200, r.text

    r2 = client.get("/api/v1/app-data", params={"action": "bootstrap"}, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    company = r2.json()["data"]["company"]
    assert "logo" not in company
    assert company["has_logo"] is True
    assert company["logo_version"]
