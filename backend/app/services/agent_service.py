from __future__ import annotations

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError
from app.services.prompts import compose_system_prompt

# Changing any of these requires patching the remote agent, because they feed
# its model or its system prompt. Everything else on an agent row is local-only.
REMOTE_FIELDS = ("instructions", "grounding", "model")


class AgentService:
    """User-owned agents: durable configuration plus a permanent knowledge base.

    One Powabase agent per row — never per chat. Powabase keeps the conversation
    thread separate from the agent (``run_agent(agent_id, message, session_id)``),
    so a single agent serves many chats with independent histories.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def create(
        self,
        owner_id: str,
        name: str,
        instructions: str,
        model: str,
        grounding: str,
        use_general_kb: bool,
    ) -> dict:
        prompt = compose_system_prompt(instructions, grounding)
        agent = self.client.create_agent(
            f"user-agent-{name}", model=model, system_prompt=prompt
        )
        return self.client.insert_agent_row({
            "owner_id": owner_id,
            "name": name,
            "instructions": instructions,
            "model": model,
            "grounding": grounding,
            "use_general_kb": use_general_kb,
            "powabase_agent_id": agent["id"],
            "kb_id": None,
            "kb_full_id": None,
        })

    def list(self, owner_id: str) -> list:
        return self.client.list_agent_rows(owner_id)

    def get_owned(self, agent_id: str, owner_id: str):
        row = self.client.get_agent_row(agent_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row

    def update(self, row: dict, fields: dict) -> dict:
        """Apply an edit, patching the remote agent only when the fields feeding
        its model or system prompt actually changed.

        The remote patch is in place: recreating the agent would mint a new id
        and orphan every chat thread bound to the old one.
        """
        merged = dict(row, **fields)
        if any(field in fields for field in REMOTE_FIELDS):
            self.client.update_agent(row["powabase_agent_id"], {
                "model": merged["model"],
                "system_prompt": compose_system_prompt(
                    merged["instructions"], merged["grounding"]
                ),
            })
        self.client.update_agent_row(row["id"], fields)
        return merged

    def ensure_kb(self, row: dict, full_document: bool = False) -> str:
        """Return the agent's permanent KB id for this document class, creating
        it lazily. An agent nobody has trained costs no knowledge base."""
        column = "kb_full_id" if full_document else "kb_id"
        existing = row.get(column)
        if existing:
            return existing
        agent_id = row["id"]
        if full_document:
            name = f"agent-{agent_id}-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"agent-{agent_id}-kb"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Permanent knowledge for agent {agent_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
        self.client.update_agent_row(agent_id, {column: kb["id"]})
        return kb["id"]

    def delete(self, agent_id: str) -> bool:
        """Delete an agent and everything it owns: its chats, its permanent KBs
        and its Powabase agent.

        Remote cleanup is best-effort so a stale resource never blocks the
        authoritative row delete — the same rule SessionService.delete follows.
        """
        row = self.client.get_agent_row(agent_id)
        if row is None:
            return False

        for session in self.client.list_sessions_for_agent(agent_id):
            try:
                self.client.delete_session_row(session["id"])
            except PowabaseAPIError:
                pass

        for resource_id, delete_fn in (
            (row.get("kb_id"), self.client.delete_knowledge_base),
            (row.get("kb_full_id"), self.client.delete_knowledge_base),
            (row.get("powabase_agent_id"), self.client.delete_agent),
        ):
            if resource_id:
                try:
                    delete_fn(resource_id)
                except PowabaseAPIError:
                    pass

        self.client.delete_agent_row(agent_id)
        return True


def get_agent_service(request: Request) -> "AgentService":
    """FastAPI dependency returning the shared AgentService created at startup."""
    return request.app.state.agent_service
