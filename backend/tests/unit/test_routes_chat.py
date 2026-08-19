# backend/tests/unit/test_routes_chat.py
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.agent_service import get_agent_service
from app.services.general_assistant import get_general_assistant_id
from app.services.scratch_kb import get_scratch_kb_id
from app.services.chatbot_kb import get_chatbot_kb_service
from app.services.orchestrator import Decision, OrchestratorService, get_orchestrator_agent_id
from app.services.session_service import get_session_service


class FakeSessionService:
    def __init__(self):
        self.touched = []
        self.row = {"id": "s1", "name": "New chat", "chatbot_id": "cb-1",
                    "powabase_session_id": None, "kb_id": "kb-s"}

    def get_owned_session(self, session_id, owner_id):
        return None if session_id == "missing" else self.row

    def touch(self, session_id, **fields):
        self.touched.append((session_id, fields))


DEFAULT_AGENT = {
    "id": "ag-1", "owner_id": "o1", "name": "Chem tutor",
    "powabase_agent_id": "pa-1", "description": "Chemistry.",
    "kb_id": "ag-chunk", "kb_full_id": "ag-full",
    "model": "m",
}


class FakeAgentService:
    def __init__(self, row=None):
        self.row = row if row is not None else DEFAULT_AGENT

    def list(self, owner_id):
        return [self.row] if self.row else []


class FakeMessageClient:
    """Backs MessageStore. The route reads recent turns and writes both turns."""

    def __init__(self):
        self.rows = []

    def list_messages(self, session_id):
        return [r for r in self.rows if r["session_id"] == session_id]

    def insert_message(self, row):
        self.rows.append(row)
        return row


# Records what the route composed, so the KB scope can be asserted.
LAST_CHAT_ARGS = {}


class FakeChatService:
    def __init__(self, client, agent_id, retrieval_kb_ids, top_k=None,
                 max_context_tokens=None):
        LAST_CHAT_ARGS["agent_id"] = agent_id
        LAST_CHAT_ARGS["kb_ids"] = retrieval_kb_ids

    def ask(self, query, message=None, retrieve=True):
        LAST_CHAT_ARGS["retrieve"] = retrieve
        LAST_CHAT_ARGS["query"] = query        # what gets SEARCHED
        LAST_CHAT_ARGS["message"] = message    # what the agent SEES
        return {"answer": "42", "citations": []}


def route_to(agent_id):
    """Pin the orchestrator's decision so route behavior is tested, not routing."""
    return lambda self, q, roster, history=None: Decision(agent_id)


def build_app(session_service, agent_service=None):
    app = FastAPI()
    app.include_router(chat_route.router)
    app.state.message_client = FakeMessageClient()
    app.dependency_overrides[get_powabase_client] = lambda: app.state.message_client
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_agent_service] = lambda: (agent_service or FakeAgentService())
    app.dependency_overrides[get_scratch_kb_id] = lambda: "scratch-kb"
    # An untrained user contributes no personal knowledge base.
    app.dependency_overrides[get_chatbot_kb_service] = lambda: SimpleNamespace(
        kb_ids=lambda row: []
    )
    app.dependency_overrides[get_orchestrator_agent_id] = lambda: "orch-1"
    app.dependency_overrides[get_general_assistant_id] = lambda: "general-1"
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        retrieval_top_k=4, retrieval_max_context_tokens=2000, history_turns=2
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def post(client, body):
    return client.post("/chat", json=body)


def test_chat_returns_the_answer(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()

    response = post(TestClient(build_app(svc)), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "42", "citations": [],
        "answered_by": {"id": None, "name": "General assistant"},
    }


def test_chat_persists_both_turns_and_autonames_on_the_first_message(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route", route_to("ag-1"))
    svc = FakeSessionService()
    app = build_app(svc)

    post(TestClient(app), {"session_id": "s1", "query": "What are my taxes?"})

    rows = app.state.message_client.rows
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "What are my taxes?"
    assert rows[1]["answered_by_name"] == "Chem tutor"

    session_id, fields = svc.touched[0]
    assert session_id == "s1"
    assert fields["name"] == "What are my taxes?"
    assert "updated_at" not in fields  # touch() adds updated_at itself


def test_chat_carries_recent_turns_into_the_agents_message(monkeypatch):
    # Agents run statelessly, so prior turns must reach them in the message.
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route", route_to("ag-1"))
    app = build_app(FakeSessionService())
    app.state.message_client.rows = [
        {"session_id": "s1", "role": "user", "content": "where is the eyewash?"},
        {"session_id": "s1", "role": "assistant", "content": "Corridor Seven."},
    ]

    post(TestClient(app), {"session_id": "s1", "query": "say that again"})

    # The agent sees the history...
    assert "Corridor Seven." in LAST_CHAT_ARGS["message"]
    assert LAST_CHAT_ARGS["message"].rstrip().endswith("Current message: say that again")
    # ...but retrieval searches only the question, or the transcript drowns it.
    assert LAST_CHAT_ARGS["query"] == "say that again"


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
        def ask(self, query, message=None, retrieve=True):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", Insufficient)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_503_when_model_busy(monkeypatch):
    class Busy(FakeChatService):
        def ask(self, query, message=None, retrieve=True):
            raise chat_route.ModelBusyError("The model is busy right now. Please wait a few seconds and try again.")

    monkeypatch.setattr(chat_route, "ChatService", Busy)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 503
    assert "try again" in response.json()["detail"].lower()


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    class ProviderError(FakeChatService):
        def ask(self, query, message=None, retrieve=True):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderError)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "bad key" in detail
    assert "Powabase Studio" in detail


def test_chat_returns_502_when_agent_run_fails(monkeypatch):
    class FailedRun(FakeChatService):
        def ask(self, query, message=None, retrieve=True):
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
    assert response.json() == {
        "answer": "42", "citations": [],
        "answered_by": {"id": None, "name": "General assistant"},
    }


def test_chat_routes_to_the_agent_the_orchestrator_picked(monkeypatch):
    # The picked specialist answers, retrieving from its permanent KBs plus
    # this chat's scratch KB.
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route", route_to("ag-1"))
    LAST_CHAT_ARGS.clear()

    r = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "q"})

    assert LAST_CHAT_ARGS["agent_id"] == "pa-1"
    assert LAST_CHAT_ARGS["kb_ids"] == ["ag-chunk", "ag-full", "kb-s"]
    assert r.json()["answered_by"] == {"id": "ag-1", "name": "Chem tutor"}


def test_chat_falls_back_to_the_general_assistant(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route", route_to(None))
    LAST_CHAT_ARGS.clear()

    r = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert LAST_CHAT_ARGS["agent_id"] == "general-1"
    # Never a specialist's permanent KBs — that would leak one agent's
    # documents into an answer attributed to another.
    assert LAST_CHAT_ARGS["kb_ids"] == ["kb-s"]
    assert r.json()["answered_by"] == {"id": None, "name": "General assistant"}


def test_chat_always_retrieves_and_lets_the_scope_decide(monkeypatch):
    # Retrieval is no longer predicted by the router; ChatService skips it when
    # the scope is empty, which is a fact rather than a guess.
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route", route_to(None))
    LAST_CHAT_ARGS.clear()

    post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert LAST_CHAT_ARGS["retrieve"] is True


def test_a_chat_can_exclude_an_agent_from_answering(monkeypatch):
    """The orchestrator must never be offered an agent this chat excluded.

    Filtering happens before routing rather than after: offering a choice and
    then discarding it would waste the call and let the reason field describe
    an agent that cannot answer.
    """
    seen = {}

    def record(self, query, roster, history=None):
        seen["roster"] = [a["id"] for a in roster]
        return Decision(None)

    monkeypatch.setattr(chat_route.OrchestratorService, "route", record)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()
    svc.row = dict(svc.row, excluded_agent_ids=["ag-1"])
    agents = FakeAgentService(row={"id": "ag-1", "name": "Chem", "powabase_agent_id": "pa-1",
                                   "kb_id": "kb-1", "kb_full_id": None, "model": "gpt-4o-mini"})

    TestClient(build_app(svc, agents)).post(
        "/chat", json={"session_id": "s1", "query": "hi"}
    )

    assert seen["roster"] == []


def test_a_chat_with_no_exclusions_sees_every_agent(monkeypatch):
    seen = {}

    def record(self, query, roster, history=None):
        seen["roster"] = [a["id"] for a in roster]
        return Decision(None)

    monkeypatch.setattr(chat_route.OrchestratorService, "route", record)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()
    agents = FakeAgentService(row={"id": "ag-1", "name": "Chem", "powabase_agent_id": "pa-1",
                                   "kb_id": "kb-1", "kb_full_id": None, "model": "gpt-4o-mini"})

    TestClient(build_app(svc, agents)).post(
        "/chat", json={"session_id": "s1", "query": "hi"}
    )

    assert seen["roster"] == ["ag-1"]


def test_the_roster_comes_from_the_chats_chatbot(monkeypatch):
    """Read from the chat row, never the request: otherwise a client could ask
    one chatbot's question against another's roster."""
    seen = {}

    def record(self, query, roster, history=None):
        seen["roster"] = [a["id"] for a in roster]
        return Decision(None)

    monkeypatch.setattr(chat_route.OrchestratorService, "route", record)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()
    svc.row = dict(svc.row, chatbot_id="cb-1")

    class ScopedAgents:
        def list(self, chatbot_id):
            seen["asked_for"] = chatbot_id
            return [{"id": "ag-1", "name": "A", "powabase_agent_id": "pa-1",
                     "kb_id": "kb-1", "kb_full_id": None, "model": "gpt-4o-mini"}]

    TestClient(build_app(svc, ScopedAgents())).post(
        "/chat", json={"session_id": "s1", "query": "hi", "chatbot_id": "cb-OTHER"}
    )

    assert seen["asked_for"] == "cb-1"
    assert seen["roster"] == ["ag-1"]
