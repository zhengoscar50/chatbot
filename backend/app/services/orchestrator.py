from __future__ import annotations

import json
from collections import namedtuple

from fastapi import Request

ORCHESTRATOR_AGENT_NAME = "agent-orchestrator"

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You route a user's message to the assistant best suited to answer it.\n"
    "You are given a roster of the user's assistants, each with an id, a name "
    "and a description of what it covers, plus the recent conversation.\n"
    "- Set agent_id to the id of the assistant whose description covers the "
    "message. Choose exactly one. Prefer a specialist whenever its description "
    "plausibly relates to the topic, even if the wording differs — a handbook "
    "about a subject covers the details inside it.\n"
    "- Set agent_id to null when no assistant covers it, or for greetings, "
    "small talk and general questions — a general assistant handles those.\n"
    "- For a follow-up that continues the previous exchange (\"explain that "
    "again\", \"why?\"), keep the assistant that just answered.\n"
    "- Set needs_kb to true if answering could depend on specific documents, "
    "facts or data; false only for greetings, small talk, or questions needing "
    "no lookup. When unsure, choose true.\n"
    "Give a one-sentence reason naming the description you matched (or why "
    "none fits) before choosing.\n"
    "Respond only as JSON."
)

ROUTE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_route",
        "schema": {
            "type": "object",
            "properties": {
                # First, and required: making the model state which description
                # it matched before committing measurably improves the pick on
                # borderline queries, and gives a human-readable trace when a
                # message routes somewhere surprising.
                "reason": {"type": "string"},
                "agent_id": {"type": ["string", "null"]},
                "needs_kb": {"type": "boolean"},
            },
            "required": ["reason", "agent_id", "needs_kb"],
            "additionalProperties": False,
        },
    },
}

# agent_id is None when the general assistant should answer. `reason` is the
# router's own explanation, kept for debugging a surprising route.
Decision = namedtuple("Decision", "agent_id needs_kb reason", defaults=("",))

GENERAL = Decision(None, True, "routing unavailable")


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def _parse_json(content: str) -> dict:
    """Parse the router's reply, tolerating a markdown code fence.

    Even with a json_schema response format, the model sometimes wraps its
    output in ```json … ```. Bare json.loads then raises, and the fail-safe
    quietly sends every such message to the general assistant — routing looks
    like it "just prefers general" instead of failing visibly.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -len("```")]
    return json.loads(text.strip())


def ensure_orchestrator_agent(client, model: str) -> str:
    """Find-or-create the shared routing agent; return its id.

    An existing agent has its prompt and model re-synced from the code. Without
    that, editing ORCHESTRATOR_SYSTEM_PROMPT would change nothing on a project
    where the agent already exists — the routing behaviour would silently stay
    on whatever prompt shipped first, which is very hard to notice.
    """
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, ORCHESTRATOR_AGENT_NAME)
    if agent is None:
        return client.create_agent(
            ORCHESTRATOR_AGENT_NAME,
            model=model,
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            settings={"temperature": 0},
        )["id"]
    client.update_agent(
        agent["id"], {"system_prompt": ORCHESTRATOR_SYSTEM_PROMPT, "model": model}
    )
    return agent["id"]


class OrchestratorService:
    """Chooses which agent answers, and whether to retrieve.

    One cheap sync LLM call decides both, so routing costs no extra round trip
    over the retrieval gate it replaces.

    Never raises: any failure resolves to the general assistant with retrieval
    on, so a broken router degrades to a working chatbot rather than a broken
    one.
    """

    def __init__(self, client, orchestrator_agent_id: str):
        self.client = client
        self.orchestrator_agent_id = orchestrator_agent_id

    def route(self, query: str, roster: list, history: list | None = None) -> Decision:
        if not roster:
            # Nothing to choose between — don't pay for the call.
            return GENERAL
        try:
            response = self.client.run_agent_sync(
                self.orchestrator_agent_id,
                self._build_message(query, roster, history or []),
                response_format=ROUTE_RESPONSE_FORMAT,
            )
            data = _parse_json(response["content"])
        except Exception:
            return GENERAL

        needs_kb = data.get("needs_kb")
        needs_kb = True if needs_kb is None else bool(needs_kb)

        agent_id = data.get("agent_id")
        # Never trust an id the model invented: it could name another user's
        # agent, or nothing at all.
        if agent_id not in {a["id"] for a in roster}:
            agent_id = None
        return Decision(agent_id, needs_kb, str(data.get("reason") or ""))

    @staticmethod
    def _build_message(query: str, roster: list, history: list) -> str:
        lines = ["Available assistants:"]
        for agent in roster:
            description = (agent.get("description") or "").strip() or "(no description)"
            lines.append(
                "- id=%s | name=%s | covers: %s" % (agent["id"], agent["name"], description)
            )
        if history:
            lines.append("")
            lines.append("Recent conversation:")
            for turn in history:
                lines.append("%s: %s" % (turn.get("role", "user"), turn.get("text", "")))
        lines.append("")
        lines.append("Current user message: %s" % query)
        lines.append("Which assistant should answer, and is a document lookup needed?")
        return "\n".join(lines)


def get_orchestrator_agent_id(request: Request) -> str:
    """FastAPI dependency returning the orchestrator agent id from startup."""
    return request.app.state.orchestrator_agent_id
