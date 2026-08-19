from __future__ import annotations

from fastapi import Request


class ChatbotKbService:
    """A chatbot's knowledge base: trained once, searched by every agent in it.

    Deliberately distinct from an AGENT's permanent tier, which belongs to one
    agent and which the general assistant is blocked from so one agent's
    documents cannot surface in an answer attributed to another.

    This one belongs to the chatbot, so every agent inside reads it — the
    general assistant included. There is no per-agent opt-in: the chatbot is
    already the boundary, and a toggle would be a second place to look when an
    answer comes back thin.

    Two tiers for the same reason agents have two: a short document is indexed
    whole, a long one is chunked. Both are created lazily, so a chatbot that is
    never trained costs no knowledge base.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def kb_ids(self, chatbot_row) -> list:
        """This chatbot's knowledge bases, in retrieval order. Empty if untrained."""
        if not chatbot_row:
            return []
        return [kb for kb in (chatbot_row.get("kb_id"),
                              chatbot_row.get("kb_full_id")) if kb]

    def ensure_kb(self, chatbot_row: dict, full_document: bool = False) -> str:
        """Return the tier that holds this document class, creating it lazily."""
        column = "kb_full_id" if full_document else "kb_id"
        existing = chatbot_row.get(column)
        if existing:
            return existing
        chatbot_id = chatbot_row["id"]
        if full_document:
            name = f"chatbot-{chatbot_id}-knowledge-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"chatbot-{chatbot_id}-knowledge"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Knowledge for chatbot {chatbot_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
        self.client.update_chatbot_row(chatbot_id, {column: kb["id"]})
        return kb["id"]

    def documents(self, chatbot_row: dict) -> list:
        """Every document across both tiers, in the order Powabase returns them."""
        out = []
        for kb_id in self.kb_ids(chatbot_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                out.append({
                    "source_id": item.get("source_id"),
                    # Powabase names these source_name / index_status.
                    "filename": item.get("source_name") or item.get("source_id"),
                    "status": item.get("index_status"),
                })
        return out

    def contains(self, kb_id: str, source_id: str) -> bool:
        """Whether this knowledge base already holds that document.

        Promotion needs it: re-indexing a source a knowledge base already has
        is at best wasted work and at worst an upstream error, and promoting
        the same file twice is a thing users do — upload_source deduplicates
        identical content, so the second upload yields the same source id.
        """
        items = self.client.list_kb_sources(kb_id).get("items", [])
        return any(item.get("source_id") == source_id for item in items)

    def untrain(self, chatbot_row: dict, source_id: str) -> bool:
        """Unlink one document from whichever tier holds it.

        Never deletes the Source itself: upload_source deduplicates identical
        content, so the same source may belong to an agent, another chatbot, or
        another user. Promotion from a chat makes multi-KB sources routine, so
        this matters more than it used to, not less.
        """
        for kb_id in self.kb_ids(chatbot_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                if item.get("source_id") == source_id:
                    self.client.remove_source_from_kb(kb_id, item["id"])
                    return True
        return False


def get_chatbot_kb_service(request: Request) -> "ChatbotKbService":
    """FastAPI dependency returning the shared ChatbotKbService."""
    return request.app.state.chatbot_kb_service
