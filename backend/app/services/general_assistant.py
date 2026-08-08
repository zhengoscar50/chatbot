from __future__ import annotations

from fastapi import Request

from app.services.prompts import compose_system_prompt

GENERAL_ASSISTANT_NAME = "general-assistant"

GENERAL_ASSISTANT_INSTRUCTIONS = (
    "You are a helpful general assistant. You answer questions that none of the "
    "user's specialist assistants cover, along with greetings and small talk."
)


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def ensure_general_assistant(client, model: str) -> str:
    """Find-or-create the shared fallback assistant; return its id.

    Open grounding, not strict: it exists to answer what no specialist covers,
    so refusing whenever there is no retrieved context would make it useless.
    """
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, GENERAL_ASSISTANT_NAME)
    if agent is None:
        agent = client.create_agent(
            GENERAL_ASSISTANT_NAME,
            model=model,
            system_prompt=compose_system_prompt(GENERAL_ASSISTANT_INSTRUCTIONS, "open"),
        )
    return agent["id"]


def get_general_assistant_id(request: Request) -> str:
    """FastAPI dependency returning the general assistant id from startup."""
    return request.app.state.general_assistant_id
