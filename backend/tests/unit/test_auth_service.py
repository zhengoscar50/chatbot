import pytest
from app.clients.powabase_client import PowabaseAPIError
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


class RaisingClient(FakeClient):
    """A client whose insert_user always fails, to test that no chatbot is
    created when the user row never commits."""

    def __init__(self, status_code, existing=None):
        super().__init__(existing=existing)
        self.status_code = status_code

    def insert_user(self, row):
        raise PowabaseAPIError(self.status_code, "boom")


class Chatbots:
    def __init__(self):
        self.created = []

    def create(self, owner_id, name, description=""):
        self.created.append((owner_id, name))
        return {"id": "cb-1"}


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
    client = FakeClient()          # already defined in this file
    bots = Chatbots()

    user = AuthService(client, chatbots=bots).register("alice", "pw-12345678")

    assert bots.created == [(user["id"], "My chatbot")]


def test_no_chatbot_when_insert_races_into_a_duplicate():
    # A 409 from insert_user means another concurrent register won the
    # username; this register must not have a user row, so it must not get
    # a chatbot either.
    client = RaisingClient(status_code=409)
    bots = Chatbots()

    with pytest.raises(DuplicateUsernameError):
        AuthService(client, chatbots=bots).register("alice", "pw-12345678")

    assert bots.created == []


def test_no_chatbot_when_insert_fails_for_another_reason():
    # Any other insert failure (e.g. the backend is down) must propagate
    # untouched, and still must not create an orphan chatbot.
    client = RaisingClient(status_code=502)
    bots = Chatbots()

    with pytest.raises(PowabaseAPIError):
        AuthService(client, chatbots=bots).register("alice", "pw-12345678")

    assert bots.created == []


def test_no_chatbot_when_username_already_taken():
    # get_user_by_username short-circuits before any insert is attempted, so
    # there is never a user row to hang a chatbot off of.
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": "h"}])
    bots = Chatbots()

    with pytest.raises(DuplicateUsernameError):
        AuthService(client, chatbots=bots).register("ALICE", "hunter2pass")

    assert bots.created == []
