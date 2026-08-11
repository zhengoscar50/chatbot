# backend/tests/unit/test_routes_health.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings


def set_env(monkeypatch, **overrides):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def build_app():
    from app.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_reports_the_models_that_drive_behaviour(monkeypatch):
    set_env(
        monkeypatch,
        ORCHESTRATOR_MODEL="claude-sonnet-5",
        DEFAULT_AGENT_MODEL="gpt-5-mini",
        GENERAL_ASSISTANT_MODEL="gemini-2.5-flash",
    )

    response = TestClient(build_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "models": {
            "orchestrator": "claude-sonnet-5",
            "default_agent": "gpt-5-mini",
            "general_assistant": "gemini-2.5-flash",
        },
    }


def test_health_does_not_report_powabase_agent_model(monkeypatch):
    """POWABASE_AGENT_MODEL drives nothing but the optional bootstrap script.

    Reporting it as *the* model is how a deployment came to advertise
    claude-sonnet-5 while every decision path ran gpt-4o-mini — a discrepancy
    that survived because /health looked authoritative and wasn't.
    """
    set_env(monkeypatch, POWABASE_AGENT_MODEL="claude-opus-5")

    body = TestClient(build_app()).get("/health").json()

    assert "model" not in body
    assert "claude-opus-5" not in str(body)


def test_health_models_default_when_unset(monkeypatch):
    set_env(monkeypatch)

    body = TestClient(build_app()).get("/health").json()

    assert body["models"] == {
        "orchestrator": "gpt-4o-mini",
        "default_agent": "gpt-4o-mini",
        "general_assistant": "gpt-4o-mini",
    }
