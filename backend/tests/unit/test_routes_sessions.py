from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import sessions as sessions_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.agent_service import get_agent_service
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.chatbot_service import ChatbotService, get_chatbot_service
from app.services.session_service import SessionService, get_session_service


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


# --- promote: moving a chat upload into chatbot knowledge -----------------

class PromoteFakeClient:
    """Backs SessionService, ChatbotService and ChatbotKbService at once, so
    a single fixture can drive the whole promote route through real service
    objects rather than stubbing the route's own logic.

    Instance attributes only — never shared class or module state.
    """

    def __init__(self):
        self.sessions = {}
        self.chatbots = {}
        self.kb_items = {}
        self.updated = []
        self.indexed = []
        self.char_counts = {}

    # --- SessionService ---------------------------------------------------
    def get_session_row(self, session_id):
        return self.sessions.get(session_id)

    def update_session(self, session_id, fields):
        self.updated.append((session_id, fields))
        if session_id in self.sessions:
            self.sessions[session_id].update(fields)

    # --- ChatbotService -----------------------------------------------
    def get_chatbot_row(self, chatbot_id):
        return self.chatbots.get(chatbot_id)

    # --- ChatbotKbService ---------------------------------------------
    def create_knowledge_base(self, name, description="", indexing_config=None,
                               retrieval_config=None):
        return {"id": f"kb-{name}"}

    def update_chatbot_row(self, chatbot_id, fields):
        if chatbot_id in self.chatbots:
            self.chatbots[chatbot_id].update(fields)

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_items.get(kb_id, [])}


class FakePromoteIngestService:
    """Stands in for IngestService, forwarding into the same fake client so
    `fake.indexed` proves whether promotion re-indexed a document.

    Well above full_document_max_chars by default, so ensure_kb lands on the
    ordinary (chunked) tier rather than the whole-document one — matching
    whichever tier a test pre-seeds on the chatbot row.
    """

    def __init__(self, client, kb_id=None, poll_interval=0, max_wait=0):
        self.client = client

    def char_count(self, source_id):
        return self.client.char_counts.get(source_id, 500_000)

    def index_into(self, kb_id, source_id):
        self.client.indexed.append((kb_id, source_id))
        return "indexed"


@pytest.fixture
def fake():
    return PromoteFakeClient()


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.setattr(sessions_route, "IngestService", FakePromoteIngestService)
    fake.sessions["s1"] = {
        "id": "s1", "owner_id": "o1", "chatbot_id": "cb-1", "source_ids": [],
    }
    fake.chatbots["cb-1"] = {
        "id": "cb-1", "owner_id": "o1", "kb_id": None, "kb_full_id": None,
    }

    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_session_service] = lambda: SessionService(
        fake, None, "scratch"
    )
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(fake)
    app.dependency_overrides[get_chatbot_kb_service] = lambda: ChatbotKbService(fake)
    app.dependency_overrides[get_powabase_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        poll_interval_seconds=0.01,
        ingest_background_max_wait_seconds=600,
        full_document_max_chars=120000,
    )
    return TestClient(app)


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def foreign_session(fake):
    fake.sessions["s-foreign"] = {
        "id": "s-foreign", "owner_id": "someone-else", "chatbot_id": "cb-1",
        "source_ids": ["src-1"],
    }
    return "s-foreign"


def test_promote_moves_the_document_into_chatbot_knowledge(client, auth, fake):
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    res = client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    assert res.status_code == 202
    assert "src-1" not in fake.sessions["s1"]["source_ids"]
    assert fake.indexed == [("kb-chatbot-cb-1-knowledge", "src-1")]


def test_promote_on_a_chat_with_no_chatbot_is_not_found(client, auth, fake):
    """A chat created between migration 011 and the deploy that stamped it has
    a null chatbot_id. Promoting there must refuse, not push the document into
    a chatbot that does not exist — and must leave the chat untouched."""
    fake.sessions["s1"]["chatbot_id"] = None
    fake.sessions["s1"]["source_ids"] = ["src-1"]

    res = client.post("/sessions/s1/documents/src-1/promote", headers=auth)

    assert res.status_code == 404
    assert res.json()["detail"] == "Chatbot not found"
    assert fake.sessions["s1"]["source_ids"] == ["src-1"]
    assert fake.indexed == []


def test_promote_into_a_chatbot_owned_by_someone_else_is_not_found(client, auth, fake):
    fake.chatbots["cb-theirs"] = {
        "id": "cb-theirs", "owner_id": "someone-else", "kb_id": None, "kb_full_id": None,
    }
    fake.sessions["s1"]["chatbot_id"] = "cb-theirs"
    fake.sessions["s1"]["source_ids"] = ["src-1"]

    res = client.post("/sessions/s1/documents/src-1/promote", headers=auth)

    assert res.status_code == 404
    assert res.json()["detail"] == "Chatbot not found"
    assert fake.indexed == []


def test_promote_rejects_a_source_this_chat_never_uploaded(client, auth, fake):
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    res = client.post("/sessions/s1/documents/other/promote", headers=auth)
    assert res.status_code == 404


def test_promote_on_another_users_chat_is_not_found(client, auth, foreign_session):
    res = client.post(
        f"/sessions/{foreign_session}/documents/src-1/promote", headers=auth
    )
    assert res.status_code == 404


def test_re_promoting_after_the_move_reports_no_such_document(client, auth, fake):
    # The move already happened, so the chat no longer names the source. This
    # is the same 404 as any unknown document and needs no special case.
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    second = client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    assert second.status_code == 404


def test_promoting_a_document_the_chatbot_already_holds_succeeds(client, auth, fake):
    # Re-uploading the same file gives the SAME source id, because Powabase
    # deduplicates identical content. Promoting it again must not re-index and
    # must not fail — it moves out of the chat and stops there.
    fake.chatbots["cb-1"]["kb_id"] = "cb-kb"
    fake.kb_items["cb-kb"] = [{"id": "i1", "source_id": "src-1"}]
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    res = client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    assert res.status_code == 202
    assert "src-1" not in fake.sessions["s1"]["source_ids"]
    assert fake.indexed == []   # nothing re-indexed
