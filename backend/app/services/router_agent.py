from fastapi import Request

ROUTER_AGENT_NAME = "kb-router-agent"

ROUTER_SYSTEM_PROMPT = (
    "You decide whether answering a user's message requires retrieving from a "
    "knowledge base of the user's uploaded documents and curated general "
    "knowledge.\n"
    "- Return needs_kb=true if a good answer could depend on specific facts, "
    "documents, policies, data, product or domain details, or anything that "
    "would live in such a knowledge base — or if you are unsure.\n"
    "- Return needs_kb=false ONLY when the message clearly needs no such lookup: "
    "greetings, small talk, thanks, meta questions about the conversation "
    "itself, or basic general knowledge you already know.\n"
    "When in doubt, choose true. Respond only as JSON."
)

GATE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "kb_gate",
        "schema": {
            "type": "object",
            "properties": {"needs_kb": {"type": "boolean"}},
            "required": ["needs_kb"],
            "additionalProperties": False,
        },
    },
}


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def ensure_router_agent(client, model: str) -> str:
    """Find-or-create the shared KB-router agent; return its id."""
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, ROUTER_AGENT_NAME)
    if agent is None:
        agent = client.create_agent(
            ROUTER_AGENT_NAME,
            model=model,
            system_prompt=ROUTER_SYSTEM_PROMPT,
            settings={"temperature": 0},
        )
    return agent["id"]


def get_router_agent_id(request: Request) -> str:
    """FastAPI dependency returning the router agent id resolved at startup."""
    return request.app.state.router_agent_id
