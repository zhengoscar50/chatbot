from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError

DEFAULT_NAME = "New session"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    """A chat: a conversation thread bound to a user-owned agent.

    Creates no agent of its own — the agent is durable and user-configured, and
    one agent serves many chats.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def create_session(self, owner_id: str, agent_id: str, name: str | None = None) -> dict:
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "agent_id": agent_id,
            "name": name or DEFAULT_NAME,
        })

    def ensure_kb(self, row: dict) -> str:
        """Return this chat's scratch KB id, creating it lazily on first upload.

        Chunk-embed only: scratch uploads are throwaway context for a single
        conversation, so the chunk/full split is reserved for an agent's
        permanent tier.
        """
        existing = row.get("kb_id")
        if existing:
            return existing
        session_id = row["id"]
        kb = self.client.create_knowledge_base(
            f"chat-{session_id}-kb",
            description=f"Scratch documents for chat {session_id}",
            retrieval_config=self.reranker_config,
        )
        self.client.update_session(session_id, {"kb_id": kb["id"]})
        return kb["id"]

    def list(self, owner_id: str) -> list:
        rows = self.client.list_sessions(owner_id)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
            for r in rows
        ]

    def get(self, session_id: str):
        return self.client.get_session_row(session_id)

    def get_owned_session(self, session_id: str, owner_id: str):
        row = self.client.get_session_row(session_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row

    def touch(self, session_id: str, **fields) -> None:
        fields["updated_at"] = _now_iso()
        self.client.update_session(session_id, fields)

    def rename(self, session_id: str, name: str) -> None:
        # Rename only — no updated_at bump, so renaming doesn't reorder the list.
        self.client.update_session(session_id, {"name": name})

    def delete(self, session_id: str) -> bool:
        """Delete a chat: its scratch KB (best-effort) and its row.

        Returns False if the chat doesn't exist. The row deletion is
        authoritative and may raise PowabaseAPIError; the KB cleanup is
        best-effort so a stale/missing resource never blocks the delete.

        Deliberately does NOT touch ``agent_id``. It used to hold a Powabase
        agent this session owned; it is now a foreign key to a user-owned agent
        that outlives the chat and is shared with the user's other chats.
        """
        row = self.client.get_session_row(session_id)
        if row is None:
            return False
        kb_id = row.get("kb_id")
        if kb_id:
            try:
                self.client.delete_knowledge_base(kb_id)
            except PowabaseAPIError:
                pass
        self.client.delete_session_row(session_id)
        return True


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency returning the shared SessionService created at startup."""
    return request.app.state.session_service
