import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import knowledge as knowledge_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.chatbot_kb import get_chatbot_kb_service

USER = {"id": "u1", "username": "alice", "kb_id": None, "kb_full_id": None}


class FakeUserKb:
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

    def get_user(self, user_id):
        return self.row


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


def build_app(kb=None, client=None):
    app = FastAPI()
    app.include_router(knowledge_route.router)
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    app.dependency_overrides[get_chatbot_kb_service] = lambda: kb or FakeUserKb()
    app.dependency_overrides[get_powabase_client] = lambda: client or FakeClient()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        poll_interval_seconds=0.01,
        ingest_background_max_wait_seconds=600,
        full_document_max_chars=120000,
    )
    return app


def upload(client_):
    return client_.post(
        "/knowledge/train",
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
    kb = FakeUserKb()

    upload(TestClient(build_app(kb)))

    assert kb.ensured == [True]
    assert FakeIngest.indexed == [("kb-full", "src-1")]


def test_a_long_document_goes_to_the_chunked_tier(monkeypatch):
    monkeypatch.setattr(knowledge_route, "IngestService", FakeIngest)
    FakeIngest.started, FakeIngest.indexed = [], []
    FakeIngest.char_count_value = 500_000
    kb = FakeUserKb()

    upload(TestClient(build_app(kb)))

    assert kb.ensured == [False]
    assert FakeIngest.indexed == [("kb-chunked", "src-1")]
    FakeIngest.char_count_value = 500


def test_status_rereads_the_user_row(monkeypatch):
    """The tier is created inside the background task, so the row carried by
    the request predates it. Reading the stale copy would report a document
    as missing forever."""
    monkeypatch.setattr(knowledge_route, "source_status",
                        lambda client, sid, kb_ids: ("indexed" if kb_ids else "processing", None))
    fresh = dict(USER, kb_full_id="kb-full")   # created after the request began

    r = TestClient(build_app(client=FakeClient(fresh))).get(
        "/knowledge/documents/src-1/status"
    )

    assert r.json()["status"] == "indexed"


def test_documents_lists_what_the_user_trained():
    kb = FakeUserKb()
    kb.docs = [{"source_id": "s1", "filename": "note.pdf", "status": "indexed"}]

    r = TestClient(build_app(kb)).get("/knowledge/documents")

    assert r.status_code == 200
    assert r.json()[0]["filename"] == "note.pdf"


def test_untrain_removes_a_document():
    kb = FakeUserKb()
    r = TestClient(build_app(kb)).delete("/knowledge/documents/src-known")
    assert r.status_code == 204
    assert kb.untrained == ["src-known"]


def test_untrain_404_for_a_document_the_user_does_not_have():
    r = TestClient(build_app()).delete("/knowledge/documents/not-mine")
    assert r.status_code == 404
