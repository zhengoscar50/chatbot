# backend/tests/unit/test_profile_service.py
import pytest

from app.services.profile_service import ProfileService, slugify


class FakeClient:
    def __init__(self, existing_kbs=None, existing_agents=None):
        self.kbs = list(existing_kbs or [])
        self.agents = list(existing_agents or [])
        self.list_kb_calls = 0
        self.list_agent_calls = 0
        self.created_kbs = []
        self.created_agents = []
        self.links = []

    def list_knowledge_bases(self):
        self.list_kb_calls += 1
        return {"items": self.kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
        self.kbs.append(kb)
        self.created_kbs.append(kb)
        return kb

    def list_agents(self):
        self.list_agent_calls += 1
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt):
        agent = {"id": f"agent-{name}", "name": name}
        self.agents.append(agent)
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.links.append((agent_id, kb_id))


def test_slugify_normalizes_names():
    assert slugify("Alice") == "alice"
    assert slugify("  Bob Smith! ") == "bob-smith"
    assert slugify("a__b--c") == "a-b-c"


def test_resolve_creates_kb_and_agent_when_absent():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    result = service.resolve("Alice")

    assert result == {
        "slug": "alice",
        "kb_id": "kb-profile-alice-kb",
        "agent_id": "agent-profile-alice-agent",
    }
    assert client.created_kbs and client.created_agents
    assert client.links == [("agent-profile-alice-agent", "kb-profile-alice-kb")]


def test_resolve_reuses_existing_resources():
    client = FakeClient(
        existing_kbs=[{"id": "kb-existing", "name": "profile-alice-kb"}],
        existing_agents=[{"id": "agent-existing", "name": "profile-alice-agent"}],
    )
    service = ProfileService(client, model="test-model")

    result = service.resolve("alice")

    assert result["kb_id"] == "kb-existing"
    assert result["agent_id"] == "agent-existing"
    assert client.created_kbs == []
    assert client.created_agents == []
    assert client.links == []


def test_resolve_caches_after_first_call():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    service.resolve("alice")
    service.resolve("alice")

    assert client.list_kb_calls == 1
    assert client.list_agent_calls == 1


def test_resolve_treats_equivalent_names_as_one_profile():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    first = service.resolve("Alice")
    second = service.resolve("  alice ")

    assert first == second
    assert len(client.created_kbs) == 1


def test_resolve_rejects_names_that_slugify_to_empty():
    service = ProfileService(FakeClient(), model="test-model")

    with pytest.raises(ValueError):
        service.resolve("!!!")
