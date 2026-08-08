import json

from app.services.orchestrator import (
    ORCHESTRATOR_AGENT_NAME,
    OrchestratorService,
    ensure_orchestrator_agent,
)

ROSTER = [
    {"id": "ag-chem", "name": "Chem tutor", "description": "AP Chemistry course material."},
    {"id": "ag-legal", "name": "Contracts", "description": "Our vendor contracts and NDAs."},
]


class FakeClient:
    def __init__(self, content=None, raises=False):
        self.content = content
        self.raises = raises
        self.calls = []
        self.agents = []

    def run_agent_sync(self, agent_id, message, response_format=None):
        self.calls.append((agent_id, message, response_format))
        if self.raises:
            raise RuntimeError("provider down")
        return {"content": self.content}

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        agent = {"id": f"a-{name}", "name": name}
        self.agents.append(agent)
        return agent


def decision(content, roster=ROSTER, **kw):
    c = FakeClient(content=content)
    return c, OrchestratorService(c, "orch-1").route("q", roster, **kw)


def test_routes_to_the_named_agent():
    _, d = decision(json.dumps({"agent_id": "ag-legal", "needs_kb": True}))
    assert d.agent_id == "ag-legal"
    assert d.needs_kb is True


def test_null_agent_id_means_the_general_assistant():
    _, d = decision(json.dumps({"agent_id": None, "needs_kb": False}))
    assert d.agent_id is None
    assert d.needs_kb is False


def test_an_agent_id_outside_the_roster_is_rejected():
    # A hallucinated id must never be trusted — it could name another user's
    # agent, or nothing at all.
    _, d = decision(json.dumps({"agent_id": "ag-not-mine", "needs_kb": True}))
    assert d.agent_id is None
    assert d.needs_kb is True


def test_unparseable_output_falls_back_to_the_general_assistant():
    _, d = decision("not json at all")
    assert d.agent_id is None
    assert d.needs_kb is True


def test_missing_needs_kb_defaults_to_retrieving():
    _, d = decision(json.dumps({"agent_id": "ag-chem"}))
    assert d.agent_id == "ag-chem"
    assert d.needs_kb is True


def test_a_provider_error_never_raises():
    c = FakeClient(raises=True)
    d = OrchestratorService(c, "orch-1").route("q", ROSTER)
    assert d.agent_id is None and d.needs_kb is True


def test_an_empty_roster_skips_the_llm_call_entirely():
    # Nothing to choose between: don't pay for a routing call.
    c = FakeClient(content=json.dumps({"agent_id": "x", "needs_kb": True}))
    d = OrchestratorService(c, "orch-1").route("q", [])
    assert d.agent_id is None and d.needs_kb is True
    assert c.calls == []


def test_the_prompt_carries_the_roster_and_recent_turns():
    c, _ = decision(
        json.dumps({"agent_id": "ag-chem", "needs_kb": True}),
        history=[{"role": "user", "text": "what is a mole?"}],
    )
    message = c.calls[0][1]
    assert "ag-chem" in message and "AP Chemistry course material." in message
    assert "ag-legal" in message
    assert "what is a mole?" in message


def test_an_agent_without_a_description_is_still_listed():
    roster = [{"id": "ag-1", "name": "Nameless", "description": ""}]
    c, d = decision(json.dumps({"agent_id": "ag-1", "needs_kb": True}), roster=roster)
    assert "(no description)" in c.calls[0][1]
    assert d.agent_id == "ag-1"


def test_routing_uses_a_strict_json_schema():
    c, _ = decision(json.dumps({"agent_id": "ag-chem", "needs_kb": True}))
    assert c.calls[0][2]["json_schema"]["name"] == "agent_route"


def test_bootstrap_is_find_or_create():
    c = FakeClient()
    first = ensure_orchestrator_agent(c, "gpt-4o-mini")
    assert first == f"a-{ORCHESTRATOR_AGENT_NAME}"
    assert ensure_orchestrator_agent(c, "gpt-4o-mini") == first
    assert len(c.agents) == 1
