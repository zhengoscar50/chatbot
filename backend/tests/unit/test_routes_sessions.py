from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import sessions as sessions_route
from app.clients.powabase_client import get_powabase_client
from app.services.session_service import get_session_service


class FakeSessionService:
    def create_session(self, user, name=None):
        return {"id": "s1", "name": name or "New session"}

    def list(self, user):
        return [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]

    def get(self, session_id):
        if session_id == "missing":
            return None
        return {"id": session_id, "powabase_session_id": "ps1"}


class FakeClient:
    def get_session_messages(self, ps):
        return {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello", "citations": [{"key": "1"}]},
            ]
        }


def build_app():
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    app.dependency_overrides[get_powabase_client] = lambda: FakeClient()
    return app


def test_create_session_returns_id_and_name():
    r = TestClient(build_app()).post("/sessions", json={"user": "alice", "name": "Taxes"})
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "Taxes"}


def test_create_session_requires_user():
    r = TestClient(build_app()).post("/sessions", json={"name": "Taxes"})
    assert r.status_code == 422


def test_list_sessions_for_user():
    r = TestClient(build_app()).get("/sessions", params={"user": "alice"})
    assert r.status_code == 200
    assert r.json() == [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]


def test_messages_formats_roles_and_citations():
    r = TestClient(build_app()).get("/sessions/s1/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"][0] == {"role": "user", "text": "hi", "citations": []}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["text"] == "hello"
    assert body["messages"][1]["citations"] == [{"key": "1"}]


def test_messages_404_for_missing_session():
    r = TestClient(build_app()).get("/sessions/missing/messages")
    assert r.status_code == 404
