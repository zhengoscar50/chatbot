from app.services.research_pipeline import ensure_research_pipeline, ORCHESTRATION_NAME, RESEARCHER_NAME


class FakeClient:
    def __init__(self):
        self.agents = []
        self.orchestrations = []
        self.entities = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        a = {"id": f"a-{name}", "name": name}
        return a

    def list_orchestrations(self):
        return {"orchestrations": self.orchestrations}

    def create_orchestration(self, name, strategy, orchestrator_config=None):
        o = {"id": f"o-{name}", "name": name, "strategy": strategy}
        self.orchestrations.append(o)
        return o

    def add_orchestration_entity(self, oid, agent_id, role_description, position=0):
        self.entities.append((oid, agent_id, role_description, position))
        return {"id": "e"}


def test_creates_three_agents_sequential_orchestration_when_absent():
    c = FakeClient()
    oid = ensure_research_pipeline(c, "m1", "m2", "m3")
    assert oid == f"o-{ORCHESTRATION_NAME}"
    assert len(c.agents) == 3
    assert [e[3] for e in c.entities] == [0, 1, 2]  # ordered researcher/analyst/writer
    assert c.orchestrations[0]["strategy"] == "sequential"


def test_reuses_when_present():
    c = FakeClient()
    ensure_research_pipeline(c, "m1", "m2", "m3")
    n_before = (len(c.agents), len(c.orchestrations), len(c.entities))
    ensure_research_pipeline(c, "m1", "m2", "m3")  # idempotent
    assert (len(c.agents), len(c.orchestrations), len(c.entities)) == n_before
