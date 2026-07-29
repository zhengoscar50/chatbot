from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError

SYSTEM_PROMPT = (
    "You are a helpful assistant. When relevant context from the knowledge "
    "base is provided with a question, base your answer on that context and "
    "cite your sources. If the provided context does not contain the answer, "
    "say so plainly rather than guessing. When no context is provided, answer "
    "normally and helpfully."
)
DEFAULT_NAME = "New session"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    def __init__(self, client, model: str, general_kb_id: str | None = None):
        self.client = client
        self.model = model
        self.general_kb_id = general_kb_id

    def create_session(self, owner_id: str, username: str, name: str | None = None) -> dict:
        user_slug = slugify(username)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")

        session_id = str(uuid.uuid4())
        kb = self.client.create_knowledge_base(
            f"session-{session_id}-kb", description=f"Documents for session {session_id}"
        )
        agent = self.client.create_agent(
            f"session-{session_id}-agent", model=self.model, system_prompt=SYSTEM_PROMPT
        )

        row = {
            "id": session_id,
            "owner_id": owner_id,
            "user_slug": user_slug,
            "name": name or DEFAULT_NAME,
            "kb_id": kb["id"],
            "agent_id": agent["id"],
        }
        return self.client.insert_session(row)

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
        """Delete a session: its Powabase KB + agent (best-effort) and its row.

        Returns False if the session doesn't exist. The row deletion is
        authoritative and may raise PowabaseAPIError; the KB/agent cleanup is
        best-effort so a stale/missing resource never blocks the delete.
        """
        row = self.client.get_session_row(session_id)
        if row is None:
            return False
        for resource_id, delete_fn in (
            (row.get("kb_id"), self.client.delete_knowledge_base),
            (row.get("agent_id"), self.client.delete_agent),
        ):
            if resource_id:
                try:
                    delete_fn(resource_id)
                except PowabaseAPIError:
                    pass
        self.client.delete_session_row(session_id)
        return True


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency returning the shared SessionService created at startup."""
    return request.app.state.session_service
