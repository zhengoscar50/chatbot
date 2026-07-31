import pytest

from app.services.session_service import SessionService, slugify


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.created_kbs = []
        self.created_agents = []
        self.links = []
        self.inserted = []
        self.updated = []

    def create_knowledge_base(self, name, description="", indexing_config=None):
        kb = {"id": f"kb-{name}", "name": name, "indexing_config": indexing_config}
        self.created_kbs.append(kb)
        return kb

    def create_agent(self, name, model, system_prompt):
        agent = {"id": f"agent-{name}", "name": name}
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.links.append((agent_id, kb_id))

    def insert_session(self, row):
        self.inserted.append(row)
        return row

    def list_sessions(self, owner_id):
        return [r for r in self.rows if r.get("owner_id") == owner_id]

    def get_session_row(self, session_id):
        return next((r for r in self.rows if r["id"] == session_id), None)

    def update_session(self, session_id, fields):
        self.updated.append((session_id, fields))


def test_slugify_normalizes():
    assert slugify("Alice Smith!") == "alice-smith"


def test_create_session_sets_owner_and_slug():
    client = FakeClient()
    row = SessionService(client, model="m").create_session("owner-1", "Alice", name="Taxes")
    assert row["owner_id"] == "owner-1"
    assert row["user_slug"] == "alice"
    assert client.links == []
    assert client.inserted and client.inserted[0]["owner_id"] == "owner-1"
    # KB is NOT created up front — it's lazy (see ensure_kb). Agent still is.
    assert client.created_kbs == []
    assert row["kb_id"] == ""
    assert client.created_agents


def test_ensure_kb_creates_and_persists_when_absent():
    client = FakeClient()
    kb_id = SessionService(client, model="m").ensure_kb({"id": "s1", "kb_id": ""})
    assert kb_id == "kb-session-s1-kb"
    assert client.created_kbs[0]["name"] == "session-s1-kb"
    assert client.updated == [("s1", {"kb_id": "kb-session-s1-kb"})]


def test_ensure_kb_returns_existing_without_creating():
    client = FakeClient()
    kb_id = SessionService(client, model="m").ensure_kb({"id": "s1", "kb_id": "kb-existing"})
    assert kb_id == "kb-existing"
    assert client.created_kbs == []
    assert client.updated == []


def test_ensure_kb_full_document_branch_creates_full_kb():
    client = FakeClient()
    kb_id = SessionService(client, model="m").ensure_kb({"id": "s1", "kb_full_id": ""}, full_document=True)
    assert kb_id == "kb-session-s1-full"
    assert client.created_kbs[0]["name"] == "session-s1-full"
    assert client.created_kbs[0]["indexing_config"] == {"strategy": "full_document"}
    assert client.updated == [("s1", {"kb_full_id": "kb-session-s1-full"})]


def test_ensure_kb_chunk_branch_passes_no_indexing_config():
    client = FakeClient()
    SessionService(client, model="m").ensure_kb({"id": "s1", "kb_id": ""})
    assert client.created_kbs[0]["indexing_config"] is None


def test_ensure_kb_full_returns_existing_without_creating():
    client = FakeClient()
    assert SessionService(client, model="m").ensure_kb(
        {"id": "s1", "kb_full_id": "kb-existing"}, full_document=True
    ) == "kb-existing"
    assert client.created_kbs == []


def test_create_session_defaults_name():
    service = SessionService(FakeClient(), model="m")
    row = service.create_session("owner-1", "alice")
    assert row["name"] == "New session"


def test_create_session_rejects_empty_user():
    service = SessionService(FakeClient(), model="m")
    with pytest.raises(ValueError):
        service.create_session("owner-1", "!!!")


def test_list_filters_by_owner():
    client = FakeClient(rows=[
        {"id": "s1", "owner_id": "o1", "name": "A", "updated_at": "t1"},
        {"id": "s2", "owner_id": "o2", "name": "B", "updated_at": "t2"},
    ])
    result = SessionService(client, model="m").list("o1")
    assert result == [{"id": "s1", "name": "A", "updated_at": "t1"}]


def test_get_owned_session_returns_only_for_owner():
    client = FakeClient(rows=[{"id": "s1", "owner_id": "o1", "kb_id": "k", "agent_id": "a"}])
    svc = SessionService(client, model="m")
    assert svc.get_owned_session("s1", "o1")["id"] == "s1"
    assert svc.get_owned_session("s1", "o2") is None
    assert svc.get_owned_session("missing", "o1") is None


def test_touch_sets_updated_at_and_patches():
    client = FakeClient()
    service = SessionService(client, model="m")

    service.touch("s1", name="Renamed")

    session_id, fields = client.updated[0]
    assert session_id == "s1"
    assert fields["name"] == "Renamed"
    assert "updated_at" in fields


def test_create_session_does_not_link_kbs_even_with_general_kb():
    client = FakeClient()
    service = SessionService(client, model="m", general_kb_id="gkb-1")
    service.create_session("owner-1", "alice")
    assert client.links == []  # detached model: no knowledge_search linking


def test_delete_removes_resources_and_row():
    client = FakeClient(rows=[{"id": "s1", "user_slug": "alice", "kb_id": "kb1", "kb_full_id": "kbf", "agent_id": "a1"}])
    client.deleted_kbs = []
    client.deleted_agents = []
    client.deleted_rows = []
    client.delete_knowledge_base = lambda kb_id: client.deleted_kbs.append(kb_id)
    client.delete_agent = lambda agent_id: client.deleted_agents.append(agent_id)
    client.delete_session_row = lambda sid: client.deleted_rows.append(sid)
    service = SessionService(client, model="m")

    result = service.delete("s1")

    assert result is True
    assert set(client.deleted_kbs) == {"kb1", "kbf"}
    assert client.deleted_agents == ["a1"]
    assert client.deleted_rows == ["s1"]


def test_delete_returns_false_for_missing_session():
    client = FakeClient(rows=[])
    client.delete_session_row = lambda sid: (_ for _ in ()).throw(AssertionError("should not delete"))
    service = SessionService(client, model="m")

    assert service.delete("missing") is False


def test_delete_is_best_effort_on_resource_cleanup():
    from app.clients.powabase_client import PowabaseAPIError

    client = FakeClient(rows=[{"id": "s1", "user_slug": "alice", "kb_id": "kb1", "agent_id": "a1"}])
    client.deleted_rows = []
    client.delete_knowledge_base = lambda kb_id: (_ for _ in ()).throw(PowabaseAPIError(404, "gone"))
    client.delete_agent = lambda agent_id: (_ for _ in ()).throw(PowabaseAPIError(404, "gone"))
    client.delete_session_row = lambda sid: client.deleted_rows.append(sid)
    service = SessionService(client, model="m")

    # KB/agent already gone must not block deleting the row.
    assert service.delete("s1") is True
    assert client.deleted_rows == ["s1"]
