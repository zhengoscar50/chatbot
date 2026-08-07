from app.clients.powabase_client import PowabaseAPIError
from app.services.agent_service import AgentService
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE


class FakeClient:
    def __init__(self):
        self.created_agents = []
        self.updated_agents = []
        self.rows = {}
        self.kbs = []
        self.deleted_agents = []
        self.deleted_kbs = []
        self.sessions_for_agent = []
        self.deleted_sessions = []
        self._n = 0

    # Powabase agent API
    def create_agent(self, name, model, system_prompt, settings=None):
        self.created_agents.append((name, model, system_prompt))
        return {"id": f"pa-{len(self.created_agents)}"}

    def update_agent(self, agent_id, fields):
        self.updated_agents.append((agent_id, fields))
        return {"id": agent_id}

    def delete_agent(self, agent_id):
        self.deleted_agents.append(agent_id)

    def create_knowledge_base(self, name, description=None, indexing_config=None,
                              retrieval_config=None):
        self._n += 1
        self.kbs.append((name, indexing_config, retrieval_config))
        return {"id": f"kb-{self._n}"}

    def delete_knowledge_base(self, kb_id):
        self.deleted_kbs.append(kb_id)

    # Rows
    def insert_agent_row(self, row):
        row = dict(row, id="ag-1")
        self.rows[row["id"]] = row
        return row

    def list_agent_rows(self, owner_id):
        return [r for r in self.rows.values() if r.get("owner_id") == owner_id]

    def get_agent_row(self, agent_id):
        return self.rows.get(agent_id)

    def update_agent_row(self, agent_id, fields):
        self.rows[agent_id].update(fields)

    def delete_agent_row(self, agent_id):
        self.rows.pop(agent_id, None)

    def list_sessions_for_agent(self, agent_id):
        return self.sessions_for_agent

    def delete_session_row(self, session_id):
        self.deleted_sessions.append(session_id)


def test_create_makes_a_powabase_agent_with_the_composed_prompt():
    c = FakeClient()
    row = AgentService(c).create("o1", "Tutor", "Be terse.", "gpt-4o-mini", "strict", False)

    name, model, prompt = c.created_agents[0]
    assert model == "gpt-4o-mini"
    assert "Be terse." in prompt and STRICT_CLAUSE in prompt
    assert row["powabase_agent_id"] == "pa-1"
    assert row["owner_id"] == "o1"


def test_create_does_not_provision_knowledge_bases_upfront():
    # KBs are lazy: an agent nobody has trained costs no KB.
    c = FakeClient()
    row = AgentService(c).create("o1", "T", "", "m", "strict", False)
    assert c.kbs == []
    assert row["kb_id"] is None and row["kb_full_id"] is None


def test_ensure_kb_creates_chunk_kb_once_and_persists_it():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    first = svc.ensure_kb(row, full_document=False)
    second = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=False)

    assert first == second
    assert len(c.kbs) == 1
    assert c.rows["ag-1"]["kb_id"] == first


def test_ensure_kb_creates_a_separate_full_document_kb():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    chunk = svc.ensure_kb(row, full_document=False)
    full = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)

    assert chunk != full
    assert c.kbs[0][1] is None
    assert c.kbs[1][1] == {"strategy": "full_document"}


def test_ensure_kb_passes_the_reranker_config():
    c = FakeClient()
    svc = AgentService(c, reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row)
    assert c.kbs[0][2] == {"reranker": {"model": "m", "candidate_count": 20}}


def test_get_owned_returns_none_for_another_users_agent():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "m", "strict", False)
    assert svc.get_owned("ag-1", "someone-else") is None
    assert svc.get_owned("ag-1", "o1") is not None


def test_list_returns_only_the_owners_agents():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "m", "strict", False)
    assert [r["id"] for r in svc.list("o1")] == ["ag-1"]
    assert svc.list("someone-else") == []


def test_update_patches_the_remote_agent_when_prompt_inputs_change():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "Old.", "m", "strict", False)

    svc.update(row, {"instructions": "New.", "grounding": "open"})

    agent_id, fields = c.updated_agents[0]
    assert agent_id == "pa-1"
    assert "New." in fields["system_prompt"] and OPEN_CLAUSE in fields["system_prompt"]


def test_update_patches_the_remote_agent_when_model_changes():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m1", "strict", False)

    svc.update(row, {"model": "m2"})

    assert c.updated_agents[0][1]["model"] == "m2"


def test_update_skips_the_remote_call_for_local_only_fields():
    # Renaming or toggling general knowledge changes nothing Powabase knows about.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    svc.update(row, {"name": "Renamed", "use_general_kb": True})

    assert c.updated_agents == []
    assert c.rows["ag-1"]["name"] == "Renamed"
    assert c.rows["ag-1"]["use_general_kb"] is True


def test_update_returns_the_merged_row():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "Old", "Keep.", "m", "strict", False)

    merged = svc.update(row, {"name": "New"})

    assert merged["name"] == "New"
    assert merged["instructions"] == "Keep."


def test_delete_cascades_to_kbs_chats_and_the_remote_agent():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row, full_document=False)
    svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)
    c.sessions_for_agent = [{"id": "s-1"}, {"id": "s-2"}]

    assert svc.delete("ag-1") is True

    assert set(c.deleted_kbs) == {"kb-1", "kb-2"}
    assert c.deleted_agents == ["pa-1"]
    assert c.deleted_sessions == ["s-1", "s-2"]
    assert c.get_agent_row("ag-1") is None


def test_delete_returns_false_for_unknown_agent():
    assert AgentService(FakeClient()).delete("nope") is False


def test_delete_survives_a_failing_remote_cleanup():
    # The row delete is authoritative; a stale remote resource must not block it.
    class Failing(FakeClient):
        def delete_knowledge_base(self, kb_id):
            raise PowabaseAPIError(404, {"error": "gone"})

    c = Failing()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row)

    assert svc.delete("ag-1") is True
    assert c.get_agent_row("ag-1") is None


def test_delete_also_removes_each_chats_scratch_kb():
    # Cascade deletes session rows directly rather than going through
    # SessionService.delete, so it has to clean up their scratch KBs itself —
    # otherwise every chat leaves an orphaned knowledge base behind.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row)
    c.sessions_for_agent = [
        {"id": "s-1", "kb_id": "scratch-1"},
        {"id": "s-2", "kb_id": None},          # a chat with no uploads
        {"id": "s-3", "kb_id": "scratch-3"},
    ]

    svc.delete("ag-1")

    assert "scratch-1" in c.deleted_kbs
    assert "scratch-3" in c.deleted_kbs
    assert c.deleted_sessions == ["s-1", "s-2", "s-3"]
