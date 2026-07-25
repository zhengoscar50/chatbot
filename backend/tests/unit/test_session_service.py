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
    assert client.links == [(row["agent_id"], row["kb_id"])]
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


def test_create_session_links_general_kb_when_set():
    client = FakeClient()
    service = SessionService(client, model="m", general_kb_id="gkb-1")

    row = service.create_session("alice")

    # Agent linked to BOTH its own session KB and the general KB.
    assert (row["agent_id"], row["kb_id"]) in client.links
    assert (row["agent_id"], "gkb-1") in client.links
    assert len(client.links) == 2


def test_create_session_links_only_session_kb_when_general_none():
    client = FakeClient()
    service = SessionService(client, model="m")  # general_kb_id defaults to None

    row = service.create_session("alice")

    assert client.links == [(row["agent_id"], row["kb_id"])]
