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
    assert s.orchestrator_model == "gpt-4o-mini"
    assert s.retrieval_top_k == 8
    assert s.retrieval_max_context_tokens == 32000
    assert s.full_document_max_chars == 120000
    assert s.history_turns == 2
    assert s.ingest_background_max_wait_seconds == 600
    assert s.reranker_model == "cohere/rerank-english-v3.0"
    assert s.reranker_candidate_count == 20


def test_auth_settings(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
    from app.core.config import Settings
    s = Settings()
    assert s.auth_jwt_secret == "test-secret"
    assert s.auth_token_ttl_hours == 168
