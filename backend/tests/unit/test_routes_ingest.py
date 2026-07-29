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


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        assert kb_id == "kb-1"

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


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


def test_ingest_routes_to_session_kb(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 200
    assert response.json() == {"source_id": "src-1", "status": "indexed"}


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


def test_ingest_returns_422_when_attention_required(monkeypatch):
    set_env(monkeypatch)

    class AttentionService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise ingest_route.AttentionRequiredError("src-2")

    monkeypatch.setattr(ingest_route, "IngestService", AttentionService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 422
    assert "src-2" in response.json()["detail"]


def test_ingest_returns_202_on_timeout(monkeypatch):
    set_env(monkeypatch)

    class TimeoutService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise ingest_route.IngestTimeoutError("src-3", "pending")

    monkeypatch.setattr(ingest_route, "IngestService", TimeoutService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 202
    assert response.json() == {"source_id": "src-3", "status": "pending"}
