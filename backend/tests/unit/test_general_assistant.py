from app.services.general_assistant import (
    GENERAL_ASSISTANT_NAME,
    ensure_general_assistant,
)
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE


class FakeClient:
    def __init__(self):
        self.agents = []
        self.updated = []
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def update_agent(self, agent_id, fields):
        self.updated.append((agent_id, fields))
        return {"id": agent_id}

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


def test_bootstrap_resyncs_the_prompt_on_an_existing_agent():
    c = FakeClient()
    c.agents.append({"id": "existing", "name": GENERAL_ASSISTANT_NAME})

    assert ensure_general_assistant(c, "gpt-4o-mini") == "existing"

    agent_id, fields = c.updated[0]
    assert agent_id == "existing"
    assert OPEN_CLAUSE in fields["system_prompt"]


class _PromptClient:
    def __init__(self, agents):
        self._agents = agents
        self.updated = []

    def list_agents(self):
        return {"agents": self._agents}

    def update_agent(self, agent_id, fields):
        self.updated.append((agent_id, fields))

    def create_agent(self, name, **kwargs):
        return {"id": "new"}


def test_general_assistant_bootstrap_writes_nothing_when_already_correct():
    """Same shared-agent race as the orchestrator: one row, every instance
    rewriting it on boot."""
    from app.services.general_assistant import (
        GENERAL_ASSISTANT_INSTRUCTIONS,
        GENERAL_ASSISTANT_NAME,
        ensure_general_assistant,
    )
    from app.services.prompts import compose_system_prompt

    prompt = compose_system_prompt(GENERAL_ASSISTANT_INSTRUCTIONS, "open")
    client = _PromptClient([{
        "id": "ga-1", "name": GENERAL_ASSISTANT_NAME,
        "system_prompt": prompt, "model": "gpt-5-mini",
    }])

    assert ensure_general_assistant(client, "gpt-5-mini") == "ga-1"
    assert client.updated == []


def test_general_assistant_bootstrap_still_resyncs_when_stale():
    from app.services.general_assistant import (
        GENERAL_ASSISTANT_NAME,
        ensure_general_assistant,
    )

    client = _PromptClient([{
        "id": "ga-1", "name": GENERAL_ASSISTANT_NAME,
        "system_prompt": "stale", "model": "gpt-5-mini",
    }])

    ensure_general_assistant(client, "gpt-5-mini")

    assert [u[0] for u in client.updated] == ["ga-1"]
