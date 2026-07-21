# backend/tests/unit/test_routes_health.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()


def build_app():
    from app.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_reports_status_and_model(monkeypatch):
    set_env(monkeypatch)

    response = TestClient(build_app()).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "model": "gpt-4o-mini"}
