import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_requires_powabase_credentials(monkeypatch):
    for var in ("POWABASE_BASE_URL", "POWABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_loads_from_environment_with_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.powabase_base_url == "https://demo.p.powabase.ai"
    assert settings.powabase_agent_model == "gpt-4o-mini"
    assert settings.poll_interval_seconds == 2.0
    assert not hasattr(settings, "powabase_kb_id")


def test_gating_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
    from app.core.config import Settings
    s = Settings()
    assert s.router_agent_model == "gpt-4o-mini"
    assert s.retrieval_top_k == 4
    assert s.retrieval_max_context_tokens == 2000
    assert s.gate_history_turns == 2


def test_auth_settings(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
    from app.core.config import Settings
    s = Settings()
    assert s.auth_jwt_secret == "test-secret"
    assert s.auth_token_ttl_hours == 168
