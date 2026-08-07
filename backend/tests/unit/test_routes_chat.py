# backend/tests/unit/test_routes_chat.py
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.agent_service import get_agent_service
from app.services.general_kb import get_general_kb_id
from app.services.router_agent import get_router_agent_id
from app.services.session_service import get_session_service


class FakeSessionService:
    def __init__(self):
        self.touched = []
        self.row = {"id": "s1", "agent_id": "ag-1", "name": "New session",
                    "powabase_session_id": None, "kb_id": "kb-s"}

    def get_owned_session(self, session_id, owner_id):
        return None if session_id == "missing" else self.row

    def touch(self, session_id, **fields):
        self.touched.append((session_id, fields))


class FakeAgentService:
    def __init__(self, row=None):
        self.row = row if row is not None else {
            "id": "ag-1", "owner_id": "o1", "powabase_agent_id": "pa-1",
            "kb_id": "ag-chunk", "kb_full_id": "ag-full",
            "use_general_kb": False, "model": "m",
        }

    def get_owned(self, agent_id, owner_id):
        return self.row


# Records what the route composed, so the KB scope can be asserted.
LAST_CHAT_ARGS = {}


class FakeChatService:
    def __init__(self, client, agent_id, gate, retrieval_kb_ids, top_k, max_context_tokens):
        LAST_CHAT_ARGS["agent_id"] = agent_id
        LAST_CHAT_ARGS["kb_ids"] = retrieval_kb_ids

    def ask(self, query, session_id=None, history=None):
        return {"answer": "42", "session_id": "ps-new", "citations": []}


def build_app(session_service, agent_service=None):
    app = FastAPI()
    app.include_router(chat_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_agent_service] = lambda: (agent_service or FakeAgentService())
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    app.dependency_overrides[get_router_agent_id] = lambda: "router-1"
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        retrieval_top_k=4, retrieval_max_context_tokens=2000, gate_history_turns=2
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def post(client, body):
    return client.post("/chat", json=body)


def test_chat_routes_to_session_agent_and_returns_answer(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()

    response = post(TestClient(build_app(svc)), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "citations": []}


def test_chat_saves_powabase_session_and_autonames_on_first_turn(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()

    post(TestClient(build_app(svc)), {"session_id": "s1", "query": "What are my taxes?"})

    session_id, fields = svc.touched[0]
    assert session_id == "s1"
    assert fields["powabase_session_id"] == "ps-new"
    assert fields["name"] == "What are my taxes?"
    assert "updated_at" not in fields  # touch() adds updated_at itself


def test_chat_404_for_missing_session(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "missing", "query": "hi"})

    assert response.status_code == 404


def test_chat_404_for_non_owned_session(monkeypatch):
    # get_owned_session returns None -> 404
    svc = FakeSessionService()
    svc.get_owned_session = lambda sid, oid: None
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    r = post(TestClient(build_app(svc)), {"session_id": "s1", "query": "hi"})
    assert r.status_code == 404


def test_chat_requires_session_id(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app(FakeSessionService())), {"query": "hi"})

    assert response.status_code == 422


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    class Insufficient(FakeChatService):
        def ask(self, query, session_id=None, history=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", Insufficient)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_503_when_model_busy(monkeypatch):
    class Busy(FakeChatService):
        def ask(self, query, session_id=None, history=None):
            raise chat_route.ModelBusyError("The model is busy right now. Please wait a few seconds and try again.")

    monkeypatch.setattr(chat_route, "ChatService", Busy)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 503
    assert "try again" in response.json()["detail"].lower()


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    class ProviderError(FakeChatService):
        def ask(self, query, session_id=None, history=None):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderError)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "bad key" in detail
    assert "Powabase Studio" in detail


def test_chat_returns_502_when_agent_run_fails(monkeypatch):
    class FailedRun(FakeChatService):
        def ask(self, query, session_id=None, history=None):
            raise RuntimeError("litellm.APIError: insufficient OpenRouter credits")

    monkeypatch.setattr(chat_route, "ChatService", FailedRun)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 502
    assert "insufficient OpenRouter credits" in response.json()["detail"]


def test_chat_returns_answer_even_if_session_persist_fails(monkeypatch):
    # The answer is already computed; a session-row write failure must not
    # fail the request (best-effort persistence).
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    class TouchFailsService(FakeSessionService):
        def touch(self, session_id, **fields):
            raise chat_route.PowabaseAPIError(500, "db down")

    response = post(TestClient(build_app(TouchFailsService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "citations": []}


def test_chat_uses_the_agents_powabase_agent_and_full_kb_scope(monkeypatch):
    # The chat runs on the agent it is bound to, retrieving from that agent's
    # permanent KBs plus this chat's scratch KB.
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    LAST_CHAT_ARGS.clear()

    post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "q"})

    assert LAST_CHAT_ARGS["agent_id"] == "pa-1"
    assert LAST_CHAT_ARGS["kb_ids"] == ["ag-chunk", "ag-full", "kb-s"]


def test_chat_includes_general_kb_only_when_the_agent_opted_in(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    LAST_CHAT_ARGS.clear()
    opted_in = FakeAgentService({
        "id": "ag-1", "owner_id": "o1", "powabase_agent_id": "pa-1",
        "kb_id": "ag-chunk", "kb_full_id": None,
        "use_general_kb": True, "model": "m",
    })

    post(TestClient(build_app(FakeSessionService(), opted_in)), {"session_id": "s1", "query": "q"})

    assert LAST_CHAT_ARGS["kb_ids"] == ["ag-chunk", "kb-s", "gkb-1"]


def test_chat_404_when_the_agent_is_not_the_users(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    not_found = FakeAgentService()
    not_found.row = None  # get_owned returns None for an agent that isn't yours

    response = post(
        TestClient(build_app(FakeSessionService(), not_found)),
        {"session_id": "s1", "query": "q"},
    )

    assert response.status_code == 404
