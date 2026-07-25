import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import admin as admin_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.general_kb import get_general_kb_id


def set_admin(monkeypatch, password="s3cret"):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    if password is None:
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("ADMIN_PASSWORD", password)
    get_settings.cache_clear()


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        assert kb_id == "gkb-1"  # trains into the GENERAL KB

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


def build_app():
    app = FastAPI()
    app.include_router(admin_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    return app


def train(client, password="s3cret"):
    return client.post(
        "/admin/train",
        data={"password": password},
        files={"file": ("g.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_verify_ok(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "s3cret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_verify_wrong_password(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "nope"})
    assert r.status_code == 401


def test_verify_not_configured(monkeypatch):
    set_admin(monkeypatch, password=None)
    r = TestClient(build_app()).post("/admin/verify", json={"password": "anything"})
    assert r.status_code == 403


def test_train_ingests_into_general_kb(monkeypatch):
    set_admin(monkeypatch)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()))
    assert r.status_code == 200
    assert r.json() == {"source_id": "src-1", "status": "indexed"}


def test_train_rejects_wrong_password(monkeypatch):
    set_admin(monkeypatch)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()), password="nope")
    assert r.status_code == 401


def test_train_403_when_not_configured(monkeypatch):
    set_admin(monkeypatch, password=None)
    monkeypatch.setattr(admin_route, "IngestService", FakeIngestService)
    r = train(TestClient(build_app()))
    assert r.status_code == 403


def test_train_202_on_timeout(monkeypatch):
    set_admin(monkeypatch)

    class TimeoutService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise admin_route.IngestTimeoutError("src-3", "pending")

    monkeypatch.setattr(admin_route, "IngestService", TimeoutService)
    r = train(TestClient(build_app()))
    assert r.status_code == 202
    assert r.json() == {"source_id": "src-3", "status": "pending"}


def test_admin_page_served(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_app()).get("/admin")
    assert r.status_code == 200
