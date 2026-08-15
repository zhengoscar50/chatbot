from __future__ import annotations

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError


class UserKbService:
    """A user's personal knowledge base: trained once, searched by every agent
    that user owns.

    Deliberately separate from the two knowledge bases already in play:

    - an AGENT's permanent tier belongs to one agent, and the general assistant
      is blocked from it so one agent's documents cannot surface in an answer
      attributed to another;
    - the ADMIN general KB is curated centrally, shared across all users, and
      opt-in per agent.

    This one is the user's own, so every one of their agents sees it — the
    general assistant included. Excluding it there would be surprising: it is
    their knowledge, not any single agent's.

    Two tiers for the same reason agents have two: a short document is indexed
    whole, a long one is chunked. Both are created lazily, so a user who never
    trains costs no knowledge base.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def kb_ids(self, user_row) -> list:
        """This user's knowledge bases, in retrieval order. Empty if untrained."""
        if not user_row:
            return []
        return [kb for kb in (user_row.get("kb_id"), user_row.get("kb_full_id")) if kb]

    def ensure_kb(self, user_row: dict, full_document: bool = False) -> str:
        """Return the tier that holds this document class, creating it lazily."""
        column = "kb_full_id" if full_document else "kb_id"
        existing = user_row.get(column)
        if existing:
            return existing
        user_id = user_row["id"]
        if full_document:
            name = f"user-{user_id}-knowledge-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"user-{user_id}-knowledge"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Personal knowledge for user {user_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
        self.client.update_user(user_id, {column: kb["id"]})
        return kb["id"]

    def documents(self, user_row: dict) -> list:
        """Every document across both tiers, newest first where available."""
        out = []
        for kb_id in self.kb_ids(user_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                out.append({
                    "source_id": item.get("source_id"),
                    # Powabase names these source_name / index_status.
                    "filename": item.get("source_name") or item.get("source_id"),
                    "status": item.get("index_status"),
                })
        return out

    def untrain(self, user_row: dict, source_id: str) -> bool:
        """Unlink one document from whichever tier holds it.

        Never deletes the Source itself: upload_source deduplicates identical
        content, so the same source may belong to an agent or another user.
        """
        for kb_id in self.kb_ids(user_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                if item.get("source_id") == source_id:
                    self.client.remove_source_from_kb(kb_id, item["id"])
                    return True
        return False


def get_user_kb_service(request: Request) -> "UserKbService":
    """FastAPI dependency returning the shared UserKbService."""
    return request.app.state.user_kb_service
