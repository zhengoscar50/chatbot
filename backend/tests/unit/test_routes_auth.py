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


def build_app(client, invite_code=""):
    app = FastAPI()
    app.include_router(auth_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: client
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        auth_jwt_secret="test-secret", auth_token_ttl_hours=168,
        signup_invite_code=invite_code,
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


# --- invite-code gate -------------------------------------------------------

def test_signup_policy_reports_whether_a_code_is_needed():
    open_app = TestClient(build_app(FakeClient()))
    assert open_app.get("/auth/signup-policy").json() == {"invite_required": False}

    gated = TestClient(build_app(FakeClient(), invite_code="let-me-in"))
    assert gated.get("/auth/signup-policy").json() == {"invite_required": True}


def test_signup_policy_never_leaks_the_code():
    body = TestClient(build_app(FakeClient(), invite_code="s3cret")).get(
        "/auth/signup-policy"
    ).text
    assert "s3cret" not in body


def test_registration_is_open_when_no_code_is_configured():
    r = TestClient(build_app(FakeClient())).post(
        "/auth/register", json={"username": "alice", "password": "password123"}
    )
    assert r.status_code == 200


def test_registration_requires_the_code_when_configured():
    c = TestClient(build_app(FakeClient(), invite_code="let-me-in"))
    r = c.post("/auth/register", json={"username": "alice", "password": "password123"})
    assert r.status_code == 403
    assert "invite code" in r.json()["detail"].lower()


def test_registration_rejects_a_wrong_code():
    c = TestClient(build_app(FakeClient(), invite_code="let-me-in"))
    r = c.post("/auth/register", json={
        "username": "alice", "password": "password123", "invite_code": "nope",
    })
    assert r.status_code == 403


def test_registration_accepts_the_right_code():
    c = TestClient(build_app(FakeClient(), invite_code="let-me-in"))
    r = c.post("/auth/register", json={
        "username": "alice", "password": "password123", "invite_code": "let-me-in",
    })
    assert r.status_code == 200


def test_login_is_never_gated_by_the_invite_code():
    # Rotating the code must not lock out anyone who already registered.
    client = FakeClient()
    TestClient(build_app(client)).post(
        "/auth/register", json={"username": "alice", "password": "password123"}
    )
    gated = TestClient(build_app(client, invite_code="rotated-code"))
    r = gated.post("/auth/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 200
