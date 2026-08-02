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
    router_agent_model: str = "gpt-4o-mini"
    retrieval_top_k: int = 8
    retrieval_max_context_tokens: int = 32000
    gate_history_turns: int = 2
    reranker_model: str = "cohere/rerank-english-v3.0"
    reranker_candidate_count: int = 20
    full_document_max_chars: int = 120000
    research_top_k: int = 12
    research_max_context_tokens: int = 24000
    research_researcher_model: str = "gpt-4o-mini"
    research_analyst_model: str = "claude-sonnet-5"
    research_writer_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
