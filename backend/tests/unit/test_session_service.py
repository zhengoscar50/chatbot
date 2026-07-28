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

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
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

    def list_sessions(self, user_slug):
        return [r for r in self.rows if r["user_slug"] == user_slug]

    def get_session_row(self, session_id):
        return next((r for r in self.rows if r["id"] == session_id), None)

    def update_session(self, session_id, fields):
        self.updated.append((session_id, fields))


def test_slugify_normalizes():
    assert slugify("Alice Smith!") == "alice-smith"


def test_create_session_provisions_and_inserts():
    client = FakeClient()
    service = SessionService(client, model="m")

    row = service.create_session("Alice", name="Taxes")

    assert row["user_slug"] == "alice"
    assert row["name"] == "Taxes"
    # KB + agent named from the row id, agent linked to the session KB
    assert client.created_kbs[0]["name"] == f"session-{row['id']}-kb"
    assert client.created_agents[0]["name"] == f"session-{row['id']}-agent"
    assert client.links == []  # detached model: KBs are no longer linked to the agent
    assert client.inserted and client.inserted[0]["id"] == row["id"]


def test_create_session_defaults_name():
    service = SessionService(FakeClient(), model="m")
    row = service.create_session("alice")
    assert row["name"] == "New session"


def test_create_session_rejects_empty_user():
    service = SessionService(FakeClient(), model="m")
    with pytest.raises(ValueError):
        service.create_session("!!!")


def test_list_returns_summaries_for_user():
    client = FakeClient(
        rows=[
            {"id": "s1", "user_slug": "alice", "name": "A", "updated_at": "t1"},
            {"id": "s2", "user_slug": "bob", "name": "B", "updated_at": "t2"},
        ]
    )
    service = SessionService(client, model="m")

    result = service.list("alice")

    assert result == [{"id": "s1", "name": "A", "updated_at": "t1"}]


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
    service.create_session("alice")
    assert client.links == []  # detached model: no knowledge_search linking


def test_delete_removes_resources_and_row():
    client = FakeClient(rows=[{"id": "s1", "user_slug": "alice", "kb_id": "kb1", "agent_id": "a1"}])
    client.deleted_kbs = []
    client.deleted_agents = []
    client.deleted_rows = []
    client.delete_knowledge_base = lambda kb_id: client.deleted_kbs.append(kb_id)
    client.delete_agent = lambda agent_id: client.deleted_agents.append(agent_id)
    client.delete_session_row = lambda sid: client.deleted_rows.append(sid)
    service = SessionService(client, model="m")

    result = service.delete("s1")

    assert result is True
    assert client.deleted_kbs == ["kb1"]
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
