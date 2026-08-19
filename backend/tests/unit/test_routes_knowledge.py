import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import knowledge as knowledge_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.chatbot_service import get_chatbot_service

USER = {"id": "u1", "username": "alice", "kb_id": None, "kb_full_id": None}


class FakeChatbotKb:
    def __init__(self):
        self.ensured = []
        self.untrained = []
        self.docs = []

    def kb_ids(self, row):
        return [kb for kb in (row.get("kb_id"), row.get("kb_full_id")) if kb]

    def ensure_kb(self, row, full_document=False):
        self.ensured.append(full_document)
        return "kb-full" if full_document else "kb-chunked"

    def documents(self, row):
        return self.docs

    def untrain(self, row, source_id):
        self.untrained.append(source_id)
        return source_id == "src-known"


class FakeClient:
    def __init__(self, row=None):
        self.row = row if row is not None else dict(USER)
        self.kb_items = {}

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_items.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, item_id):
        self.kb_items[kb_id] = [
            i for i in self.kb_items.get(kb_id, []) if i.get("id") != item_id
        ]

    def create_knowledge_base(self, name, description=None, indexing_config=None,
                               retrieval_config=None):
        return {"id": f"kb-{name}"}

    def update_chatbot_row(self, chatbot_id, fields):
        pass


class FakeChatbots:
    """Stands in for ChatbotService.

    An id not explicitly registered via `add` is reported as owned by
    whoever asks — enough for tests that aren't exercising the ownership
    guard itself. `add` registers a specific id with a specific owner (and
    optional kb columns), so a foreign chatbot can be simulated precisely.
    """

    def __init__(self):
        self.rows = {}
        self._n = 0

    def add(self, owner_id, **fields):
        self._n += 1
        chatbot_id = f"cb-{self._n}"
        row = {"id": chatbot_id, "owner_id": owner_id, "kb_id": None, "kb_full_id": None}
        row.update(fields)
        self.rows[chatbot_id] = row
        return chatbot_id

    def get_owned(self, chatbot_id, owner_id):
        if chatbot_id in self.rows:
            row = self.rows[chatbot_id]
            return row if row["owner_id"] == owner_id else None
        return {"id": chatbot_id, "owner_id": owner_id, "kb_id": None, "kb_full_id": None}


class FakeIngest:
    started = []
    indexed = []
    char_count_value = 500

    def __init__(self, client, kb_id=None, poll_interval=0, max_wait=0):
        self.max_wait = max_wait

    def start(self, filename, content):
        type(self).started.append((filename, self.max_wait))
        return "src-1"

    def await_extraction(self, source_id):
        pass

    def char_count(self, source_id):
        return type(self).char_count_value

    def index_into(self, kb_id, source_id):
        type(self).indexed.append((kb_id, source_id))
        return "indexed"


def build_app(kb=None, client=None, chatbots=None):
    app = FastAPI()
    app.include_router(knowledge_route.router)
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    app.dependency_overrides[get_chatbot_kb_service] = lambda: kb or FakeChatbotKb()
    app.dependency_overrides[get_powabase_client] = lambda: client or FakeClient()
    app.dependency_overrides[get_chatbot_service] = lambda: chatbots or FakeChatbots()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        poll_interval_seconds=0.01,
        ingest_background_max_wait_seconds=600,
        full_document_max_chars=120000,
    )
    return app


def upload(client_, chatbot_id="cb-1"):
    return client_.post(
        "/knowledge/train",
        data={"chatbot_id": chatbot_id},
        files={"file": ("note.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_training_returns_202_and_uses_the_long_budget(monkeypatch):
    """Backgrounded like agent training: a large PDF takes minutes to extract,
    so a blocking request with the 60s foreground budget could never finish."""
    monkeypatch.setattr(knowledge_route, "IngestService", FakeIngest)
    FakeIngest.started, FakeIngest.indexed = [], []

    r = upload(TestClient(build_app()))

    assert r.status_code == 202
    assert r.json() == {"source_id": "src-1", "status": "processing"}
    assert FakeIngest.started[0][1] == 600


def test_a_short_document_goes_to_the_whole_document_tier(monkeypatch):
    monkeypatch.setattr(knowledge_route, "IngestService", FakeIngest)
    FakeIngest.started, FakeIngest.indexed = [], []
    FakeIngest.char_count_value = 500
    kb = FakeChatbotKb()

    upload(TestClient(build_app(kb)))

    assert kb.ensured == [True]
    assert FakeIngest.indexed == [("kb-full", "src-1")]


def test_a_long_document_goes_to_the_chunked_tier(monkeypatch):
    monkeypatch.setattr(knowledge_route, "IngestService", FakeIngest)
    FakeIngest.started, FakeIngest.indexed = [], []
    FakeIngest.char_count_value = 500_000
    kb = FakeChatbotKb()

    upload(TestClient(build_app(kb)))

    assert kb.ensured == [False]
    assert FakeIngest.indexed == [("kb-chunked", "src-1")]
    FakeIngest.char_count_value = 500


def test_status_rereads_the_chatbot_row(monkeypatch):
    """The tier is created inside the background task, so the row carried by
    the request predates it. get_owned re-reads it, so this must see the
    freshly created tier rather than reporting the document missing forever."""
    monkeypatch.setattr(knowledge_route, "source_status",
                        lambda client, sid, kb_ids: ("indexed" if kb_ids else "processing", None))
    chatbots = FakeChatbots()
    chatbot_id = chatbots.add(owner_id=USER["id"], kb_full_id="kb-full")

    r = TestClient(build_app(chatbots=chatbots)).get(
        f"/knowledge/documents/src-1/status?chatbot_id={chatbot_id}"
    )

    assert r.json()["status"] == "indexed"


def test_documents_lists_what_the_chatbot_trained():
    kb = FakeChatbotKb()
    kb.docs = [{"source_id": "s1", "filename": "note.pdf", "status": "indexed"}]

    r = TestClient(build_app(kb)).get("/knowledge/documents?chatbot_id=cb-1")

    assert r.status_code == 200
    assert r.json()[0]["filename"] == "note.pdf"


def test_untrain_removes_a_document():
    kb = FakeChatbotKb()
    r = TestClient(build_app(kb)).delete("/knowledge/documents/src-known?chatbot_id=cb-1")
    assert r.status_code == 204
    assert kb.untrained == ["src-known"]


def test_untrain_404_for_a_document_the_chatbot_does_not_have():
    r = TestClient(build_app()).delete("/knowledge/documents/not-mine?chatbot_id=cb-1")
    assert r.status_code == 404


# --- ownership scoping --------------------------------------------------

@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def chatbots():
    return FakeChatbots()


@pytest.fixture
def client(fake, chatbots):
    return TestClient(build_app(kb=ChatbotKbService(fake), client=fake, chatbots=chatbots))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def my_chatbot(chatbots):
    return chatbots.add(owner_id=USER["id"], kb_id="cb-kb")


@pytest.fixture
def other_chatbot(chatbots):
    return chatbots.add(owner_id="someone-else")


def test_listing_documents_requires_a_chatbot_id(client, auth):
    assert client.get("/knowledge/documents", headers=auth).status_code == 422


def test_listing_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.get(
        f"/knowledge/documents?chatbot_id={other_chatbot}", headers=auth
    )
    # 404 not 403: a foreign id must be indistinguishable from a missing one.
    assert res.status_code == 404


def test_documents_come_from_the_named_chatbot(client, auth, my_chatbot, fake):
    fake.kb_items["cb-kb"] = [
        {"id": "i1", "source_id": "s1", "source_name": "a.pdf", "index_status": "indexed"}
    ]
    res = client.get(f"/knowledge/documents?chatbot_id={my_chatbot}", headers=auth)
    assert res.status_code == 200
    assert [d["source_id"] for d in res.json()] == ["s1"]


def test_two_chatbots_owned_by_the_same_user_see_only_their_own_documents(
    client, auth, chatbots, fake
):
    cb_a = chatbots.add(owner_id=USER["id"], kb_id="kb-a")
    cb_b = chatbots.add(owner_id=USER["id"], kb_id="kb-b")
    fake.kb_items["kb-a"] = [
        {"id": "i1", "source_id": "s-a", "source_name": "a.pdf", "index_status": "indexed"}
    ]
    fake.kb_items["kb-b"] = [
        {"id": "i2", "source_id": "s-b", "source_name": "b.pdf", "index_status": "indexed"}
    ]

    res = client.get(f"/knowledge/documents?chatbot_id={cb_a}", headers=auth)

    assert res.status_code == 200
    assert [d["source_id"] for d in res.json()] == ["s-a"]


def test_untraining_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.delete(
        f"/knowledge/documents/s1?chatbot_id={other_chatbot}", headers=auth
    )
    assert res.status_code == 404


def test_training_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.post(
        "/knowledge/train",
        data={"chatbot_id": other_chatbot},
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth,
    )
    assert res.status_code == 404
