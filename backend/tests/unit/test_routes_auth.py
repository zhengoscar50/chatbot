from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_route
from app.api.deps import get_current_user
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.core.security import hash_password, create_access_token
from types import SimpleNamespace


class FakeClient:
    def __init__(self):
        self.users = []

    def get_user_by_username(self, username):
        return next((u for u in self.users if u["username"] == username), None)

    def insert_user(self, row):
        row = {"id": f"u-{len(self.users)}", **row}
        self.users.append(row)
        return row

    def get_user(self, user_id):
        return next((u for u in self.users if u["id"] == user_id), None)


def build_app(client):
    app = FastAPI()
    app.include_router(auth_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: client
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        auth_jwt_secret="test-secret", auth_token_ttl_hours=168
    )
    return app


def test_register_then_me():
    client = FakeClient()
    app = build_app(client)
    tc = TestClient(app)
    r = tc.post("/auth/register", json={"username": "Alice", "password": "hunter2pass"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["username"] == "alice"
    me = tc.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["username"] == "alice"


def test_register_duplicate_409():
    client = FakeClient()
    tc = TestClient(build_app(client))
    tc.post("/auth/register", json={"username": "alice", "password": "hunter2pass"})
    r = tc.post("/auth/register", json={"username": "alice", "password": "hunter2pass"})
    assert r.status_code == 409


def test_login_ok_and_bad_password_401():
    client = FakeClient()
    client.users.append({"id": "u-0", "username": "alice", "password_hash": hash_password("hunter2pass")})
    tc = TestClient(build_app(client))
    assert tc.post("/auth/login", json={"username": "alice", "password": "hunter2pass"}).status_code == 200
    r = tc.post("/auth/login", json={"username": "alice", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_token():
    assert TestClient(build_app(FakeClient())).get("/auth/me").status_code == 401


def test_me_rejects_bad_token():
    r = TestClient(build_app(FakeClient())).get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_register_symbol_only_username_422():
    """Symbol-only username should be rejected by validation (422)."""
    client = FakeClient()
    tc = TestClient(build_app(client))
    r = tc.post("/auth/register", json={"username": "___", "password": "password123"})
    assert r.status_code == 422


def test_register_space_username_422():
    r = TestClient(build_app(FakeClient())).post(
        "/auth/register", json={"username": "Oscar Zheng", "password": "password123"}
    )
    assert r.status_code == 422
