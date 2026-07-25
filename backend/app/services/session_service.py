from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import Request

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)
DEFAULT_NAME = "New session"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def create_session(self, user: str, name: str | None = None) -> dict:
        user_slug = slugify(user)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")

        session_id = str(uuid.uuid4())
        kb = self.client.create_knowledge_base(
            f"session-{session_id}-kb", description=f"Documents for session {session_id}"
        )
        agent = self.client.create_agent(
            f"session-{session_id}-agent", model=self.model, system_prompt=SYSTEM_PROMPT
        )
        self.client.link_kb_to_agent(agent["id"], kb["id"])

        row = {
            "id": session_id,
            "user_slug": user_slug,
            "name": name or DEFAULT_NAME,
            "kb_id": kb["id"],
            "agent_id": agent["id"],
        }
        return self.client.insert_session(row)

    def list(self, user: str) -> list:
        user_slug = slugify(user)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")
        rows = self.client.list_sessions(user_slug)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
            for r in rows
        ]

    def get(self, session_id: str):
        return self.client.get_session_row(session_id)

    def touch(self, session_id: str, **fields) -> None:
        fields["updated_at"] = _now_iso()
        self.client.update_session(session_id, fields)

    def rename(self, session_id: str, name: str) -> None:
        # Rename only — no updated_at bump, so renaming doesn't reorder the list.
        self.client.update_session(session_id, {"name": name})


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency returning the shared SessionService created at startup."""
    return request.app.state.session_service
