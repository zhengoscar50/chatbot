import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_requires_powabase_credentials(monkeypatch):
    for var in (
        "POWABASE_BASE_URL",
        "POWABASE_SERVICE_ROLE_KEY",
        "POWABASE_KB_ID",
        "POWABASE_AGENT_ID",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_loads_from_environment_with_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")

    settings = Settings(_env_file=None)

    assert settings.powabase_base_url == "https://demo.p.powabase.ai"
    assert settings.powabase_agent_model == "gpt-4o-mini"
    assert settings.poll_interval_seconds == 2.0
