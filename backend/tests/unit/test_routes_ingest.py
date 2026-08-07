# backend/tests/unit/test_routes_ingest.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import ingest as ingest_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.session_service import get_session_service


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


class FakeSessionService:
    def get_owned_session(self, session_id, owner_id):
        return None if session_id == "missing" else {"id": session_id, "kb_id": "kb-1"}

    def ensure_kb(self, row, full_document=False):
        return row["kb_id"]


class FakeIngestService:
    def __init__(self, client, poll_interval=0, max_wait=0):
        pass

    def start(self, filename, content):
        return "src-1"

    def await_extraction(self, source_id):
        pass

    char_count_value = 100  # class attr the test tweaks for small/large

    def char_count(self, source_id):
        return type(self).char_count_value

    def index_into(self, kb_id, source_id):
        return "indexed"


def build_app():
    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def upload(client, session_id="s1"):
    return client.post(
        "/ingest/file",
        data={"session_id": session_id},
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_ingest_returns_202_processing(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 202
    assert response.json() == {"source_id": "src-1", "status": "processing"}


def test_ingest_requires_session_id(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = TestClient(build_app()).post(
        "/ingest/file",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )

    assert response.status_code == 422


def test_ingest_404_for_missing_session(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()), session_id="missing")

    assert response.status_code == 404


def test_ingest_404_for_non_owned_session(monkeypatch):
    # get_owned_session returns None for a session that exists but isn't
    # owned by the current user -> 404 (indistinguishable from missing).
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    class NonOwnerService(FakeSessionService):
        def get_owned_session(self, session_id, owner_id):
            return None

    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: NonOwnerService()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    response = upload(TestClient(app), session_id="not-mine")

    assert response.status_code == 404


class RoutingSessionService:
    def __init__(self):
        self.calls = []

    def get_owned_session(self, session_id, owner_id):
        return {"id": session_id, "kb_id": ""}

    def ensure_kb(self, row):
        # No full_document parameter: the chat scratch KB is chunk-only.
        self.calls.append(row["id"])
        return "kb-scratch"


def build_routing_app(svc):
    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: svc
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def test_chat_upload_always_uses_the_chunk_only_scratch_kb(monkeypatch):
    # Content-aware routing moved to the agent's permanent tier
    # (POST /agents/{id}/train). Scratch uploads are throwaway context for one
    # conversation, so a small document is no longer routed to a full_document
    # KB here — ensure_kb takes no full_document argument at all.
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)
    FakeIngestService.char_count_value = 100  # small: would once have gone full_document
    svc = RoutingSessionService()

    response = TestClient(build_routing_app(svc)).post(
        "/ingest/file",
        data={"session_id": "s1"},
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )

    assert response.status_code == 202
    assert svc.calls == ["s1"]


def test_status_reports_indexed(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "source_status", lambda client, sid, kb_ids: ("indexed", None))

    class SS:
        def get_owned_session(self, session_id, owner_id):
            return {"id": session_id, "kb_id": "kb-1"}

    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: SS()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    r = TestClient(app).get("/ingest/status/src-1?session_id=s1")

    assert r.status_code == 200
    assert r.json()["status"] == "indexed"


def test_status_404_for_non_owner(monkeypatch):
    set_env(monkeypatch)

    class SS:
        def get_owned_session(self, session_id, owner_id):
            return None

    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: SS()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}

    response = TestClient(app).get("/ingest/status/src-1?session_id=s1")

    assert response.status_code == 404
