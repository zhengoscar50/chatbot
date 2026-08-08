import pytest
from app.services import admin_users
from app.services.admin_users import UsernameTakenError
from app.core.security import verify_password


class FakeClient:
    def __init__(self):
        self.users = [
            {"id": "u1", "username": "alice", "created_at": "t1", "password_hash": "h"},
            {"id": "u2", "username": "bob", "created_at": "t2", "password_hash": "h"},
        ]
        self.sessions = [
            {"id": "s1", "owner_id": "u1"}, {"id": "s2", "owner_id": "u1"},
        ]
        self.updated = []
        self.deleted_users = []

    def list_users(self): return list(self.users)
    def list_all_sessions(self): return list(self.sessions)
    def list_sessions(self, owner_id): return [s for s in self.sessions if s["owner_id"] == owner_id]
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
    client, ss = FakeClient(), FakeSessionService()
    ags = FakeAgentService([{"id": "ag-1", "owner_id": "u1"},
                            {"id": "ag-mine", "owner_id": "u2"}])

    admin_users.delete_user(client, ss, ags, "u1")

    assert ags.deleted == ["ag-1"]


def test_delete_user_survives_a_failing_agent_delete():
    # The user row delete is authoritative; a stale remote resource must not
    # leave the account half-removed.
    from app.clients.powabase_client import PowabaseAPIError

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
