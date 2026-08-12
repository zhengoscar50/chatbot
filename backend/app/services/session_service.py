from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError

DEFAULT_NAME = "New chat"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    """A chat: a conversation thread owned by a user.

    Creates no agent of its own. Chats are not bound to an agent at all — the
    orchestrator picks one per message from the user's whole roster.
    """

    def __init__(self, client, reranker_config: dict | None = None,
                 scratch_kb_id: str | None = None):
        self.client = client
        self.reranker_config = reranker_config
        # The one KB holding every chat's uploads. None in tests that never
        # touch scratch documents.
        self.scratch_kb_id = scratch_kb_id

    def create_session(self, owner_id: str, name: str | None = None) -> dict:
        """Create a chat. Chats belong to the user, not to an agent — the
        orchestrator picks an agent per message."""
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "name": name or DEFAULT_NAME,
        })

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

        Chats own nothing but their scratch KB: agents are separate, durable
        and shared across every chat.
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
        self._unlink_scratch_sources(row)
        self.client.delete_session_row(session_id)
        return True

    def record_source(self, session_id: str, source_id: str) -> None:
        """Append an indexed upload to this chat's scratch scope.

        Re-reads the row rather than trusting a copy: uploads finish in a
        background task, so two concurrent uploads would otherwise each write
        back the list as it looked before the other started, losing one.
        """
        row = self.client.get_session_row(session_id)
        if row is None:
            return
        source_ids = list(row.get("source_ids") or [])
        if source_id in source_ids:
            return
        source_ids.append(source_id)
        self.client.update_session(session_id, {"source_ids": source_ids})

    def _unlink_scratch_sources(self, row: dict) -> None:
        """Remove this chat's uploads from the SHARED scratch KB.

        Deleting the chat used to delete its KB outright. The shared KB holds
        every other chat's uploads too, so unlink only this chat's sources —
        and never delete the Source itself, since upload_source reuses
        duplicates and another chat may point at the same one.

        Best-effort: a missing source must not block deleting the chat.
        """
        source_ids = row.get("source_ids") or []
        if not source_ids or not self.scratch_kb_id:
            return
        try:
            items = self.client.list_kb_sources(self.scratch_kb_id).get("items", [])
        except PowabaseAPIError:
            return
        # The unlink takes the indexed-source id (item["id"]), not source_id.
        for item in items:
            if item.get("source_id") in source_ids:
                try:
                    self.client.remove_source_from_kb(self.scratch_kb_id, item["id"])
                except PowabaseAPIError:
                    pass


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency returning the shared SessionService created at startup."""
    return request.app.state.session_service
