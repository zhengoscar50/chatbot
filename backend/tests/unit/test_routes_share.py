"""The public routes: /s/{token}/info, /session, /chat.

Built around one FakeClient that backs ShareService, SessionService and
AgentService through the real service classes, plus MessageStore. Only
ChatService and OrchestratorService.route are monkeypatched — the same
pattern test_routes_chat.py uses — so the routing/answering machinery itself
stays real.

The orchestrator is pinned to a SPECIALIST agent (ag-1, a real id) rather than
falling back to the general assistant. The general assistant's id is always
None, so a test asserting `answered_by.id is None` would pass vacuously if it
answered — pinning to a specialist with a real id makes that assertion prove
redaction happened rather than restating a default.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import share as share_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services import chat_turn
from app.services.agent_service import AgentService, get_agent_service
from app.services.chatbot_kb import get_chatbot_kb_service
from app.services.general_assistant import get_general_assistant_id
from app.services.message_store import get_message_store, MessageStore
from app.services.orchestrator import Decision, OrchestratorService, get_orchestrator_agent_id
from app.services.scratch_kb import get_scratch_kb_id
from app.services.session_service import get_session_service, SessionService
from app.services.share_service import get_share_service, ShareService

SPECIALIST = {
    "id": "ag-1", "name": "Chem tutor", "powabase_agent_id": "pa-1",
    "kb_id": "ag-chunk", "kb_full_id": "ag-full", "model": "m",
}


class FakeClient:
    """Backs ShareService, SessionService, AgentService and MessageStore.

    Also carries `citations` and `answered`, read/appended by FakeChatService
    below — not real PowabaseClient methods, just a convenient single place
    for a test to steer and observe one turn.
    """

    def __init__(self):
        self.chatbots = {
            "cb-shared": {
                "id": "cb-shared", "owner_id": "o1", "name": "Support bot",
                "description": "Ask me anything.", "share_token": "tok",
                "share_daily_limit": 100, "share_used_today": 0,
                "share_used_date": None,
            },
            "cb-other": {
                "id": "cb-other", "owner_id": "o1", "name": "Other bot",
                "description": "", "share_token": None,
            },
        }
        self.sessions = {
            "owner-1": {"id": "owner-1", "owner_id": "o1", "chatbot_id": "cb-shared",
                        "name": "owner's", "shared": False},
            "v1": {"id": "v1", "owner_id": "o1", "chatbot_id": "cb-shared",
                   "name": "visitor's", "shared": True},
            "other-1": {"id": "other-1", "owner_id": "o1", "chatbot_id": "cb-other",
                        "name": "other chatbot's", "shared": True},
        }
        self.agent_rows = {"cb-shared": [SPECIALIST], "cb-other": []}
        self.messages = []
        self.citations = []
        self.answered = []

    # --- ShareService -------------------------------------------------
    def get_chatbot_by_share_token(self, token):
        for row in self.chatbots.values():
            if row.get("share_token") == token:
                return row
        return None

    def update_chatbot_row(self, chatbot_id, fields):
        if chatbot_id in self.chatbots:
            self.chatbots[chatbot_id].update(fields)

    # --- SessionService -------------------------------------------------
    def insert_session(self, row):
        self.sessions[row["id"]] = row
        return row

    def get_session_row(self, session_id):
        return self.sessions.get(session_id)

    def update_session(self, session_id, fields):
        if session_id in self.sessions:
            self.sessions[session_id].update(fields)

    def list_sessions(self, chatbot_id, shared=False):
        return [s for s in self.sessions.values()
                if s.get("chatbot_id") == chatbot_id and s.get("shared", False) == shared]

    # --- AgentService -----------------------------------------------------
    def list_agent_rows(self, chatbot_id):
        return self.agent_rows.get(chatbot_id, [])

    # --- MessageStore -------------------------------------------------
    def insert_message(self, row):
        self.messages.append(row)
        return row

    def list_messages(self, session_id):
        return [m for m in self.messages if m.get("session_id") == session_id]


class FakeChatService:
    """Stands in for chat_turn.ChatService. Reads/writes back onto the client
    it was constructed with, so a test can steer or observe via `fake`."""

    def __init__(self, client, agent_id, retrieval_kb_ids, top_k=None,
                 max_context_tokens=None):
        self.client = client

    def ask(self, query, message=None, retrieve=True):
        self.client.answered.append(query)
        return {"answer": "the answer", "citations": self.client.citations}


def route_to(agent_id):
    return lambda self, q, roster, history=None: Decision(agent_id)


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def client(fake, monkeypatch):
    # ChatService is patched at the class level exactly as test_routes_chat.py
    # does. The orchestrator is pinned to the SPECIALIST (a real agent id) so
    # answered_by.id being None on the way out proves redaction, not the
    # general assistant's default.
    monkeypatch.setattr(chat_turn, "ChatService", FakeChatService)
    monkeypatch.setattr(OrchestratorService, "route", route_to("ag-1"))

    app = FastAPI()
    app.include_router(share_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: fake
    app.dependency_overrides[get_share_service] = lambda: ShareService(fake)
    app.dependency_overrides[get_session_service] = lambda: SessionService(fake)
    app.dependency_overrides[get_agent_service] = lambda: AgentService(fake)
    app.dependency_overrides[get_message_store] = lambda: MessageStore(fake)
    app.dependency_overrides[get_chatbot_kb_service] = lambda: SimpleNamespace(
        kb_ids=lambda row: []
    )
    app.dependency_overrides[get_scratch_kb_id] = lambda: "scratch-kb"
    app.dependency_overrides[get_orchestrator_agent_id] = lambda: "orch-1"
    app.dependency_overrides[get_general_assistant_id] = lambda: "general-1"
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        retrieval_top_k=4, retrieval_max_context_tokens=2000, history_turns=2
    )
    return TestClient(app)


def test_an_unknown_token_is_not_found(client):
    assert client.get("/s/nope/info").status_code == 404
    assert client.post("/s/nope/session").status_code == 404
    assert client.post("/s/nope/chat", json={"session_id": "v1", "query": "hi"}).status_code == 404


def test_info_exposes_only_the_name_and_description(client):
    body = client.get("/s/tok/info").json()
    assert set(body) == {"name", "description"}


def test_a_visitor_cannot_use_an_owners_session(client, fake):
    """THE test. The owner's chats live in the same chatbot as the visitors',
    so chatbot membership alone would let a stranger read and inject into a
    private conversation."""
    res = client.post("/s/tok/chat", json={"session_id": "owner-1", "query": "hi"})
    assert res.status_code == 404
    assert fake.answered == []


def test_a_visitor_cannot_use_a_session_from_another_chatbot(client):
    res = client.post("/s/tok/chat", json={"session_id": "other-1", "query": "hi"})
    assert res.status_code == 404


def test_a_visitor_session_is_created_flagged_shared(client, fake):
    body = client.post("/s/tok/session").json()
    assert fake.sessions[body["session_id"]]["shared"] is True


def test_the_answer_contains_no_filename(client, fake):
    fake.citations = [{"key": 1, "source_id": "u-1",
                       "source_name": "Q3_confidential.pdf", "text_excerpt": "x"}]
    raw = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).text
    assert "Q3_confidential.pdf" not in raw
    assert "u-1" not in raw
    assert "Source 1" in raw


def test_the_answer_exposes_no_agent_id(client, fake):
    body = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).json()
    assert body["answered_by"]["id"] is None
    assert body["answered_by"]["name"]


def test_the_cap_refuses_once_the_limit_is_reached(client, fake):
    fake.chatbots["cb-shared"]["share_daily_limit"] = 1
    assert client.post("/s/tok/chat", json={"session_id": "v1", "query": "a"}).status_code == 200
    res = client.post("/s/tok/chat", json={"session_id": "v1", "query": "b"})
    assert res.status_code == 429
    assert len(fake.answered) == 1


def test_an_upload_field_is_rejected(client):
    res = client.post("/s/tok/chat",
                      json={"session_id": "v1", "query": "hi", "chatbot_id": "cb-other"})
    assert res.status_code == 422
