from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    powabase_base_url: str
    powabase_service_role_key: str
    powabase_agent_model: str = "gpt-4o-mini"
    admin_password: Optional[str] = None
    auth_jwt_secret: str
    auth_token_ttl_hours: int = 168

    poll_interval_seconds: float = 2.0
    ingest_max_wait_seconds: float = 60.0
    ingest_background_max_wait_seconds: int = 600
    orchestrator_model: str = "gpt-4o-mini"
    default_agent_model: str = "gpt-4o-mini"
    general_assistant_model: str = "gpt-4o-mini"
    # Offered in the agent form's model picker. Powabase exposes no model
    # catalog (/api/models 404s), so this list is hand-maintained and can drift
    # from what the provider actually serves — which is why creating an agent
    # still probes the chosen model, and why the UI keeps an "Other…" escape
    # hatch. Every id here answered a live ping on 2026-08-07.
    agent_models: str = (
        "gpt-4o-mini,gpt-4o,gpt-5-nano,gpt-5-mini,gpt-5,"
        "claude-haiku-4-5,claude-sonnet-4-5,claude-sonnet-5,claude-opus-5,"
        "gemini-2.5-flash"
    )

    @property
    def agent_model_choices(self) -> list:
        return [m.strip() for m in self.agent_models.split(",") if m.strip()]
    retrieval_top_k: int = 8
    retrieval_max_context_tokens: int = 32000
    history_turns: int = 2
    reranker_model: str = "cohere/rerank-english-v3.0"
    reranker_candidate_count: int = 20
    full_document_max_chars: int = 120000


@lru_cache
def get_settings() -> Settings:
    return Settings()
