# backend/tests/unit/test_routes_research.py
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import research as research_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.general_kb import get_general_kb_id
from app.services.research_pipeline import get_research_orchestration_id
from app.services.session_service import get_session_service


class FakeSessionService:
    def __init__(self):
        self.row = {"id": "s1", "owner_id": "o1", "kb_id": "kb-s", "kb_full_id": None}

    def get_owned_session(self, session_id, owner_id):
        if session_id == "missing":
            return None
        if owner_id != self.row["owner_id"]:
            return None
        return self.row


class FakeClient:
    def __init__(self, handler=None):
        self.handler = handler or {
            "formatted_context": "some context",
            "retrieved_context": [{"source_name": "doc-a"}, {"source_name": "doc-a"}, {"source_id": "src-b"}],
        }
        self.calls = []

    def create_context_handler(self, query, knowledge_bases, max_context_tokens=None):
        self.calls.append((query, knowledge_bases, max_context_tokens))
        return self.handler


def build_app(session_service=None, client=None):
    app = FastAPI()
    app.state.research_jobs = {}
    app.include_router(research_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: (client or FakeClient())
    app.dependency_overrides[get_session_service] = lambda: (session_service or FakeSessionService())
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    app.dependency_overrides[get_research_orchestration_id] = lambda: "orch-1"
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        research_top_k=12, research_max_context_tokens=24000,
        research_max_concurrent_per_user=2, research_job_ttl_seconds=1800,
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    return app


def test_start_research_404_for_unknown_session(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()

    response = TestClient(app).post("/research", json={"session_id": "missing", "query": "q"})

    assert response.status_code == 404


def test_start_research_404_for_non_owned_session(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    svc = FakeSessionService()
    svc.row["owner_id"] = "someone-else"
    app = build_app(session_service=svc)

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert response.status_code == 404


def test_start_research_202_registers_running_job(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    job_id = body["job_id"]
    assert job_id

    job = app.state.research_jobs[job_id]
    assert job["owner"] == "o1"
    assert job["status"] == "running"


def test_start_research_deduplicates_citations_from_handler(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    client = FakeClient()
    app = build_app(client=client)

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    job_id = response.json()["job_id"]
    job = app.state.research_jobs[job_id]
    assert job["citations"] == ["doc-a", "src-b"]


def test_start_research_retrieves_broadly_across_all_three_kbs(monkeypatch):
    # The defining behavior of Deep Research: retrieve from the session's chunk
    # KB, its full-document KB and the shared general KB at once, at the wider
    # research top_k / token budget — not the narrower chat-time retrieval.
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    svc = FakeSessionService()
    svc.row["kb_full_id"] = "kb-f"
    client = FakeClient()
    app = build_app(session_service=svc, client=client)

    TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert client.calls == [(
        "q",
        [{"id": "kb-s", "top_k": 12}, {"id": "kb-f", "top_k": 12}, {"id": "gkb-1", "top_k": 12}],
        24000,
    )]


def test_start_research_skips_missing_knowledge_bases(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    client = FakeClient()  # FakeSessionService has kb_full_id None
    app = build_app(client=client)

    TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    _query, kbs, _tokens = client.calls[0]
    assert [kb["id"] for kb in kbs] == ["kb-s", "gkb-1"]


def test_start_research_429_over_per_user_concurrency_cap(monkeypatch):
    # run_research is stubbed out, so jobs stay "running" — the same state a
    # real in-flight run is in while it holds a threadpool thread.
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    client = TestClient(app)

    assert client.post("/research", json={"session_id": "s1", "query": "q"}).status_code == 202
    assert client.post("/research", json={"session_id": "s1", "query": "q"}).status_code == 202
    third = client.post("/research", json={"session_id": "s1", "query": "q"})

    assert third.status_code == 429
    assert len(app.state.research_jobs) == 2


def test_start_research_cap_counts_only_the_requesting_user(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    app.state.research_jobs["other-1"] = {
        "status": "running", "owner": "someone-else", "created_at": time.monotonic(),
    }
    app.state.research_jobs["other-2"] = {
        "status": "running", "owner": "someone-else", "created_at": time.monotonic(),
    }

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert response.status_code == 202


def test_start_research_cap_ignores_finished_jobs(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    for i, status in enumerate(("done", "failed")):
        app.state.research_jobs[f"old-{i}"] = {
            "status": status, "owner": "o1", "created_at": time.monotonic(),
        }

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert response.status_code == 202


def test_start_research_evicts_jobs_older_than_the_ttl(monkeypatch):
    # Without eviction the in-memory store grows for the process lifetime,
    # each entry holding a full report.
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    app.state.research_jobs["stale"] = {
        "status": "done", "owner": "o1", "report": "old report",
        "created_at": time.monotonic() - 1801,
    }
    fresh_at = time.monotonic()
    app.state.research_jobs["fresh"] = {"status": "done", "owner": "o1", "created_at": fresh_at}

    TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert "stale" not in app.state.research_jobs
    assert "fresh" in app.state.research_jobs


def test_start_research_eviction_frees_a_slot_held_by_a_hung_job(monkeypatch):
    # A run whose stream never reaches a terminal event would otherwise occupy
    # a concurrency slot forever.
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    for i in range(2):
        app.state.research_jobs[f"hung-{i}"] = {
            "status": "running", "owner": "o1", "created_at": time.monotonic() - 1801,
        }

    response = TestClient(app).post("/research", json={"session_id": "s1", "query": "q"})

    assert response.status_code == 202


def test_research_status_200_for_own_job(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    app.state.research_jobs["j1"] = {
        "status": "done", "stage": "Writing", "report": "the report",
        "citations": ["a"], "detail": None, "owner": "o1",
    }

    response = TestClient(app).get("/research/status/j1")

    assert response.status_code == 200
    assert response.json() == {
        "status": "done", "stage": "Writing", "report": "the report",
        "citations": ["a"], "detail": None,
    }


def test_research_status_404_for_foreign_job(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()
    app.state.research_jobs["j1"] = {
        "status": "running", "stage": "Researching", "report": None,
        "citations": [], "detail": None, "owner": "someone-else",
    }

    response = TestClient(app).get("/research/status/j1")

    assert response.status_code == 404


def test_research_status_404_for_unknown_job(monkeypatch):
    monkeypatch.setattr(research_route, "run_research", lambda *a, **k: None)
    app = build_app()

    response = TestClient(app).get("/research/status/unknown")

    assert response.status_code == 404
