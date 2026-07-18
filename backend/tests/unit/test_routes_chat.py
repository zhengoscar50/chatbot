# backend/tests/unit/test_routes_chat.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def build_app():
    app = FastAPI()
    app.include_router(chat_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    return app


class FakeChatService:
    def __init__(self, client, agent_id):
        pass

    def ask(self, query, session_id=None):
        return {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_returns_answer(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = TestClient(build_app()).post("/chat", json={"query": "What is the answer?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    set_env(monkeypatch)

    class InsufficientService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", InsufficientService)

    response = TestClient(build_app()).post("/chat", json={"query": "hi"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    set_env(monkeypatch)

    class ProviderErrorService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderErrorService)

    response = TestClient(build_app()).post("/chat", json={"query": "hi"})

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "bad key" in detail
    assert "Powabase Studio" in detail
