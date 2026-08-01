from __future__ import annotations

from fastapi import Request

GENERAL_KB_NAME = "general-knowledge-kb"


def _find_by_name(items: list, name: str):
    return next((item for item in items if item.get("name") == name), None)


def ensure_general_kb(client, reranker_config: dict | None = None) -> str:
    """Find-or-create the shared general-knowledge KB; return its id."""
    existing = client.list_knowledge_bases().get("knowledge_bases", [])
    kb = _find_by_name(existing, GENERAL_KB_NAME)
    if kb is None:
        kb = client.create_knowledge_base(
            GENERAL_KB_NAME,
            description="Shared admin-curated general knowledge",
            retrieval_config=reranker_config,
        )
    return kb["id"]


def get_general_kb_id(request: Request) -> str:
    """FastAPI dependency returning the general KB id resolved at startup."""
    return request.app.state.general_kb_id
