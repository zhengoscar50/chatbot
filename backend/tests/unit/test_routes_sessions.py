from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import sessions as sessions_route
from app.clients.powabase_client import get_powabase_client
from app.services.agent_service import get_agent_service
from app.services.chatbot_service import get_chatbot_service
from app.services.session_service import get_session_service


class FakeSessionService:
    def create_session(self, owner_id, chatbot_id, name=None):
        return {"id": "s1", "name": name or "New chat"}

    def list(self, chatbot_id):
        return [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]

    def get_owned_session(self, session_id, owner_id):
        if session_id == "missing":
            return None
        if session_id == "not-mine":
            return None
        return {"id": session_id, "powabase_session_id": "ps1", "chatbot_id": "cb-1"}


class FakeClient:
    """Backs MessageStore: rows from our own messages table."""

    def list_messages(self, session_id):
        return [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "citations": [{"key": "1"}],
             "answered_by_name": "Chem tutor"},
        ]


class FakeChatbots:
    """Stands in for ChatbotService.get_owned.

    owned=True (the default) reports every chatbot as belonging to whoever
    asks — enough for tests that aren't exercising the ownership guard
    itself. owned=False simulates a chatbot that is missing, or somebody
    else's.
    """

    def __init__(self, owned=True):
        self.owned = owned

    def get_owned(self, chatbot_id, owner_id):
        return {"id": chatbot_id, "owner_id": owner_id} if self.owned else None


def build_app(chatbots=None):
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    # PATCH validates exclusions against the caller's roster.
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace(
        list=lambda owner_id: []
    )
    app.dependency_overrides[get_powabase_client] = lambda: FakeClient()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_chatbot_service] = lambda: (chatbots or FakeChatbots())
    return app


def test_create_session_returns_id_and_name():
    r = TestClient(build_app()).post(
        "/sessions", json={"chatbot_id": "cb-1", "name": "Taxes"}
    )
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "Taxes", "excluded_agent_ids": []}


def test_create_session_defaults_name_when_omitted():
    r = TestClient(build_app()).post("/sessions", json={"chatbot_id": "cb-1"})
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "New chat", "excluded_agent_ids": []}


def test_create_session_requires_a_chatbot_you_own():
    app = build_app(chatbots=FakeChatbots(owned=False))
    r = TestClient(app).post("/sessions", json={"chatbot_id": "cb-OTHER"})
    assert r.status_code == 404


def test_list_sessions_for_current_user():
    r = TestClient(build_app()).get("/sessions?chatbot_id=cb-1")
    assert r.status_code == 200
    assert r.json() == [
        {"id": "s1", "name": "Taxes", "updated_at": "t1", "excluded_agent_ids": []}
    ]


def test_listing_sessions_requires_a_chatbot_you_own():
    app = build_app(chatbots=FakeChatbots(owned=False))
    assert TestClient(app).get("/sessions?chatbot_id=cb-1").status_code == 404


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
    # PATCH validates exclusions against the caller's roster.
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace(
        list=lambda owner_id: []
    )
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
    # PATCH validates exclusions against the caller's roster.
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace(
        list=lambda owner_id: []
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    r = TestClient(app).patch("/sessions/s1", json={"name": "My taxes"})

    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "My taxes", "excluded_agent_ids": []}
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
    # PATCH validates exclusions against the caller's roster.
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace(
        list=lambda owner_id: []
    )
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
