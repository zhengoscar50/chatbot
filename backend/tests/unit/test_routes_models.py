from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import models as models_route
from app.core.config import Settings, get_settings


def build_app(**settings_kwargs):
    app = FastAPI()
    app.include_router(models_route.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(**settings_kwargs)
    return app


def test_returns_the_configured_choices_and_default():
    app = build_app(agent_model_choices=["a", "b"], default_agent_model="a")

    body = TestClient(app).get("/models").json()

    assert body["models"] == ["a", "b"]
    assert body["default"] == "a"
    # The form bounds its slider from here rather than hardcoding limits in JS.
    assert body["context_limits"]["a"]["max"] > 0
    assert body["context_min"] > 0


def test_default_is_prepended_when_missing_from_the_list():
    # Otherwise trimming the list could leave the default unselectable in the UI.
    app = build_app(agent_model_choices=["b", "c"], default_agent_model="a")

    body = TestClient(app).get("/models").json()

    assert body["models"] == ["a", "b", "c"]


def test_default_is_not_duplicated_when_already_listed():
    app = build_app(agent_model_choices=["a", "b"], default_agent_model="a")
    assert TestClient(app).get("/models").json()["models"].count("a") == 1


def test_requires_authentication():
    app = FastAPI()
    app.include_router(models_route.router)
    # get_current_user resolves the shared client before rejecting, so state
    # has to exist for the 401 path to be reached at all.
    app.state.powabase_client = object()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        agent_model_choices=["a"], default_agent_model="a"
    )
    assert TestClient(app).get("/models").status_code == 401


def test_settings_parses_the_comma_separated_list(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "key")
    monkeypatch.setenv("AGENT_MODELS", " gpt-4o-mini , claude-sonnet-5 ,, ")

    assert Settings().agent_model_choices == ["gpt-4o-mini", "claude-sonnet-5"]


def test_default_settings_ship_a_non_empty_verified_list(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "key")
    monkeypatch.delenv("AGENT_MODELS", raising=False)

    choices = Settings().agent_model_choices

    assert len(choices) >= 5
    assert "gpt-4o-mini" in choices and "claude-sonnet-5" in choices
