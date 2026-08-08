import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import admin as admin_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.general_kb import get_general_kb_id
from app.services.agent_service import get_agent_service
from app.services.session_service import get_session_service


def set_admin(monkeypatch, password="s3cret"):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    if password is None:
        # Force it empty (treated as "not configured") rather than deleting the
        # env var — an env var overrides a real backend/.env, so this stays
        # robust even when ADMIN_PASSWORD is set there for live use.
        monkeypatch.setenv("ADMIN_PASSWORD", "")
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


class FakeAdminClient:
    def __init__(self):
        self.users = [
            {"id": "u1", "username": "alice", "created_at": "t1", "password_hash": "h"},
            {"id": "u2", "username": "bob", "created_at": "t2", "password_hash": "h"},
        ]
        self.sessions = [
            {"id": "s1", "owner_id": "u1", "name": "Session 1", "updated_at": "t3"},
            {"id": "s2", "owner_id": "u1", "name": "Session 2", "updated_at": "t4"},
        ]
        self.session_rows = {
            "s1": {"id": "s1", "owner_id": "u1", "powabase_session_id": "psid-1"},
        }
        self.session_messages = {
            "psid-1": {"messages": [{"role": "user", "content": "hi"}]},
        }
        self.updated = []
        self.deleted_users = []

    def list_users(self):
        return list(self.users)

    def list_all_sessions(self):
        return list(self.sessions)

    def list_sessions(self, owner_id):
        return [s for s in self.sessions if s["owner_id"] == owner_id]

    def get_user(self, uid):
        return next((u for u in self.users if u["id"] == uid), None)

    def get_user_by_username(self, name):
        return next((u for u in self.users if u["username"] == name), None)

    def update_user(self, uid, fields):
        self.updated.append((uid, fields))

    def delete_user(self, uid):
        self.deleted_users.append(uid)

    def get_session_row(self, session_id):
        return self.session_rows.get(session_id)

    def list_messages(self, session_id):
        return [{"role": "user", "content": "hi", "citations": []}]

    def get_session_messages(self, powabase_session_id):
        return self.session_messages.get(powabase_session_id, {"messages": []})


class FakeAdminAgentService:
    """Deleting a user must also take their agents."""

    def __init__(self):
        self.deleted = []

    def list(self, owner_id):
        return [{"id": "ag-1", "owner_id": owner_id}]

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        return True


class FakeSessionService:
    def __init__(self):
        self.deleted = []

    def delete(self, session_id):
        self.deleted.append(session_id)
        return True


def build_users_app():
    app = FastAPI()
    app.include_router(admin_route.router)
    client = FakeAdminClient()
    sessions = FakeSessionService()
    agents = FakeAdminAgentService()
    app.dependency_overrides[get_powabase_client] = lambda: client
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    app.dependency_overrides[get_session_service] = lambda: sessions
    app.dependency_overrides[get_agent_service] = lambda: agents
    app.state.fake_client = client
    app.state.fake_sessions = sessions
    app.state.fake_agents = agents
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


# --- GET /admin/users -------------------------------------------------

def test_list_users_403_when_not_configured(monkeypatch):
    set_admin(monkeypatch, password=None)
    r = TestClient(build_users_app()).get("/admin/users")
    assert r.status_code == 403


def test_list_users_401_wrong_header(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/users", headers={"X-Admin-Password": "nope"}
    )
    assert r.status_code == 401


def test_list_users_200_with_counts(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/users", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 200
    by_id = {u["id"]: u for u in r.json()}
    assert by_id["u1"]["session_count"] == 2
    assert by_id["u2"]["session_count"] == 0
    assert "password_hash" not in by_id["u1"]


# --- GET /admin/users/{id}/sessions ------------------------------------

def test_user_sessions_200(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/users/u1/sessions", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert ids == {"s1", "s2"}


def test_user_sessions_404_unknown_user(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/users/ghost/sessions", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 404


# --- GET /admin/sessions/{id}/messages ---------------------------------

def test_session_messages_200(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/sessions/s1/messages", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 200
    assert r.json()["messages"][0]["text"] == "hi"


def test_session_messages_404_unknown_session(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).get(
        "/admin/sessions/ghost/messages", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 404


# --- POST /admin/users/{id}/reset-password ------------------------------

def test_reset_password_422_short(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).post(
        "/admin/users/u1/reset-password",
        json={"password": "short"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 422


def test_reset_password_204_valid(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).post(
        "/admin/users/u1/reset-password",
        json={"password": "newpassword123"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 204


def test_reset_password_404_unknown_user(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).post(
        "/admin/users/ghost/reset-password",
        json={"password": "newpassword123"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 404


# --- PATCH /admin/users/{id} --------------------------------------------

def test_rename_user_409_taken(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).patch(
        "/admin/users/u1",
        json={"username": "bob"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 409


def test_rename_user_200_valid(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).patch(
        "/admin/users/u1",
        json={"username": "alice2"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 200
    assert r.json() == {"id": "u1", "username": "alice2"}


def test_rename_user_404_unknown(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).patch(
        "/admin/users/ghost",
        json={"username": "someone"},
        headers={"X-Admin-Password": "s3cret"},
    )
    assert r.status_code == 404


# --- DELETE /admin/users/{id} -------------------------------------------

def test_delete_user_204(monkeypatch):
    set_admin(monkeypatch)
    app = build_users_app()
    r = TestClient(app).delete(
        "/admin/users/u1", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 204
    assert app.state.fake_client.deleted_users == ["u1"]
    assert set(app.state.fake_sessions.deleted) == {"s1", "s2"}


def test_delete_user_404_unknown(monkeypatch):
    set_admin(monkeypatch)
    r = TestClient(build_users_app()).delete(
        "/admin/users/ghost", headers={"X-Admin-Password": "s3cret"}
    )
    assert r.status_code == 404


def test_delete_user_also_deletes_their_agents(monkeypatch):
    # Otherwise the agents outlive their owner: unreachable through the API,
    # still holding their knowledge bases and Powabase agents.
    set_admin(monkeypatch)
    app = build_users_app()

    r = TestClient(app).delete("/admin/users/u1", headers={"X-Admin-Password": "s3cret"})

    assert r.status_code == 204
    assert app.state.fake_agents.deleted == ["ag-1"]
