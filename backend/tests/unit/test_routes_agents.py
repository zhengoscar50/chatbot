from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import agents as agents_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.agent_service import get_agent_service


class FakeAgentService:
    def __init__(self):
        self.rows = {}
        self.deleted = []
        self.trained_full = []
        self._n = 0

    def create(self, owner_id, name, instructions, description, model, grounding, use_general_kb):
        self._n += 1
        row = {
            "id": f"ag-{self._n}", "owner_id": owner_id, "name": name,
            "instructions": instructions, "description": description,
            "model": model, "grounding": grounding,
            "use_general_kb": use_general_kb, "powabase_agent_id": "pa-1",
            "kb_id": None, "kb_full_id": None, "updated_at": "2026-08-06T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def list(self, owner_id):
        return [r for r in self.rows.values() if r["owner_id"] == owner_id]

    def get_owned(self, agent_id, owner_id):
        row = self.rows.get(agent_id)
        return row if row and row["owner_id"] == owner_id else None

    def update(self, row, fields):
        row.update(fields)
        return row

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        return self.rows.pop(agent_id, None) is not None

    def ensure_kb(self, row, full_document=False):
        self.trained_full.append(full_document)
        column = "kb_full_id" if full_document else "kb_id"
        row[column] = f"kb-{column}"
        return row[column]


class FakeIngestClient:
    def __init__(self):
        self.kb_sources = {}
        self.removed = []

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_sources.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, source_id):
        self.removed.append((kb_id, source_id))


def build_app(service=None, client=None):
    app = FastAPI()
    app.include_router(agents_route.router)
    svc = service or FakeAgentService()
    app.dependency_overrides[get_agent_service] = lambda: svc
    app.dependency_overrides[get_powabase_client] = lambda: (client or FakeIngestClient())
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        default_agent_model="gpt-4o-mini",
        poll_interval_seconds=0.01,
        ingest_max_wait_seconds=1,
        ingest_background_max_wait_seconds=600,
        full_document_max_chars=120000,
    )
    return app


# --- CRUD -------------------------------------------------------------------

def test_create_agent_returns_the_configured_agent():
    app = build_app()
    r = TestClient(app).post("/agents", json={
        "name": "Tutor", "instructions": "Be terse.",
        "grounding": "open", "use_general_kb": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Tutor"
    assert body["grounding"] == "open"
    assert body["use_general_kb"] is True
    assert body["trained"] is False


def test_create_agent_falls_back_to_the_default_model():
    app = build_app()
    assert TestClient(app).post("/agents", json={"name": "T"}).json()["model"] == "gpt-4o-mini"


def test_create_agent_rejects_unknown_grounding():
    app = build_app()
    r = TestClient(app).post("/agents", json={"name": "T", "grounding": "sideways"})
    assert r.status_code == 422


def test_create_agent_rejects_empty_name():
    app = build_app()
    assert TestClient(app).post("/agents", json={"name": ""}).status_code == 422


def test_list_agents_returns_only_mine():
    svc = FakeAgentService()
    svc.create("o1", "Mine", "", "", "m", "strict", False)
    svc.create("other", "Theirs", "", "", "m", "strict", False)
    app = build_app(svc)

    assert [a["name"] for a in TestClient(app).get("/agents").json()] == ["Mine"]


def test_trained_flag_is_true_once_a_kb_exists():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    row["kb_id"] = "kb-1"
    app = build_app(svc)

    assert TestClient(app).get("/agents").json()[0]["trained"] is True


def test_get_agent_404_for_unknown_id():
    app = build_app()
    assert TestClient(app).get("/agents/nope").status_code == 404


def test_patch_updates_fields():
    svc = FakeAgentService()
    svc.create("o1", "Old", "", "", "m", "strict", False)
    app = build_app(svc)

    r = TestClient(app).patch("/agents/ag-1", json={"name": "New", "grounding": "open"})

    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert r.json()["grounding"] == "open"


def test_patch_ignores_unset_fields():
    svc = FakeAgentService()
    svc.create("o1", "Keep", "Keep me.", "", "m", "strict", False)
    app = build_app(svc)

    TestClient(app).patch("/agents/ag-1", json={"name": "Renamed"})

    assert svc.rows["ag-1"]["instructions"] == "Keep me."


def test_patch_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "Theirs", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).patch("/agents/ag-1", json={"name": "x"}).status_code == 404


def test_delete_agent_204_and_cascades():
    svc = FakeAgentService()
    svc.create("o1", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).delete("/agents/ag-1").status_code == 204
    assert svc.deleted == ["ag-1"]


def test_delete_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).delete("/agents/ag-1").status_code == 404
    assert svc.deleted == []


# --- Training ---------------------------------------------------------------

def test_train_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    r = TestClient(app).post(
        "/agents/ag-1/train", files={"file": ("d.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert r.status_code == 404


class FakeTrainIngest:
    """Stands in for IngestService during a training request."""
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


def test_training_returns_202_immediately(monkeypatch):
    """Training is backgrounded like a chat upload.

    It used to block the request while extracting, capped at 60s, so a large
    document could not be trained at all: extraction takes minutes, and the
    request 504'd (or died on a transient upstream blip) first.
    """
    monkeypatch.setattr(agents_route, "IngestService", FakeTrainIngest)
    FakeTrainIngest.started, FakeTrainIngest.indexed = [], []
    svc = FakeAgentService()
    svc.create("o1", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    r = TestClient(app).post(
        "/agents/ag-1/train", files={"file": ("big.pdf", b"%PDF-1.4", "application/pdf")}
    )

    assert r.status_code == 202
    assert r.json() == {"source_id": "src-1", "status": "processing"}
    # The long budget, not the 60s foreground one.
    assert FakeTrainIngest.started[0][1] == 600


def test_training_routes_a_small_document_to_the_whole_document_tier(monkeypatch):
    monkeypatch.setattr(agents_route, "IngestService", FakeTrainIngest)
    FakeTrainIngest.started, FakeTrainIngest.indexed = [], []
    FakeTrainIngest.char_count_value = 500          # small
    svc = FakeAgentService()
    svc.create("o1", "T", "", "", "m", "strict", False)

    TestClient(build_app(svc)).post(
        "/agents/ag-1/train", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")}
    )

    assert svc.trained_full == [True]
    assert FakeTrainIngest.indexed == [("kb-kb_full_id", "src-1")]


def test_training_routes_a_large_document_to_the_chunked_tier(monkeypatch):
    monkeypatch.setattr(agents_route, "IngestService", FakeTrainIngest)
    FakeTrainIngest.started, FakeTrainIngest.indexed = [], []
    FakeTrainIngest.char_count_value = 500_000      # past full_document_max_chars
    svc = FakeAgentService()
    svc.create("o1", "T", "", "", "m", "strict", False)

    TestClient(build_app(svc)).post(
        "/agents/ag-1/train", files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")}
    )

    assert svc.trained_full == [False]
    assert FakeTrainIngest.indexed == [("kb-kb_id", "src-1")]
    FakeTrainIngest.char_count_value = 500


def test_documents_lists_both_permanent_kbs():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    row["kb_id"], row["kb_full_id"] = "kb-c", "kb-f"
    client = FakeIngestClient()
    # Real Powabase shape: id is the indexed-source link, and the fields are
    # source_name / index_status (not filename / status).
    client.kb_sources = {
        "kb-c": [{"id": "ix-1", "source_id": "s1", "source_name": "big.pdf",
                  "index_status": "indexed"}],
        "kb-f": [{"id": "ix-2", "source_id": "s2", "source_name": "small.pdf",
                  "index_status": "indexed"}],
    }
    app = build_app(svc, client)

    body = TestClient(app).get("/agents/ag-1/documents").json()

    assert {d["source_id"] for d in body} == {"s1", "s2"}
    assert {d["filename"] for d in body} == {"big.pdf", "small.pdf"}
    assert {d["status"] for d in body} == {"indexed"}


def test_documents_empty_for_untrained_agent():
    svc = FakeAgentService()
    svc.create("o1", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).get("/agents/ag-1/documents").json() == []


def test_documents_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).get("/agents/ag-1/documents").status_code == 404


def test_untrain_unlinks_from_the_kb_that_holds_it():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    row["kb_id"], row["kb_full_id"] = "kb-c", "kb-f"
    client = FakeIngestClient()
    client.kb_sources = {"kb-f": [{"id": "ix-2", "source_id": "s2"}]}
    app = build_app(svc, client)

    r = TestClient(app).delete("/agents/ag-1/documents/s2")

    assert r.status_code == 204
    # Unlinked by the INDEXED-SOURCE id, not the source_id: Powabase 404s
    # with "Indexed source not found" if given the latter. And the Source
    # itself is never deleted, since it may be shared with other agents.
    assert client.removed == [("kb-f", "ix-2")]


def test_untrain_404_when_the_agent_does_not_hold_that_document():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "", "m", "strict", False)
    row["kb_id"] = "kb-c"
    app = build_app(svc, FakeIngestClient())

    assert TestClient(app).delete("/agents/ag-1/documents/nope").status_code == 404


def test_untrain_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).delete("/agents/ag-1/documents/s1").status_code == 404


def test_list_agents_502_when_powabase_is_unreachable():
    # A missing agents table (or any client error) must surface as a clean 502,
    # not an unhandled 500 — the frontend shows this string to the user.
    from app.clients.powabase_client import PowabaseAPIError

    class Failing(FakeAgentService):
        def list(self, owner_id):
            raise PowabaseAPIError(404, {"code": "PGRST205"})

    app = build_app(Failing())
    assert TestClient(app, raise_server_exceptions=False).get("/agents").status_code == 502


def test_get_agent_502_when_powabase_is_unreachable():
    from app.clients.powabase_client import PowabaseAPIError

    class Failing(FakeAgentService):
        def get_owned(self, agent_id, owner_id):
            raise PowabaseAPIError(404, {"code": "PGRST205"})

    app = build_app(Failing())
    r = TestClient(app, raise_server_exceptions=False).get("/agents/ag-1")
    assert r.status_code == 502


def test_create_agent_400_for_a_model_the_provider_refuses():
    from app.services.agent_service import ModelRejectedError

    class Refusing(FakeAgentService):
        def create(self, *a, **k):
            raise ModelRejectedError("not-a-real-model", "unknown model")

    app = build_app(Refusing())
    r = TestClient(app).post("/agents", json={"name": "T", "model": "not-a-real-model"})

    assert r.status_code == 400
    assert "not-a-real-model" in r.json()["detail"]


def test_patch_agent_400_for_a_model_the_provider_refuses():
    from app.services.agent_service import ModelRejectedError

    class Refusing(FakeAgentService):
        def update(self, row, fields):
            raise ModelRejectedError("bad", "unknown model")

    svc = Refusing()
    svc.create("o1", "T", "", "", "m", "strict", False)
    app = build_app(svc)

    r = TestClient(app).patch("/agents/ag-1", json={"model": "bad"})

    assert r.status_code == 400
    assert "unchanged" in r.json()["detail"]


def test_create_agent_accepts_and_returns_a_description():
    app = build_app()
    r = TestClient(app).post("/agents", json={
        "name": "Tutor", "description": "Answers AP Chemistry questions.",
    })
    assert r.status_code == 201
    assert r.json()["description"] == "Answers AP Chemistry questions."


def test_agent_description_defaults_to_empty():
    app = build_app()
    assert TestClient(app).post("/agents", json={"name": "T"}).json()["description"] == ""


def test_list_agents_includes_descriptions_for_routing():
    svc = FakeAgentService()
    svc.create("o1", "T", "", "Answers chemistry questions.", "m", "strict", False)
    app = build_app(svc)
    assert TestClient(app).get("/agents").json()[0]["description"] == "Answers chemistry questions."
