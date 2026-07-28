from app.services.router_agent import (
    ROUTER_AGENT_NAME,
    ensure_router_agent,
    GATE_RESPONSE_FORMAT,
)


class FakeClient:
    def __init__(self, existing=None):
        self.agents = list(existing or [])
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        agent = {"id": f"agent-{name}", "name": name, "settings": settings}
        self.agents.append(agent)
        self.created.append(agent)
        return agent


def test_ensure_router_agent_creates_when_absent():
    client = FakeClient()
    agent_id = ensure_router_agent(client, "gpt-4o-mini")
    assert agent_id == f"agent-{ROUTER_AGENT_NAME}"
    assert client.created[0]["name"] == ROUTER_AGENT_NAME
    assert client.created[0]["settings"] == {"temperature": 0}


def test_ensure_router_agent_reuses_when_present():
    client = FakeClient(existing=[{"id": "router-existing", "name": ROUTER_AGENT_NAME}])
    assert ensure_router_agent(client, "gpt-4o-mini") == "router-existing"
    assert client.created == []


def test_gate_response_format_shape():
    schema = GATE_RESPONSE_FORMAT["json_schema"]["schema"]
    assert schema["properties"]["needs_kb"]["type"] == "boolean"
    assert schema["required"] == ["needs_kb"]
