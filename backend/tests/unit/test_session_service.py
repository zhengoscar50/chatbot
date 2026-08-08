from app.clients.powabase_client import PowabaseAPIError
from app.services.session_service import SessionService


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.created_kbs = []
        self.created_agents = []
        self.inserted = []
        self.updated = []
        self.deleted_kbs = []
        self.deleted_agents = []
        self.deleted_rows = []

    def create_knowledge_base(self, name, description="", indexing_config=None,
                              retrieval_config=None):
        kb = {"id": f"kb-{name}", "name": name, "indexing_config": indexing_config,
              "retrieval_config": retrieval_config}
        self.created_kbs.append(kb)
        return kb

    def create_agent(self, name, model, system_prompt):
        agent = {"id": f"agent-{name}", "name": name}
        self.created_agents.append(agent)
        return agent

    def insert_session(self, row):
        self.inserted.append(row)
        return row

    def list_sessions(self, owner_id):
        return [r for r in self.rows if r.get("owner_id") == owner_id]

    def get_session_row(self, session_id):
        return next((r for r in self.rows if r["id"] == session_id), None)

    def update_session(self, session_id, fields):
        self.updated.append((session_id, fields))

    def delete_knowledge_base(self, kb_id):
        self.deleted_kbs.append(kb_id)

    def delete_agent(self, agent_id):
        self.deleted_agents.append(agent_id)

    def delete_session_row(self, session_id):
        self.deleted_rows.append(session_id)


def test_create_session_creates_no_agent_and_binds_to_none():
    # Chats belong to the user; the orchestrator picks an agent per message.
    c = FakeClient()
    row = SessionService(c).create_session("o1", "My chat")

    assert row["owner_id"] == "o1"
    assert row["name"] == "My chat"
    assert "agent_id" not in row
    assert c.created_agents == []


def test_create_session_defaults_the_name():
    row = SessionService(FakeClient()).create_session("o1")
    assert row["name"] == "New chat"


def test_create_session_does_not_provision_a_kb_upfront():
    c = FakeClient()
    SessionService(c).create_session("o1")
    assert c.created_kbs == []


def test_ensure_kb_creates_one_chunk_scratch_kb():
    c = FakeClient()
    kb_id = SessionService(c).ensure_kb({"id": "s1", "kb_id": None})

    assert kb_id == "kb-chat-s1-kb"
    # chunk_embed: the chunk/full split belongs to the agent's permanent tier.
    assert c.created_kbs[0]["indexing_config"] is None
    assert c.updated == [("s1", {"kb_id": "kb-chat-s1-kb"})]


def test_ensure_kb_returns_existing_without_creating():
    c = FakeClient()
    assert SessionService(c).ensure_kb({"id": "s1", "kb_id": "kb-existing"}) == "kb-existing"
    assert c.created_kbs == []
    assert c.updated == []


def test_ensure_kb_passes_the_reranker_config():
    c = FakeClient()
    svc = SessionService(c, reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    svc.ensure_kb({"id": "s1", "kb_id": None})
    assert c.created_kbs[0]["retrieval_config"] == {"reranker": {"model": "m", "candidate_count": 20}}


def test_list_filters_by_owner():
    c = FakeClient(rows=[
        {"id": "s1", "owner_id": "o1", "name": "A", "updated_at": "t1"},
        {"id": "s2", "owner_id": "o2", "name": "B", "updated_at": "t2"},
    ])
    assert SessionService(c).list("o1") == [{"id": "s1", "name": "A", "updated_at": "t1"}]


def test_get_owned_session_returns_only_for_owner():
    c = FakeClient(rows=[{"id": "s1", "owner_id": "o1", "kb_id": "k", "agent_id": "ag-1"}])
    svc = SessionService(c)
    assert svc.get_owned_session("s1", "o1")["id"] == "s1"
    assert svc.get_owned_session("s1", "o2") is None
    assert svc.get_owned_session("missing", "o1") is None


def test_touch_sets_updated_at_and_patches():
    c = FakeClient()
    SessionService(c).touch("s1", name="Renamed")

    session_id, fields = c.updated[0]
    assert session_id == "s1"
    assert fields["name"] == "Renamed"
    assert "updated_at" in fields


def test_delete_removes_the_scratch_kb_and_row():
    c = FakeClient(rows=[{"id": "s1", "kb_id": "kb1", "agent_id": "ag-1"}])

    assert SessionService(c).delete("s1") is True

    assert c.deleted_kbs == ["kb1"]
    assert c.deleted_rows == ["s1"]


def test_delete_never_deletes_the_agent():
    # agent_id used to be a Powabase agent this session owned. It is now a
    # foreign key to a user-owned agent shared with the user's other chats —
    # deleting a chat must not destroy it.
    c = FakeClient(rows=[{"id": "s1", "kb_id": "kb1", "agent_id": "ag-1"}])

    SessionService(c).delete("s1")

    assert c.deleted_agents == []


def test_delete_returns_false_for_missing_session():
    c = FakeClient(rows=[])
    assert SessionService(c).delete("missing") is False
    assert c.deleted_rows == []


def test_delete_is_best_effort_on_kb_cleanup():
    c = FakeClient(rows=[{"id": "s1", "kb_id": "kb1", "agent_id": "ag-1"}])
    c.delete_knowledge_base = lambda kb_id: (_ for _ in ()).throw(PowabaseAPIError(404, "gone"))

    # A KB that is already gone must not block deleting the row.
    assert SessionService(c).delete("s1") is True
    assert c.deleted_rows == ["s1"]
