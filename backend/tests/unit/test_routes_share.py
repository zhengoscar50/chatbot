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
from fastapi.middleware.cors import CORSMiddleware
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
        self.answer = "the answer"
        self.answered = []
        self.uploaded = []
        self.indexed = []

    # --- IngestService ------------------------------------------------
    # Only what a visitor upload touches. The background finisher runs after
    # the response, so a test asserting on the cap never reaches indexing.
    def upload_source(self, filename, content):
        self.uploaded.append(filename)
        return {"id": f"src-{len(self.uploaded)}"}

    # The finisher runs as a background task, which TestClient executes inside
    # the request. These let it complete quietly so a test about the cap is not
    # really a test about indexing.
    def get_source(self, source_id):
        return {"id": source_id, "extraction_status": "extracted",
                "auto_metadata": {"char_count": 10}}

    def add_source_to_kb(self, kb_id, source_id):
        self.indexed.append((kb_id, source_id))
        return source_id

    def list_kb_sources(self, kb_id):
        return {"items": [{"source_id": sid, "index_status": "indexed"}
                          for _, sid in self.indexed]}

    def update_session_row(self, session_id, fields):
        self.sessions.setdefault(session_id, {}).update(fields)

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
        return {"answer": self.client.answer, "citations": self.client.citations}


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
    # Mirrors main.py's create_app() exactly: the widget's loader calls these
    # routes from the HOST page's origin, so this is what makes the panel load
    # on any real third-party site instead of a blank rectangle.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
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


def test_an_unknown_token_is_not_found(client, fake):
    assert client.get("/s/nope/info").status_code == 404
    assert client.post("/s/nope/session").status_code == 404
    assert client.post("/s/nope/chat", json={"session_id": "v1", "query": "hi"}).status_code == 404
    assert fake.answered == []
    assert fake.chatbots["cb-shared"]["share_used_today"] == 0


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


def test_a_visitor_cannot_use_a_session_from_another_chatbot(client, fake):
    res = client.post("/s/tok/chat", json={"session_id": "other-1", "query": "hi"})
    assert res.status_code == 404
    assert fake.answered == []
    assert fake.chatbots["cb-shared"]["share_used_today"] == 0


def test_a_visitor_session_is_created_flagged_shared(client, fake):
    body = client.post("/s/tok/session").json()
    assert fake.sessions[body["session_id"]]["shared"] is True


def test_session_creation_is_capped_at_the_daily_limit(client, fake):
    """The reviewer's finding: an uncapped /session let 50 requests create 50
    session rows with no LLM spend involved, poisoning the "N visitor chats"
    dashboard readout. has_room refuses without writing anything."""
    from datetime import date

    fake.chatbots["cb-shared"]["share_daily_limit"] = 1
    fake.chatbots["cb-shared"]["share_used_today"] = 1
    fake.chatbots["cb-shared"]["share_used_date"] = date.today().isoformat()
    sessions_before = set(fake.sessions)
    res = client.post("/s/tok/session")
    assert res.status_code == 429
    assert res.json()["detail"] == "This demo has reached its limit for today — try again tomorrow."
    assert set(fake.sessions) == sessions_before


def test_the_answer_contains_no_filename(client, fake):
    fake.citations = [{"key": 1, "source_id": "u-1",
                       "source_name": "Q3_confidential.pdf", "text_excerpt": "x"}]
    raw = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).text
    assert "Q3_confidential.pdf" not in raw
    assert "u-1" not in raw
    assert "Source 1" in raw


def test_the_answer_text_itself_is_redacted(client, fake):
    """prompts.py tells every agent to cite its sources, so the model's own
    prose can name a document even though `citations` is already redacted.
    This is the cheap partial fix: a filename the model just cited is scrubbed
    from the answer too, using the same label the citation list shows."""
    fake.answer = "That's from Q3_confidential.pdf, page 2."
    fake.citations = [{"key": 1, "source_id": "u-1",
                       "source_name": "Q3_confidential.pdf", "text_excerpt": "x"}]
    body = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).json()
    assert "Q3_confidential.pdf" not in body["answer"]
    assert "Source 1" in body["answer"]
    assert body["citations"][0]["source_name"] == "Source 1"


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


# --- GET /s/{token}/session/{session_id}/messages -------------------------
#
# The brief for this suite sketches a `build_app()` helper returning
# `(app, fakes)` with `fakes.chatbots.rows` / `fakes.sessions.rows` /
# `fakes.messages.rows`. This file has no such helper — it uses the
# `client`/`fake` fixtures above, backed by one FakeClient whose `messages` is
# a flat list filtered by `session_id` (see `list_messages`), and whose
# `sessions` fixture already includes exactly the three cases this endpoint
# needs to distinguish: `v1` (visitor's own, shared, cb-shared), `owner-1`
# (owner's private chat, cb-shared) and `other-1` (shared, but cb-other).
# Adapted to that existing shape rather than inventing a parallel one; no new
# fake infrastructure was needed since `list_messages` already does what
# `MessageStore.transcript` needs.

def test_transcript_replays_a_visitors_own_conversation(client, fake):
    fake.messages.append({"session_id": "v1", "role": "user", "content": "hi",
                          "citations": [], "answered_by_name": None})
    fake.messages.append({"session_id": "v1", "role": "assistant", "content": "hello",
                          "citations": [], "answered_by_name": "Chem tutor"})

    body = client.get("/s/tok/session/v1/messages").json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["content"] == "hello"
    assert body["messages"][1]["answered_by"] == {"id": None, "name": "Chem tutor"}


def test_transcript_refuses_a_session_from_another_chatbot(client):
    """Enumeration guard. A visitor holding one chatbot's token must not be
    able to read a conversation belonging to a different chatbot."""
    assert client.get("/s/tok/session/other-1/messages").status_code == 404


def test_transcript_refuses_the_owners_private_chat(client):
    """The owner's own chats live in this same chatbot. Membership alone is not
    enough — this is the check public_chat already makes, and the reason it
    makes it."""
    assert client.get("/s/tok/session/owner-1/messages").status_code == 404


def test_transcript_is_404_not_403_for_a_session_that_does_not_exist(client):
    """403 would confirm the session exists, which is exactly what an
    enumeration attempt wants to learn."""
    assert client.get("/s/tok/session/nope/messages").status_code == 404


def test_transcript_redacts_filenames_the_live_answer_also_hides(client, fake):
    """The check worth writing first. Rows are stored UNREDACTED — share.py
    redacts the response after answer_turn has written the row — so replaying
    them raw hands back the document names the live path stripped. The same
    answer would be secret when given and public when read back."""
    fake.messages.append({
        "session_id": "v1",
        "role": "assistant",
        "content": "According to Q3-finances.pdf, revenue rose.",
        "citations": [{"source_name": "Q3-finances.pdf", "source_id": "src-1",
                       "text_excerpt": "revenue rose 4%"}],
        "answered_by_name": "Analyst",
    })

    body = client.get("/s/tok/session/v1/messages").json()
    turn = body["messages"][0]

    assert "Q3-finances.pdf" not in turn["content"]
    assert "Q3-finances.pdf" not in str(turn["citations"])
    assert "src-1" not in str(turn["citations"])
    # The excerpt is what makes an answer credible and is deliberately kept.
    assert "revenue rose 4%" in str(turn["citations"])


# --- CORS -------------------------------------------------------------
#
# The widget's loader runs on the HOST page's origin and calls these routes,
# so every request it makes is cross-origin by definition. Without CORS
# headers the browser discards the response even though the server did the
# work — a session row gets created and then orphaned, and the visitor sees
# a blank panel forever.

def test_a_cross_origin_get_is_answered_with_an_allow_origin_header(client, fake):
    fake.messages.append({"session_id": "v1", "role": "user", "content": "hi",
                          "citations": [], "answered_by_name": None})
    res = client.get(
        "/s/tok/session/v1/messages",
        headers={"Origin": "https://example.com"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "*"


def test_a_content_type_preflight_for_chat_is_allowed(client):
    """The widget's own POSTs never send anything but Content-Type, so this
    is the preflight that must succeed for the widget to work at all."""
    res = client.options(
        "/s/tok/chat",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "*"


def test_an_authorization_preflight_is_refused(client):
    """THE test: proves the restriction is load-bearing, not decorative. The
    authenticated (non-share) routes need Authorization; if a cross-origin
    page could preflight it successfully here, this app-wide CORS config
    would just as well let a hostile page attach a stolen bearer token to
    those routes. Starlette answers a disallowed preflight header with 400
    and a body naming the failure — not a 403/404 — so that is what this
    asserts on, rather than guessing at a status code.
    """
    res = client.options(
        "/s/tok/chat",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert res.status_code == 400
    assert "headers" in res.text.lower()
    allowed = res.headers.get("access-control-allow-headers", "")
    assert "authorization" not in allowed.lower()


def _upload(client, session_id):
    return client.post(
        "/s/tok/upload",
        data={"session_id": session_id},
        files={"file": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )


def test_upload_refuses_a_session_from_another_chatbot(client):
    """Same enumeration guard as the transcript route. A visitor holding one
    chatbot's token must not attach a file to a conversation belonging to a
    different chatbot."""
    assert _upload(client, "other-1").status_code == 404


def test_upload_refuses_the_owners_private_chat(client):
    """The owner's own chats live in this same chatbot. Without the `shared`
    check a stranger could attach a document to the owner's private
    conversation — and it would then be retrievable inside it."""
    assert _upload(client, "owner-1").status_code == 404


def test_upload_consumes_the_daily_allowance(client, fake):
    """Extraction and embedding are the expensive half of a share link. A cap
    that bounds messages but leaves uploads unlimited guards the cheap half
    only."""
    before = fake.chatbots["cb-shared"]["share_used_today"]

    _upload(client, "v1")

    assert fake.chatbots["cb-shared"]["share_used_today"] == before + 1


def test_upload_is_refused_once_the_cap_is_spent(client, fake):
    fake.chatbots["cb-shared"]["share_daily_limit"] = 0

    assert _upload(client, "v1").status_code == 429


def test_an_oversized_upload_is_refused_without_spending_the_allowance(
    client, fake, monkeypatch
):
    """A refused file must not cost the visitor one of the day's messages.

    The order matters and is easy to reverse by accident: `share.consume`
    debits the allowance, so reading the file has to be bounded *before* it,
    or a stranger uploading a file too large to accept still burns the
    owner's quota for a document that was never stored.

    `public_upload` reads settings directly rather than through the injected
    dependency, so the limit is patched on the route module — a small limit
    keeps the request body tiny instead of shipping 10 MB through the test.
    """
    real = share_route.get_settings()
    monkeypatch.setattr(
        share_route, "get_settings",
        lambda: SimpleNamespace(
            max_upload_bytes=8,
            poll_interval_seconds=real.poll_interval_seconds,
            ingest_background_max_wait_seconds=real.ingest_background_max_wait_seconds,
        ),
    )
    before = fake.chatbots["cb-shared"]["share_used_today"]

    res = _upload(client, "v1")

    assert res.status_code == 413
    assert "too large" in res.json()["detail"].lower()
    assert fake.chatbots["cb-shared"]["share_used_today"] == before


def test_there_is_no_public_promote_route():
    """The account app can promote a chat upload into the chatbot's permanent
    knowledge. A stranger on someone else's website must never reach that: it
    would write into the owner's knowledge base for every future conversation.
    Asserted against the assembled app, because a route added later would
    otherwise inherit the /s prefix silently."""
    from app.main import create_app

    public = [r.path for r in create_app().routes
              if hasattr(r, "path") and r.path.startswith("/s/")]

    assert not any("promote" in p for p in public), public


def _status(client, session_id, source_id="src-1"):
    return client.get(f"/s/tok/upload/{source_id}?session_id={session_id}")


def test_upload_status_refuses_another_chatbots_session(client):
    assert _status(client, "other-1").status_code == 404


def test_upload_status_refuses_the_owners_private_chat(client):
    assert _status(client, "owner-1").status_code == 404


def test_upload_status_does_not_consume_the_allowance(client, fake):
    """A poll is not work. Charging for it would let a slow extraction spend the
    owner's whole daily cap before the document it is waiting on ever became
    answerable — and the visitor polls once every few seconds."""
    before = fake.chatbots["cb-shared"]["share_used_today"]

    for _ in range(5):
        _status(client, "v1")

    assert fake.chatbots["cb-shared"]["share_used_today"] == before


def test_indexed_but_unrecorded_still_reports_processing(client, fake):
    """The check this endpoint exists for. A source indexed in the shared
    scratch KB is not yet answerable in THIS chat: indexing happens first and
    recording on the session second, and only the recording puts it in
    retrieval scope. Saying "ready" in between tells the visitor a document is
    usable while it still answers "I don't know"."""
    fake.indexed.append(("scratch-kb", "src-9"))
    fake.sessions["v1"].pop("source_ids", None)

    body = _status(client, "v1", "src-9").json()

    assert body["status"] == "processing"


def test_indexed_and_recorded_reports_ready(client, fake):
    fake.indexed.append(("scratch-kb", "src-9"))
    fake.sessions["v1"]["source_ids"] = ["src-9"]

    body = _status(client, "v1", "src-9").json()

    assert body["status"] == "indexed"
