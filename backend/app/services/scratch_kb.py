from __future__ import annotations

from fastapi import Request

SCRATCH_KB_NAME = "chat-scratch-kb"


def _find_by_name(items: list, name: str):
    return next((item for item in items if item.get("name") == name), None)


def ensure_scratch_kb(client, reranker_config: dict | None = None) -> str:
    """Find-or-create the ONE knowledge base holding every chat's uploads.

    Chats used to get a Knowledge Base each. They share this one instead, and
    stay isolated because retrieval names the chat's own `source_ids` — see
    retrieval_scope, which never emits this KB unscoped.

    Chunk-embed only: scratch uploads are throwaway context for a single
    conversation, so the chunk/full split stays reserved for an agent's
    permanent tier.
    """
    existing = client.list_knowledge_bases().get("knowledge_bases", [])
    kb = _find_by_name(existing, SCRATCH_KB_NAME)
    if kb is None:
        kb = client.create_knowledge_base(
            SCRATCH_KB_NAME,
            description="Per-chat scratch uploads, scoped by source_ids",
            retrieval_config=reranker_config,
        )
    return kb["id"]


def get_scratch_kb_id(request: Request) -> str:
    """FastAPI dependency returning the scratch KB id resolved at startup."""
    return request.app.state.scratch_kb_id
