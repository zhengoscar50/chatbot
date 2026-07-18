# backend/tests/unit/test_routes_ingest.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ingest as ingest_route
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def build_app():
    app = FastAPI()
    app.include_router(ingest_route.router)
    return app


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        pass

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


def upload(client):
    return client.post(
        "/ingest/file",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_ingest_file_returns_indexed_status(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 200
    assert response.json() == {"source_id": "src-1", "status": "indexed"}


def test_ingest_file_returns_422_when_attention_required(monkeypatch):
    set_env(monkeypatch)

    class AttentionService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise ingest_route.AttentionRequiredError("src-2")

    monkeypatch.setattr(ingest_route, "IngestService", AttentionService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 422
    assert "src-2" in response.json()["detail"]


def test_ingest_file_returns_202_on_timeout(monkeypatch):
    set_env(monkeypatch)

    class TimeoutService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise ingest_route.IngestTimeoutError("src-3", "pending")

    monkeypatch.setattr(ingest_route, "IngestService", TimeoutService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 202
    assert response.json() == {"source_id": "src-3", "status": "pending"}
