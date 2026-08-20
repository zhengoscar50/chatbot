import pytest
from app.services import admin_users
from app.services.admin_users import UsernameTakenError
from app.core.security import verify_password
from app.clients.powabase_client import PowabaseAPIError


class FakeClient:
    def __init__(self):
        self.users = [
            {"id": "u1", "username": "alice", "created_at": "t1", "password_hash": "h"},
            {"id": "u2", "username": "bob", "created_at": "t2", "password_hash": "h"},
        ]
        self.sessions = [
            {"id": "s1", "owner_id": "u1"}, {"id": "s2", "owner_id": "u1"},
        ]
        # Owner-scoped listings: separate from `sessions`/chatbot-scoped
        # `list_sessions` because delete_user must find everything a user
        # owns across every chatbot, not just one. Defaults mirror the
        # `sessions` list above and the agents delete_user is expected to
        # find, so tests that don't care about this still pass unchanged.
        self.sessions_by_owner = {"u1": [{"id": "s1"}, {"id": "s2"}]}
        self.agents_by_owner = {"u1": [{"id": "ag-1"}, {"id": "ag-2"}]}
        self.chatbots_by_owner = {}
        self.failing_chatbot_deletes = set()
        self.deleted_kbs = []
        self.failing_kb_deletes = set()
        self.updated = []
        self.deleted_users = []
        self.deleted_chatbots = []

    def list_users(self): return list(self.users)
    def list_all_sessions(self): return list(self.sessions)
    def list_sessions(self, chatbot_id): return [s for s in self.sessions if s.get("chatbot_id") == chatbot_id]
    def list_sessions_by_owner(self, owner_id): return list(self.sessions_by_owner.get(owner_id, []))
    def list_agent_rows_by_owner(self, owner_id): return list(self.agents_by_owner.get(owner_id, []))
    def list_chatbot_rows(self, owner_id): return list(self.chatbots_by_owner.get(owner_id, []))

    def delete_knowledge_base(self, kb_id):
        if kb_id in self.failing_kb_deletes:
            raise PowabaseAPIError(500, {"error": "boom"})
        self.deleted_kbs.append(kb_id)

    def delete_chatbot_row(self, chatbot_id):
        if chatbot_id in self.failing_chatbot_deletes:
            raise PowabaseAPIError(500, {"error": "boom"})
        self.deleted_chatbots.append(chatbot_id)

    def get_user(self, uid): return next((u for u in self.users if u["id"] == uid), None)
    def get_user_by_username(self, name): return next((u for u in self.users if u["username"] == name), None)
    def update_user(self, uid, fields): self.updated.append((uid, fields))
    def delete_user(self, uid): self.deleted_users.append(uid)


class FakeSessionService:
    def __init__(self): self.deleted = []
    def delete(self, sid): self.deleted.append(sid); return True


class FakeAgentService:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [
            {"id": "ag-1", "owner_id": "u1"},
            {"id": "ag-2", "owner_id": "u1"},
        ]
        self.deleted = []

    def list(self, owner_id):
        return [r for r in self.rows if r["owner_id"] == owner_id]

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        return True


def test_list_users_with_counts():
    rows = admin_users.list_users_with_counts(FakeClient())
    by_id = {r["id"]: r for r in rows}
    assert by_id["u1"]["session_count"] == 2
    assert by_id["u2"]["session_count"] == 0
    assert "password_hash" not in by_id["u1"]  # never exposed


def test_delete_user_cascades_sessions_then_user():
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    assert admin_users.delete_user(client, ss, ags, "u1") is True
    assert set(ss.deleted) == {"s1", "s2"}
    assert client.deleted_users == ["u1"]


def test_delete_user_also_deletes_their_agents():
    # Without this the agents survive with no owner: unreachable through the
    # API, still holding their knowledge bases and Powabase agents forever.
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()

    admin_users.delete_user(client, ss, ags, "u1")

    assert set(ags.deleted) == {"ag-1", "ag-2"}


def test_delete_user_leaves_other_users_agents_alone():
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    client.agents_by_owner = {
        "u1": [{"id": "ag-1"}],
        "u2": [{"id": "ag-mine"}],
    }

    admin_users.delete_user(client, ss, ags, "u1")

    assert ags.deleted == ["ag-1"]


def test_delete_user_survives_a_failing_agent_delete():
    # The user row delete is authoritative; a stale remote resource must not
    # leave the account half-removed.
    class Failing(FakeAgentService):
        def delete(self, agent_id):
            raise PowabaseAPIError(404, {"error": "gone"})

    client, ss = FakeClient(), FakeSessionService()
    assert admin_users.delete_user(client, ss, Failing(), "u1") is True
    assert client.deleted_users == ["u1"]


def test_delete_user_missing_returns_false():
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    assert admin_users.delete_user(client, ss, ags, "ghost") is False
    assert client.deleted_users == []
    assert ags.deleted == []


def test_deleting_a_user_still_removes_agents_and_chats_in_every_chatbot():
    """Enumeration is by OWNER, not by chatbot. Listing by chatbot would find
    nothing for a user id and strand everything they own. The fake spreads
    the user's sessions and agents across two different chatbot ids to prove
    delete_user doesn't need to already know either chatbot to find them."""
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    client.sessions_by_owner = {
        "u1": [
            {"id": "s-1", "chatbot_id": "cb-a"},
            {"id": "s-2", "chatbot_id": "cb-b"},
        ]
    }
    client.agents_by_owner = {
        "u1": [
            {"id": "ag-1", "chatbot_id": "cb-a"},
            {"id": "ag-2", "chatbot_id": "cb-b"},
        ]
    }

    assert admin_users.delete_user(client, ss, ags, "u1") is True

    assert set(ss.deleted) == {"s-1", "s-2"}
    assert set(ags.deleted) == {"ag-1", "ag-2"}


def test_delete_user_also_deletes_their_chatbots():
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    client.chatbots_by_owner = {"u1": [{"id": "cb-a"}, {"id": "cb-b"}]}

    admin_users.delete_user(client, ss, ags, "u1")

    assert set(client.deleted_chatbots) == {"cb-a", "cb-b"}


def test_delete_user_survives_a_failing_chatbot_delete():
    # Best-effort, same as sessions/agents: one stale chatbot row must not
    # block the (authoritative) user row delete, and the other chatbot
    # should still get cleaned up.
    client, ss, ags = FakeClient(), FakeSessionService(), FakeAgentService()
    client.chatbots_by_owner = {"u1": [{"id": "cb-a"}, {"id": "cb-b"}]}
    client.failing_chatbot_deletes = {"cb-a"}

    assert admin_users.delete_user(client, ss, ags, "u1") is True

    assert client.deleted_chatbots == ["cb-b"]
    assert client.deleted_users == ["u1"]


def test_reset_password_hashes_and_updates():
    client = FakeClient()
    assert admin_users.reset_password(client, "u1", "newpass123") is True
    uid, fields = client.updated[0]
    assert uid == "u1" and verify_password("newpass123", fields["password_hash"])


def test_reset_password_missing_user_false():
    assert admin_users.reset_password(FakeClient(), "ghost", "newpass123") is False


def test_rename_user_updates():
    client = FakeClient()
    result = admin_users.rename_user(client, "u1", "alice2")
    assert result == {"id": "u1", "username": "alice2"}
    assert client.updated[0] == ("u1", {"username": "alice2"})


def test_rename_user_taken_raises():
    with pytest.raises(UsernameTakenError):
        admin_users.rename_user(FakeClient(), "u1", "bob")


def test_rename_user_same_name_ok():
    # renaming to your own (lowercased) name is not a conflict
    client = FakeClient()
    assert admin_users.rename_user(client, "u1", "Alice")["username"] == "alice"


def test_deleting_a_user_also_deletes_their_chatbots_knowledge_bases():
    """A chatbot owns two knowledge-base tiers since phase 2. Removing only the
    row leaves both alive in Powabase with nothing referencing them — the same
    stranding this function's docstring warns about, in a newer form."""
    client = FakeClient()
    client.chatbots_by_owner["u1"] = [
        {"id": "cb-1", "kb_id": "k-chunked", "kb_full_id": "k-full"},
        {"id": "cb-2", "kb_id": None, "kb_full_id": None},
    ]

    assert admin_users.delete_user(client, FakeSessionService(), FakeAgentService(), "u1")

    assert sorted(client.deleted_kbs) == ["k-chunked", "k-full"]
    assert sorted(client.deleted_chatbots) == ["cb-1", "cb-2"]


def test_a_knowledge_base_that_is_already_gone_does_not_block_the_delete():
    client = FakeClient()
    client.chatbots_by_owner["u1"] = [{"id": "cb-1", "kb_id": "k1", "kb_full_id": None}]
    client.failing_kb_deletes = {"k1"}

    assert admin_users.delete_user(client, FakeSessionService(), FakeAgentService(), "u1")

    assert client.deleted_chatbots == ["cb-1"]
    assert client.deleted_users == ["u1"]
