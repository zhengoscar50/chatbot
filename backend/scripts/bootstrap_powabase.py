"""One-time setup: create the Powabase Knowledge Base and Agent for this project.

Run from backend/ with: python -m scripts.bootstrap_powabase
"""
from __future__ import annotations

import os

from app.clients.powabase_client import PowabaseClient

KB_NAME = "rag-chatbot-kb"
AGENT_NAME = "rag-chatbot-agent"
SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)


def find_by_name(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if item.get("name") == name), None)


def bootstrap(
    client: PowabaseClient,
    model: str,
    provider: str | None = None,
    provider_key: str | None = None,
) -> dict:
    if provider and provider_key:
        client.create_provider_key(provider, provider_key)

    existing_kbs = client.list_knowledge_bases().get("items", [])
    kb = find_by_name(existing_kbs, KB_NAME)
    if kb is None:
        kb = client.create_knowledge_base(KB_NAME, description="RAG chatbot knowledge base")

    existing_agents = client.list_agents().get("agents", [])
    agent = find_by_name(existing_agents, AGENT_NAME)
    if agent is None:
        agent = client.create_agent(AGENT_NAME, model=model, system_prompt=SYSTEM_PROMPT)
        client.link_kb_to_agent(agent["id"], kb["id"])

    return {"kb_id": kb["id"], "agent_id": agent["id"]}


def main() -> None:
    base_url = os.environ["POWABASE_BASE_URL"]
    service_role_key = os.environ["POWABASE_SERVICE_ROLE_KEY"]
    model = os.environ.get("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    provider = os.environ.get("POWABASE_PROVIDER_NAME") or None
    provider_key = os.environ.get("POWABASE_PROVIDER_KEY") or None

    client = PowabaseClient(base_url, service_role_key)
    result = bootstrap(client, model, provider=provider, provider_key=provider_key)

    print(f"POWABASE_KB_ID={result['kb_id']}")
    print(f"POWABASE_AGENT_ID={result['agent_id']}")


if __name__ == "__main__":
    main()
