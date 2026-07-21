# backend/tests/unit/test_routes_chat.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.profile_service import get_profile_service


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


class FakeProfileService:
    def resolve(self, name):
        return {"slug": "alice", "kb_id": "kb-1", "agent_id": "agent-1"}


class FakeChatService:
    def __init__(self, client, agent_id):
        assert agent_id == "agent-1"

    def ask(self, query, session_id=None):
        return {"answer": "42", "session_id": "sess-1", "citations": []}


def build_app():
    app = FastAPI()
    app.include_router(chat_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_profile_service] = lambda: FakeProfileService()
    return app


def post(client, body):
    return client.post("/chat", json=body)


def test_chat_routes_to_profile_agent_and_returns_answer(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app()), {"query": "What is the answer?", "profile": "alice"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_requires_profile(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app()), {"query": "hi"})

    assert response.status_code == 422


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    set_env(monkeypatch)

    class InsufficientService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", InsufficientService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    set_env(monkeypatch)

    class ProviderErrorService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderErrorService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "bad key" in detail
    assert "Powabase Studio" in detail


def test_chat_returns_502_when_agent_run_fails(monkeypatch):
    set_env(monkeypatch)

    class FailedRunService(FakeChatService):
        def ask(self, query, session_id=None):
            raise RuntimeError("litellm.APIError: insufficient OpenRouter credits")

    monkeypatch.setattr(chat_route, "ChatService", FailedRunService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 502
    assert "insufficient OpenRouter credits" in response.json()["detail"]


def test_chat_returns_503_when_model_busy(monkeypatch):
    set_env(monkeypatch)

    class BusyService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ModelBusyError(
                "The model is busy right now. Please wait a few seconds and try again."
            )

    monkeypatch.setattr(chat_route, "ChatService", BusyService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 503
    assert "try again" in response.json()["detail"].lower()
