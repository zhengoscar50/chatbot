# backend/tests/unit/test_routes_health.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    # Explicit, not relying on the class default: a real backend/.env (used
    # for live verification against a real Powabase project) would otherwise
    # supply this value via pydantic-settings' env_file fallback and silently
    # override the default this test asserts on.
    monkeypatch.setenv("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()


def build_app():
    from app.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_returns_configured_ids(monkeypatch):
    set_env(monkeypatch)

    client = TestClient(build_app())
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kb_id"] == "kb-123"
    assert body["agent_id"] == "agent-456"
    assert body["model"] == "gpt-4o-mini"
