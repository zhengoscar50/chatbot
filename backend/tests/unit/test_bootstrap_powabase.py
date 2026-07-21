from scripts.bootstrap_powabase import bootstrap


class FakeClient:
    def __init__(self, existing_kbs=None, existing_agents=None):
        self.existing_kbs = existing_kbs or []
        self.existing_agents = existing_agents or []
        self.created_kbs = []
        self.created_agents = []
        self.linked = []
        self.provider_keys = []

    def create_provider_key(self, provider, api_key):
        self.provider_keys.append((provider, api_key))

    def list_knowledge_bases(self):
        return {"knowledge_bases": self.existing_kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": "kb-new", "name": name}
        self.created_kbs.append(kb)
        return kb

    def list_agents(self):
        return {"agents": self.existing_agents}

    def create_agent(self, name, model, system_prompt):
        agent = {"id": "agent-new", "name": name}
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.linked.append((agent_id, kb_id))


def test_bootstrap_creates_kb_and_agent_when_none_exist():
    client = FakeClient()

    result = bootstrap(client, model="gpt-4o-mini")

    assert result == {"kb_id": "kb-new", "agent_id": "agent-new"}
    assert client.linked == [("agent-new", "kb-new")]


def test_bootstrap_reuses_existing_kb_and_agent():
    client = FakeClient(
        existing_kbs=[{"id": "kb-existing", "name": "rag-chatbot-kb"}],
        existing_agents=[{"id": "agent-existing", "name": "rag-chatbot-agent"}],
    )

    result = bootstrap(client, model="gpt-4o-mini")

    assert result == {"kb_id": "kb-existing", "agent_id": "agent-existing"}
    assert client.created_kbs == []
    assert client.created_agents == []
    assert client.linked == []


def test_bootstrap_registers_provider_key_when_supplied():
    client = FakeClient()

    bootstrap(client, model="gpt-4o-mini", provider="groq", provider_key="secret-key")

    assert client.provider_keys == [("groq", "secret-key")]
