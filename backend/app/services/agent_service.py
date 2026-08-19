from __future__ import annotations

import logging
import uuid

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError
from app.services.context_budget import clamp_context_tokens
from app.services.prompts import compose_system_prompt

# Changing any of these requires patching the remote agent, because they feed
# its model or its system prompt. Everything else on an agent row is local-only.
REMOTE_FIELDS = ("instructions", "grounding", "model")

logger = logging.getLogger(__name__)


class ModelRejectedError(Exception):
    """The provider won't serve this model id."""

    def __init__(self, model: str, detail: str):
        self.model = model
        self.detail = detail
        super().__init__(f"Model {model!r} was rejected: {detail}")


class AgentService:
    """User-owned agents: durable configuration plus a permanent knowledge base.

    One Powabase agent per row — never per chat. Powabase keeps the conversation
    thread separate from the agent (``run_agent(agent_id, message, session_id)``),
    so a single agent serves many chats with independent histories.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def probe_model(self, model: str) -> None:
        """Raise ModelRejectedError unless the provider will actually serve this
        model.

        Powabase accepts any string as an agent's model and only fails at run
        time (verified live: a nonsense id creates fine and 502s on the first
        message), so a typo would otherwise ship as a silently broken agent.

        Probing on a throwaway agent rather than the real one means a rejected
        model never leaves a half-made agent behind, and the same code path
        works for editing an existing agent's model without needing a rollback.
        Costs one cheap model call.
        """
        probe = None
        try:
            probe = self.client.create_agent(
                f"model-probe-{uuid.uuid4()}", model=model,
                system_prompt="Reply with OK.",
            )
            self.client.run_agent_sync(probe["id"], "ping")
        except PowabaseAPIError as e:
            raise ModelRejectedError(model, str(e))
        finally:
            if probe:
                try:
                    self.client.delete_agent(probe["id"])
                except PowabaseAPIError:
                    pass

    def create(
        self,
        chatbot_id: str,
        owner_id: str,
        name: str,
        instructions: str,
        description: str,
        model: str,
        grounding: str,
        max_context_tokens=None,
    ) -> dict:
        self.probe_model(model)
        prompt = compose_system_prompt(instructions, grounding)
        agent = self.client.create_agent(
            f"user-agent-{name}", model=model, system_prompt=prompt
        )
        return self.client.insert_agent_row({
            "chatbot_id": chatbot_id,
            "owner_id": owner_id,
            "name": name,
            "instructions": instructions,
            "description": description,
            "model": model,
            "grounding": grounding,
            "powabase_agent_id": agent["id"],
            "kb_id": None,
            "kb_full_id": None,
            # Clamped here, never trusted from the caller: the slider is a
            # convenience and a client can post anything.
            "max_context_tokens": clamp_context_tokens(max_context_tokens, model),
        })

    def list(self, chatbot_id: str) -> list:
        """Agents in one chatbot. Ownership is checked on the CHATBOT by the
        caller — a chatbot narrows what you see, it does not replace who you
        are."""
        return self.client.list_agent_rows(chatbot_id)

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
        # Validate before patching, so a bad model can't break a working agent.
        if "model" in fields and fields["model"] != row.get("model"):
            self.probe_model(fields["model"])
        # Re-clamp on every edit. A budget legal for a 200k model is illegal on
        # a 128k one, so changing ONLY the model must still bring it down.
        if "model" in fields or "max_context_tokens" in fields:
            clamped = clamp_context_tokens(
                merged.get("max_context_tokens"), merged["model"]
            )
            if clamped != row.get("max_context_tokens"):
                fields = dict(fields, max_context_tokens=clamped)
            merged["max_context_tokens"] = clamped
        if any(field in fields for field in REMOTE_FIELDS):
            self.client.update_agent(row["powabase_agent_id"], {
                "model": merged["model"],
                "system_prompt": compose_system_prompt(
                    merged["instructions"], merged["grounding"]
                ),
            })
        self.client.update_agent_row(row["id"], fields)
        return merged

    def resync_prompts(self) -> int:
        """Push the current grounding clause onto every existing agent.

        Prompts are otherwise only patched when a user edits instructions or
        grounding, so editing a shared clause in code reaches nothing already
        created — the same trap already recorded for the orchestrator. That
        mattered when the clause was rewritten to tell agents to use their
        knowledge_search tool: without this, existing agents kept the old
        wording and kept answering from memory.

        Model is deliberately not sent: a prompt re-sync must not re-point an
        agent at a different model. Failures are per-agent, so one broken
        record cannot stop the rest. Returns how many were updated.

        Cost is one request per agent at startup. Fine at this scale; a project
        with thousands of agents would want a stored prompt version instead.
        """
        updated = 0
        for row in self.client.list_all_agent_rows():
            remote = row.get("powabase_agent_id")
            if not remote:
                continue
            try:
                self.client.update_agent(remote, {
                    "system_prompt": compose_system_prompt(
                        row.get("instructions") or "", row.get("grounding") or "strict"
                    ),
                })
                updated += 1
            except PowabaseAPIError:
                logger.warning("prompt re-sync skipped agent %s", row.get("id"))
        return updated

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
        """Delete an agent: its permanent KBs and its Powabase agent.

        Chats are NOT touched. They belong to the user, not to an agent — the
        orchestrator picks an agent per message — so deleting one agent must
        leave every conversation intact, including the turns that agent
        answered. (The transcript keeps its name, so old turns stay attributed.)

        Remote cleanup is best-effort so a stale resource never blocks the
        authoritative row delete — the same rule SessionService.delete follows.
        """
        row = self.client.get_agent_row(agent_id)
        if row is None:
            return False

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

        self._prune_from_chat_scopes(agent_id)
        self.client.delete_agent_row(agent_id)
        return True

    def _prune_from_chat_scopes(self, agent_id: str) -> None:
        """Drop this agent from any chat that excluded it.

        Filtering is a membership test, so a stale id is harmless at read time
        — but without this, every deleted agent leaves a permanent entry behind
        and the lists only grow.

        Best-effort, like the resource cleanup above: a chat that cannot be
        updated must not leave the agent undeletable.
        """
        try:
            sessions = self.client.list_all_sessions()
        except PowabaseAPIError:
            return
        for row in sessions:
            excluded = row.get("excluded_agent_ids") or []
            if agent_id not in excluded:
                continue
            try:
                self.client.update_session(
                    row["id"],
                    {"excluded_agent_ids": [i for i in excluded if i != agent_id]},
                )
            except PowabaseAPIError:
                pass


def get_agent_service(request: Request) -> "AgentService":
    """FastAPI dependency returning the shared AgentService created at startup."""
    return request.app.state.agent_service
