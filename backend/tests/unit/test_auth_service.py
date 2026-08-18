import pytest
from app.services.auth_service import (
    AuthService, DuplicateUsernameError, InvalidCredentialsError,
)
from app.core.security import hash_password


class FakeClient:
    def __init__(self, existing=None):
        self.users = list(existing or [])
        self.inserted = []

    def get_user_by_username(self, username):
        return next((u for u in self.users if u["username"] == username), None)

    def insert_user(self, row):
        row = {"id": f"u-{len(self.users)}", **row}
        self.users.append(row)
        self.inserted.append(row)
        return row


def test_register_creates_lowercased_user():
    client = FakeClient()
    user = AuthService(client).register("Alice", "hunter2pass")
    assert user["username"] == "alice"
    assert client.inserted[0]["password_hash"] != "hunter2pass"


def test_register_duplicate_raises():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": "h"}])
    with pytest.raises(DuplicateUsernameError):
        AuthService(client).register("ALICE", "hunter2pass")


def test_authenticate_happy():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": hash_password("hunter2pass")}])
    user = AuthService(client).authenticate("Alice", "hunter2pass")
    assert user["id"] == "u-0"


def test_authenticate_wrong_password_raises():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": hash_password("hunter2pass")}])
    with pytest.raises(InvalidCredentialsError):
        AuthService(client).authenticate("alice", "nope")


def test_authenticate_unknown_user_raises():
    with pytest.raises(InvalidCredentialsError):
        AuthService(FakeClient()).authenticate("ghost", "whatever")


def test_registering_creates_a_default_chatbot():
    """The backfill only covers users who already own something, so without
    this a newly registered account has nowhere to put its first agent.

    Registration is also the only place this can happen exactly once — doing
    it lazily on first list would race two parallel requests into two
    chatbots.
    """
    class Chatbots:
        def __init__(self):
            self.created = []

        def create(self, owner_id, name, description=""):
            self.created.append((owner_id, name))
            return {"id": "cb-1"}

    client = FakeClient()          # already defined in this file
    bots = Chatbots()

    user = AuthService(client, chatbots=bots).register("alice", "pw-12345678")

    assert bots.created == [(user["id"], "My chatbot")]
