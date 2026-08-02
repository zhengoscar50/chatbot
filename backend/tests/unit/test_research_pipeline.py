from app.services.research_pipeline import (
    ANALYST_NAME,
    ensure_research_pipeline,
    ORCHESTRATION_NAME,
    RESEARCHER_NAME,
    WRITER_NAME,
)


class FakeClient:
    def __init__(self):
        self.agents = []
        self.orchestrations = []
        self.entities = []
        # Creations are recorded separately from `agents`: ensure_research_pipeline
        # appends newly created agents into the list `list_agents()` handed back
        # (its find-or-create cache), so asserting on `agents` would be asserting
        # on that aliasing rather than on what was actually created.
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        a = {"id": f"a-{name}", "name": name}
        self.created.append((name, model))
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
    assert c.created == [(RESEARCHER_NAME, "m1"), (ANALYST_NAME, "m2"), (WRITER_NAME, "m3")]
    assert c.orchestrations[0]["strategy"] == "sequential"


def test_entities_are_ordered_researcher_analyst_writer():
    # Order is the whole point of a sequential pipeline: the analyst must read
    # the researcher's output and the writer the analyst's. Asserting positions
    # alone would still pass if two agents were swapped.
    c = FakeClient()
    oid = ensure_research_pipeline(c, "m1", "m2", "m3")
    assert c.entities == [
        (oid, f"a-{RESEARCHER_NAME}", "researcher", 0),
        (oid, f"a-{ANALYST_NAME}", "analyst", 1),
        (oid, f"a-{WRITER_NAME}", "writer", 2),
    ]


def test_reuses_when_present():
    c = FakeClient()
    oid = ensure_research_pipeline(c, "m1", "m2", "m3")
    n_before = (len(c.created), len(c.orchestrations), len(c.entities))
    # Idempotent means callers get the same orchestration id back, not merely
    # that nothing new was created.
    assert ensure_research_pipeline(c, "m1", "m2", "m3") == oid
    assert (len(c.created), len(c.orchestrations), len(c.entities)) == n_before
