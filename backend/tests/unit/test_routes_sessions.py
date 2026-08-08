from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import sessions as sessions_route
from app.clients.powabase_client import get_powabase_client
from app.services.session_service import get_session_service


class FakeSessionService:
    def create_session(self, owner_id, name=None):
        return {"id": "s1", "name": name or "New chat"}

    def list(self, owner_id):
        return [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]

    def get_owned_session(self, session_id, owner_id):
        if session_id == "missing":
            return None
        if session_id == "not-mine":
            return None
        return {"id": session_id, "powabase_session_id": "ps1"}


class FakeClient:
    """Backs MessageStore: rows from our own messages table."""

    def list_messages(self, session_id):
        return [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "citations": [{"key": "1"}],
             "answered_by_name": "Chem tutor"},
        ]


def build_app():
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    app.dependency_overrides[get_powabase_client] = lambda: FakeClient()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def test_create_session_returns_id_and_name():
    r = TestClient(build_app()).post("/sessions", json={"name": "Taxes"})
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "Taxes"}


def test_create_session_defaults_name_when_omitted():
    r = TestClient(build_app()).post("/sessions", json={})
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "New chat"}


def test_list_sessions_for_current_user():
    r = TestClient(build_app()).get("/sessions")
    assert r.status_code == 200
    assert r.json() == [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]


def test_messages_formats_roles_and_citations():
    r = TestClient(build_app()).get("/sessions/s1/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"][0] == {
        "role": "user", "text": "hi", "citations": [], "answered_by": None,
    }
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["text"] == "hello"
    assert body["messages"][1]["citations"] == [{"key": "1"}]
    # The transcript remembers which agent answered, so reopening a chat
    # still shows the attribution the badge displayed live.
    assert body["messages"][1]["answered_by"] == "Chem tutor"


def test_messages_404_for_missing_session():
    r = TestClient(build_app()).get("/sessions/missing/messages")
    assert r.status_code == 404


def test_get_messages_404_for_non_owner():
    # get_owned_session returns None for a session that exists but isn't owned
    # by the current user -> 404 (indistinguishable from missing).
    r = TestClient(build_app()).get("/sessions/not-mine/messages")
    assert r.status_code == 404


def test_messages_empty_for_a_brand_new_chat():
    # A chat nobody has spoken in yet simply has no rows.
    class EmptyClient:
        def list_messages(self, session_id):
            return []

    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    app.dependency_overrides[get_powabase_client] = lambda: EmptyClient()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    r = TestClient(app).get("/sessions/s1/messages")

    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_rename_session_updates_name():
    renamed = {}

    class RenamingService(FakeSessionService):
        def rename(self, session_id, name):
            renamed["args"] = (session_id, name)

    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: RenamingService()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    r = TestClient(app).patch("/sessions/s1", json={"name": "My taxes"})

    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "My taxes"}
    assert renamed["args"] == ("s1", "My taxes")


def test_rename_requires_nonempty_name():
    r = TestClient(build_app()).patch("/sessions/s1", json={"name": ""})
    assert r.status_code == 422


def test_rename_404_for_missing_session():
    r = TestClient(build_app()).patch("/sessions/missing", json={"name": "x"})
    assert r.status_code == 404


def test_rename_404_for_non_owner():
    r = TestClient(build_app()).patch("/sessions/not-mine", json={"name": "x"})
    assert r.status_code == 404


def test_delete_session_returns_204():
    deleted = {}

    class DeletingService(FakeSessionService):
        def delete(self, session_id):
            deleted["id"] = session_id
            return True

    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: DeletingService()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    r = TestClient(app).delete("/sessions/s1")

    assert r.status_code == 204
    assert deleted["id"] == "s1"


def test_delete_404_for_missing_session():
    r = TestClient(build_app()).delete("/sessions/missing")
    assert r.status_code == 404


def test_delete_404_for_non_owner():
    r = TestClient(build_app()).delete("/sessions/not-mine")
    assert r.status_code == 404
