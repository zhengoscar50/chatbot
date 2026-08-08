from app.services.general_assistant import (
    GENERAL_ASSISTANT_NAME,
    ensure_general_assistant,
)
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE


class FakeClient:
    def __init__(self):
        self.agents = []
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        self.created.append((name, model, system_prompt))
        agent = {"id": f"a-{name}", "name": name}
        self.agents.append(agent)
        return agent


def test_creates_the_shared_assistant_when_absent():
    c = FakeClient()
    agent_id = ensure_general_assistant(c, "gpt-4o-mini")
    assert agent_id == f"a-{GENERAL_ASSISTANT_NAME}"
    assert c.created[0][1] == "gpt-4o-mini"


def test_is_find_or_create():
    c = FakeClient()
    first = ensure_general_assistant(c, "gpt-4o-mini")
    assert ensure_general_assistant(c, "gpt-4o-mini") == first
    assert len(c.created) == 1


def test_uses_open_grounding():
    # It answers questions no specialist covers, so refusing without context
    # would make it useless.
    c = FakeClient()
    ensure_general_assistant(c, "gpt-4o-mini")
    prompt = c.created[0][2]
    assert OPEN_CLAUSE in prompt
    assert STRICT_CLAUSE not in prompt
