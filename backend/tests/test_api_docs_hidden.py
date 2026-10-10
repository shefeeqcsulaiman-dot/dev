"""The interactive API docs (/docs, /redoc, /openapi.json) map every endpoint, so they're
off in production and on everywhere else."""
from app import main


def test_api_docs_are_off_in_production(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    app = main.create_app()
    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None


def test_api_docs_can_be_turned_on_in_production(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    assert main.create_app().openapi_url == "/openapi.json"


def test_api_docs_stay_on_outside_production(client):
    assert client.get("/openapi.json").status_code == 200
