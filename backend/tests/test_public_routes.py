"""Public page routes answer with a page or a redirect -- never a server error. The old
marketing pages (/landing.html, /contact.html, /signup.html) were removed but their URLs
stayed in old links; they answered 500 until 2026-10-10."""
import pytest


@pytest.mark.parametrize("path,target", [("/landing.html", "/"), ("/contact.html", "/"), ("/signup.html", "/signup")])
def test_old_site_pages_redirect(client, path, target):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == target


@pytest.mark.parametrize("path", ["/", "/login", "/signup", "/hrms", "/ess", "/superadmin", "/pos", "/handbook"])
def test_public_pages_load(client, path):
    assert client.get(path).status_code == 200
