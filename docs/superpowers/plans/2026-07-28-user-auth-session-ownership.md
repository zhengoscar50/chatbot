# User Accounts & Session Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add username+password accounts and enforce session ownership on every session-scoped route, so a session UUID (or a typed username) is no longer enough to reach another user's data.

**Architecture:** Argon2id password hashing + a signed HS256 JWT (`PyJWT`) carried as `Authorization: Bearer`. A `users` table and an `owner_id` on `sessions`. A `get_current_user` dependency gates protected routes; session-addressing routes return **404** when the row isn't owned by the caller. Frontend gains a login/register gate and attaches the token to every request.

**Tech Stack:** FastAPI, httpx, PostgREST, `PyJWT`, `argon2-cffi`, pytest + respx, vanilla JS.

## Global Constraints

- **Python 3.9.6** — any new module using module-level `X | None` must start with `from __future__ import annotations`.
- **Never commit secrets.** `.env` is gitignored. `AUTH_JWT_SECRET` is a real signing secret — it lives only in `.env`, never in code or tests (tests use a dummy value).
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 100 passing).
- **Ownership mismatch and missing session both return 404** (no existence leak). Missing/invalid/expired token → **401**. Duplicate username on register → **409**. Bad login → **401** with a generic message (no user enumeration).
- Usernames are normalized to lowercase before storage/lookup; validated to `^[A-Za-z0-9_.-]{3,32}$`. Passwords min length 8.
- The admin general-knowledge password gate is unchanged and out of scope.
- Commands assume CWD `backend/` and interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/requirements.txt` — add `pyjwt`, `argon2-cffi`.
- Modify `backend/app/core/config.py` — `auth_jwt_secret` (required), `auth_token_ttl_hours` (default 168).
- Create `backend/app/core/security.py` — hashing + JWT helpers.
- Modify `backend/app/clients/powabase_client.py` — `insert_user`, `get_user_by_username`, `get_user`; change `list_sessions` to filter by `owner_id`.
- Create `backend/app/services/auth_service.py` — `AuthService.register` / `.authenticate`.
- Modify `backend/app/models/schemas.py` — auth request/response models; drop `user` from `SessionCreateRequest`.
- Create `backend/app/api/deps.py` — `get_current_user` dependency.
- Create `backend/app/api/routes/auth.py` — `/auth/register`, `/auth/login`, `/auth/me`.
- Modify `backend/app/services/session_service.py` — `create_session(owner_id, username, name)`, `list(owner_id)`, `get_owned_session`.
- Modify `backend/app/api/routes/sessions.py`, `chat.py`, `ingest.py` — auth dependency + ownership checks.
- Modify `backend/app/main.py` — register the auth router.
- Create `backend/migrations/002_create_users.sql` — `users` table + `sessions.owner_id`.
- Modify `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` — login/register gate, token, logout.

---

### Task 1: Dependencies & config

**Files:**
- Modify: `backend/requirements.txt`, `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config.py`, `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Produces: `Settings.auth_jwt_secret: str` (required), `Settings.auth_token_ttl_hours: int` (default 168).

- [ ] **Step 1: Install deps + add to requirements** — run `.venv/bin/pip install pyjwt argon2-cffi`, then add two lines to `requirements.txt`:

```
pyjwt
argon2-cffi
```

- [ ] **Step 2: Add the real secret to `.env`** (gitignored) — generate and append:

```bash
python3 -c "import secrets; print('AUTH_JWT_SECRET=' + secrets.token_urlsafe(48))" >> .env
```

- [ ] **Step 3: Write failing test** — append to `test_config.py`:

```python
def test_auth_settings(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
    from app.core.config import Settings
    s = Settings()
    assert s.auth_jwt_secret == "test-secret"
    assert s.auth_token_ttl_hours == 168
```

Also, in `test_config.py`'s existing `test_gating_defaults` add `monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")` alongside the other setenvs (auth_jwt_secret is now required). In `test_main_lifespan.py`, add the same line inside `set_env`.

- [ ] **Step 4: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → FAIL.

- [ ] **Step 5: Implement** — in `config.py`, add to `Settings` after `admin_password`:

```python
    auth_jwt_secret: str
    auth_token_ttl_hours: int = 168
```

- [ ] **Step 6: Run full suite** — `.venv/bin/python -m pytest -q` → all pass (the `.env` secret + updated helpers keep other Settings-constructing tests green).

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: auth deps + JWT config settings"`

---

### Task 2: Security helpers (hashing + JWT)

**Files:**
- Create: `backend/app/core/security.py`
- Test: `backend/tests/unit/test_security.py`

**Interfaces:**
- Produces: `hash_password(password) -> str`; `verify_password(password, password_hash) -> bool`; `create_access_token(user_id, secret, ttl_hours) -> str`; `decode_access_token(token, secret) -> str` (returns user_id, raises `TokenError`); `class TokenError(Exception)`.

- [ ] **Step 1: Write failing tests** — `test_security.py`:

```python
import pytest
from app.core.security import (
    hash_password, verify_password, create_access_token, decode_access_token, TokenError,
)

def test_hash_and_verify_roundtrip():
    h = hash_password("hunter2pass")
    assert h != "hunter2pass"
    assert verify_password("hunter2pass", h) is True
    assert verify_password("wrong", h) is False

def test_token_roundtrip():
    tok = create_access_token("user-1", "sekret", 168)
    assert decode_access_token(tok, "sekret") == "user-1"

def test_token_bad_secret_raises():
    tok = create_access_token("user-1", "sekret", 168)
    with pytest.raises(TokenError):
        decode_access_token(tok, "other-secret")

def test_token_expired_raises():
    tok = create_access_token("user-1", "sekret", 0)  # exp = iat, already expired
    import time; time.sleep(1)
    with pytest.raises(TokenError):
        decode_access_token(tok, "sekret")

def test_token_garbage_raises():
    with pytest.raises(TokenError):
        decode_access_token("not.a.jwt", "sekret")
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_security.py -q` → FAIL (import error).

- [ ] **Step 3: Implement** — `security.py`:

```python
from __future__ import annotations

import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, tampered, or expired."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: str, secret: str, ttl_hours: int) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + ttl_hours * 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> str:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise TokenError(str(e))
    return payload["sub"]
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_security.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: argon2 password hashing + JWT helpers"`

---

### Task 3: Powabase client — user rows + owner-scoped session list

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client_sessions.py`

**Interfaces:**
- Produces: `insert_user(row) -> dict`; `get_user_by_username(username) -> dict | None`; `get_user(user_id) -> dict | None`.
- Changes: `list_sessions(owner_id)` now filters `owner_id=eq.<id>` (was `user_slug`).

- [ ] **Step 1: Write failing tests** — append to `test_powabase_client_sessions.py` (respx):

```python
@respx.mock
def test_insert_user_returns_created_row():
    respx.post(f"{BASE_URL}/rest/v1/users").mock(
        return_value=httpx.Response(201, json=[{"id": "u-1", "username": "alice"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    row = client.insert_user({"username": "alice", "password_hash": "h"})
    assert row["id"] == "u-1"


@respx.mock
def test_get_user_by_username_found_and_missing():
    route = respx.get(f"{BASE_URL}/rest/v1/users")
    route.mock(return_value=httpx.Response(200, json=[{"id": "u-1", "username": "alice"}]))
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_user_by_username("alice")["id"] == "u-1"
    route.mock(return_value=httpx.Response(200, json=[]))
    assert client.get_user_by_username("nobody") is None


@respx.mock
def test_list_sessions_filters_by_owner_id():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s-1"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    client.list_sessions("owner-1")
    assert route.calls[0].request.url.params["owner_id"] == "eq.owner-1"
```

(Confirm `BASE_URL`, `httpx`, `respx`, `PowabaseClient` are already imported in this test file; they are.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_powabase_client_sessions.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `powabase_client.py`:

Change `list_sessions` to filter by owner:

```python
    def list_sessions(self, owner_id: str) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={"owner_id": f"eq.{owner_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()
```

Add user methods (new `# Users (PostgREST)` section):

```python
    def insert_user(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/users", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        created = response.json()
        return created[0] if isinstance(created, list) else created

    def get_user_by_username(self, username: str) -> dict:
        response = self._client.get(
            "/rest/v1/users", params={"username": f"eq.{username}"}
        )
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def get_user(self, user_id: str):
        response = self._client.get("/rest/v1/users", params={"id": f"eq.{user_id}"})
        if response.status_code == 400:  # malformed uuid -> treat as not found
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None
```

(Usernames are stored lowercased and validated, so `eq.<username>` is an exact match — no wildcard/`ilike` concerns.)

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_powabase_client_sessions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: client user rows + owner-scoped session list"`

---

### Task 4: AuthService (register / authenticate)

**Files:**
- Create: `backend/app/services/auth_service.py`
- Test: `backend/tests/unit/test_auth_service.py`

**Interfaces:**
- Consumes: `client.get_user_by_username`, `client.insert_user` (Task 3); `hash_password`, `verify_password` (Task 2); `PowabaseAPIError`.
- Produces: `AuthService(client)`; `.register(username, password) -> dict`; `.authenticate(username, password) -> dict`; `class DuplicateUsernameError(Exception)`; `class InvalidCredentialsError(Exception)`.

- [ ] **Step 1: Write failing tests** — `test_auth_service.py`:

```python
import pytest
from app.services.auth_service import (
    AuthService, DuplicateUsernameError, InvalidCredentialsError,
)
from app.core.security import hash_password


class FakeClient:
    def __init__(self, existing=None):
        self.users = list(existing or [])
        self.inserted = []

    def get_user_by_username(self, username):
        return next((u for u in self.users if u["username"] == username), None)

    def insert_user(self, row):
        row = {"id": f"u-{len(self.users)}", **row}
        self.users.append(row)
        self.inserted.append(row)
        return row


def test_register_creates_lowercased_user():
    client = FakeClient()
    user = AuthService(client).register("Alice", "hunter2pass")
    assert user["username"] == "alice"
    assert client.inserted[0]["password_hash"] != "hunter2pass"


def test_register_duplicate_raises():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": "h"}])
    with pytest.raises(DuplicateUsernameError):
        AuthService(client).register("ALICE", "hunter2pass")


def test_authenticate_happy():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": hash_password("hunter2pass")}])
    user = AuthService(client).authenticate("Alice", "hunter2pass")
    assert user["id"] == "u-0"


def test_authenticate_wrong_password_raises():
    client = FakeClient(existing=[{"id": "u-0", "username": "alice", "password_hash": hash_password("hunter2pass")}])
    with pytest.raises(InvalidCredentialsError):
        AuthService(client).authenticate("alice", "nope")


def test_authenticate_unknown_user_raises():
    with pytest.raises(InvalidCredentialsError):
        AuthService(FakeClient()).authenticate("ghost", "whatever")
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_auth_service.py -q` → FAIL.

- [ ] **Step 3: Implement** — `auth_service.py`:

```python
from __future__ import annotations

from app.clients.powabase_client import PowabaseAPIError
from app.core.security import hash_password, verify_password


class DuplicateUsernameError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, client):
        self.client = client

    def register(self, username: str, password: str) -> dict:
        uname = username.strip().lower()
        if self.client.get_user_by_username(uname) is not None:
            raise DuplicateUsernameError(uname)
        try:
            return self.client.insert_user(
                {"username": uname, "password_hash": hash_password(password)}
            )
        except PowabaseAPIError as e:
            # Unique-index race: two concurrent registers of the same name.
            if getattr(e, "status_code", None) == 409:
                raise DuplicateUsernameError(uname)
            raise

    def authenticate(self, username: str, password: str) -> dict:
        uname = username.strip().lower()
        user = self.client.get_user_by_username(uname)
        if user is None or not verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError()
        return user
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_auth_service.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: AuthService register/authenticate"`

---

### Task 5: Auth schemas, dependency & routes

**Files:**
- Modify: `backend/app/models/schemas.py` (ADD only; do not remove `user` yet)
- Create: `backend/app/api/deps.py`
- Create: `backend/app/api/routes/auth.py`
- Modify: `backend/app/main.py` (register the auth router)
- Test: `backend/tests/unit/test_routes_auth.py`

**Interfaces:**
- Consumes: `AuthService` (Task 4); `create_access_token`, `decode_access_token`, `TokenError` (Task 2); `get_powabase_client`, `get_settings`.
- Produces: `RegisterRequest`, `LoginRequest`, `AuthResponse`, `MeResponse`; `get_current_user(request) -> dict`; routes `POST /auth/register`, `POST /auth/login`, `GET /auth/me`.

- [ ] **Step 1: Add schemas** — in `schemas.py` add (and add `from pydantic import ... Field`, already present):

```python
class RegisterRequest(BaseModel):
    username: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str
```

- [ ] **Step 2: Write failing tests** — `test_routes_auth.py`:

```python
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
```

- [ ] **Step 3: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_auth.py -q` → FAIL (imports).

- [ ] **Step 4: Implement the dependency** — `app/api/deps.py`:

```python
from fastapi import Depends, Header, HTTPException

from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.core.security import TokenError, decode_access_token


def get_current_user(
    authorization: str = Header(None),
    client: PowabaseClient = Depends(get_powabase_client),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[len("Bearer "):]
    try:
        user_id = decode_access_token(token, get_settings().auth_jwt_secret)
    except TokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = client.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user
```

- [ ] **Step 5: Implement the routes** — `app/api/routes/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.schemas import AuthResponse, LoginRequest, MeResponse, RegisterRequest
from app.services.auth_service import (
    AuthService, DuplicateUsernameError, InvalidCredentialsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: dict, settings) -> str:
    return create_access_token(user["id"], settings.auth_jwt_secret, settings.auth_token_ttl_hours)


@router.post("/register", response_model=AuthResponse)
def register(
    req: RegisterRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    try:
        user = AuthService(client).register(req.username, req.password)
    except DuplicateUsernameError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return AuthResponse(token=_token_for(user, settings), username=user["username"])


@router.post("/login", response_model=AuthResponse)
def login(
    req: LoginRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    try:
        user = AuthService(client).authenticate(req.username, req.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return AuthResponse(token=_token_for(user, settings), username=user["username"])


@router.get("/me", response_model=MeResponse)
def me(user: dict = Depends(get_current_user)):
    return MeResponse(username=user["username"])
```

- [ ] **Step 6: Register the router** — in `main.py`, add `from app.api.routes.auth import router as auth_router` and `app.include_router(auth_router)` (before the StaticFiles mount).

- [ ] **Step 7: Run tests** — `.venv/bin/python -m pytest tests/unit/test_routes_auth.py -q` → PASS, then full suite green.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat: auth schemas, get_current_user dependency, /auth routes"`

---

### Task 6: Data model + ownership on session routes

**Files:**
- Create: `backend/migrations/002_create_users.sql`
- Modify: `backend/app/services/session_service.py`
- Modify: `backend/app/models/schemas.py` (drop `user` from `SessionCreateRequest`)
- Modify: `backend/app/api/routes/sessions.py`
- Test: `backend/tests/unit/test_session_service.py`, `backend/tests/unit/test_routes_sessions.py`

**Interfaces:**
- Consumes: `get_current_user` (Task 5); `client.list_sessions(owner_id)`, `get_session_row` (Task 3).
- Produces: `SessionService.create_session(owner_id, username, name=None)`, `.list(owner_id)`, `.get_owned_session(session_id, owner_id) -> dict | None`.

- [ ] **Step 1: Create the migration** — `migrations/002_create_users.sql`:

```sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  username text not null,
  password_hash text not null,
  created_at timestamptz not null default now()
);
create unique index if not exists users_username_unique on public.users (lower(username));
alter table public.users enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

alter table public.sessions add column if not exists owner_id uuid;
create index if not exists sessions_owner_updated_idx
  on public.sessions (owner_id, updated_at desc);
```

- [ ] **Step 2: Update tests** — in `test_session_service.py`: change `create_session` calls to the new signature and assert `owner_id`; change `list` to take an owner id; add a `get_owned_session` test. Concretely, replace the body of `test_create_session_provisions_and_inserts` and `test_list_returns_summaries_for_user`, and add tests:

```python
def test_create_session_sets_owner_and_slug():
    client = FakeClient()
    row = SessionService(client, model="m").create_session("owner-1", "Alice", name="Taxes")
    assert row["owner_id"] == "owner-1"
    assert row["user_slug"] == "alice"
    assert client.links == []
    assert client.inserted and client.inserted[0]["owner_id"] == "owner-1"


def test_list_filters_by_owner():
    client = FakeClient(rows=[
        {"id": "s1", "owner_id": "o1", "name": "A", "updated_at": "t1"},
        {"id": "s2", "owner_id": "o2", "name": "B", "updated_at": "t2"},
    ])
    result = SessionService(client, model="m").list("o1")
    assert result == [{"id": "s1", "name": "A", "updated_at": "t1"}]


def test_get_owned_session_returns_only_for_owner():
    client = FakeClient(rows=[{"id": "s1", "owner_id": "o1", "kb_id": "k", "agent_id": "a"}])
    svc = SessionService(client, model="m")
    assert svc.get_owned_session("s1", "o1")["id"] == "s1"
    assert svc.get_owned_session("s1", "o2") is None
    assert svc.get_owned_session("missing", "o1") is None
```

In this file's `FakeClient`, update `list_sessions` to filter by `owner_id` (matching the real client): `return [r for r in self.rows if r.get("owner_id") == owner_id]`. Delete the old `test_create_session_provisions_and_inserts` name if replaced, and the now-obsolete `test_list_returns_summaries_for_user`. Keep the detach test from the prior feature but update its `create_session` call to `("owner-1", "alice")`.

- [ ] **Step 3: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_session_service.py -q` → FAIL.

- [ ] **Step 4: Implement `session_service.py`** — change `create_session`, `list`, add `get_owned_session`:

```python
    def create_session(self, owner_id: str, username: str, name: str | None = None) -> dict:
        user_slug = slugify(username)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")
        session_id = str(uuid.uuid4())
        kb = self.client.create_knowledge_base(
            f"session-{session_id}-kb", description=f"Documents for session {session_id}"
        )
        agent = self.client.create_agent(
            f"session-{session_id}-agent", model=self.model, system_prompt=SYSTEM_PROMPT
        )
        row = {
            "id": session_id,
            "owner_id": owner_id,
            "user_slug": user_slug,
            "name": name or DEFAULT_NAME,
            "kb_id": kb["id"],
            "agent_id": agent["id"],
        }
        return self.client.insert_session(row)

    def list(self, owner_id: str) -> list:
        rows = self.client.list_sessions(owner_id)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
            for r in rows
        ]

    def get_owned_session(self, session_id: str, owner_id: str):
        row = self.client.get_session_row(session_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row
```

Keep `get`, `touch`, `rename`, `delete` as they are. (`get` is still used internally by `delete`.)

- [ ] **Step 5: Drop `user` from the schema** — in `schemas.py`, change `SessionCreateRequest` to:

```python
class SessionCreateRequest(BaseModel):
    name: Optional[str] = None
```

- [ ] **Step 6: Update `test_routes_sessions.py`** — override `get_current_user` and use the ownership methods. Add near the top:

```python
from app.api.deps import get_current_user
```

In the app-builder helper, add `app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}`. Update the `FakeSessionService` to implement `create_session(self, owner_id, username, name=None)`, `list(self, owner_id)`, and `get_owned_session(self, session_id, owner_id)` (returning its row for the owned id, `None` otherwise), and make `POST /sessions` / `GET /sessions` assertions match the no-`user` request/response. Add a test that a non-owner gets 404:

```python
def test_get_messages_404_for_non_owner(...):
    # get_owned_session returns None -> 404
```

(Follow the existing test file's structure; keep every existing behavior assertion that still applies.)

- [ ] **Step 7: Implement `sessions.py`** — add the auth dependency and ownership checks. Each handler gains `user: dict = Depends(get_current_user)`. Bodies:
  - `POST /sessions`: `sessions.create_session(user["id"], user["username"], req.name)`.
  - `GET /sessions`: `sessions.list(user["id"])` (remove the `user: str` query param).
  - `PATCH /sessions/{id}`, `GET /sessions/{id}/messages`: resolve via `sessions.get_owned_session(session_id, user["id"])`; 404 if `None`.
  - `DELETE /sessions/{id}`: check `sessions.get_owned_session(...)` first; if `None` → 404; else `sessions.delete(session_id)`.

Example for the messages route:

```python
@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def session_messages(
    session_id: str,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # ... unchanged from here ...
```

Apply the same `get_owned_session(... , user["id"])` pattern to PATCH and DELETE, and drop the `user` query param from GET. Add `from app.api.deps import get_current_user`.

- [ ] **Step 8: Run full suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 9: Commit** — `git add -A && git commit -m "feat: users/owner_id migration + ownership on session routes"`

---

### Task 7: Ownership on chat & ingest

**Files:**
- Modify: `backend/app/api/routes/chat.py`, `backend/app/api/routes/ingest.py`
- Test: `backend/tests/unit/test_routes_chat.py`, `backend/tests/unit/test_routes_ingest.py`

**Interfaces:**
- Consumes: `get_current_user`; `SessionService.get_owned_session`.

- [ ] **Step 1: Update `test_routes_chat.py`** — add `from app.api.deps import get_current_user`; in `build_app` add `app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}`. Change `FakeSessionService` so it exposes `get_owned_session(self, session_id, owner_id)` returning its row for `"s1"` (and `None` for `"missing"` / non-owner), replacing the current `get`. Add:

```python
def test_chat_404_for_non_owned_session(monkeypatch):
    # get_owned_session returns None -> 404
    svc = FakeSessionService()
    svc.get_owned_session = lambda sid, oid: None
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    r = post(TestClient(build_app(svc)), {"session_id": "s1", "query": "hi"})
    assert r.status_code == 404
```

- [ ] **Step 2: Update `test_routes_ingest.py`** — same pattern: override `get_current_user`; the fake session service resolves via `get_owned_session`; add a non-owner → 404 test.

- [ ] **Step 3: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_chat.py tests/unit/test_routes_ingest.py -q` → FAIL.

- [ ] **Step 4: Implement** — in `chat.py`: add `user: dict = Depends(get_current_user)` to the handler, and replace `row = sessions.get(req.session_id)` with:

```python
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
```

Add `from app.api.deps import get_current_user`. Everything else (gate/retrieval/persistence) is unchanged.

In `ingest.py`: add `user: dict = Depends(get_current_user)` to `ingest_file`, and replace `row = await run_in_threadpool(sessions.get, session_id)` with:

```python
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
```

Add `from app.api.deps import get_current_user`.

- [ ] **Step 5: Run full suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: ownership checks on chat and ingest routes"`

---

### Task 8: Frontend — login/register gate + token

**Files:**
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`

**Interfaces:** consumes `/auth/register`, `/auth/login`, `/auth/me`; sends `Authorization: Bearer` on all API calls; `/sessions` no longer sends/needs `user`.

- [ ] **Step 1: Add an auth screen to `index.html`** — before `<div class="app">`, add an overlay:

```html
<div class="auth-gate" id="auth-gate">
  <form class="auth-card" id="auth-form">
    <h1 class="auth-card__title">RAG Chat</h1>
    <p class="auth-card__mode" id="auth-mode-label">Log in to continue</p>
    <input id="auth-username" class="auth-input" placeholder="Username" autocomplete="username" />
    <input id="auth-password" type="password" class="auth-input" placeholder="Password" autocomplete="current-password" />
    <button type="submit" id="auth-submit" class="auth-submit">Log in</button>
    <p class="auth-error" id="auth-error"></p>
    <button type="button" id="auth-toggle" class="auth-toggle">Need an account? Register</button>
  </form>
</div>
```

In the sidebar head, replace the `User` label + `#user-input` block with a logged-in row:

```html
<div class="sidebar__head">
  <span class="sidebar__user" id="current-user"></span>
  <button type="button" id="logout-btn" class="logout-btn">Log out</button>
</div>
```

- [ ] **Step 2: Add styles to `styles.css`** — a centered full-screen overlay `.auth-gate` (flex center, covers viewport, `z-index` above `.app`), a card `.auth-card` (matching the app's gradient/rounded aesthetic), `.auth-input`, `.auth-submit`, `.auth-error` (red, hidden when empty), `.auth-toggle` (link-style), `.logout-btn`. When authenticated, hide `#auth-gate` via a `hidden` attribute.

- [ ] **Step 3: Rewrite the auth-relevant parts of `app.js`:**

Replace the `USER_KEY`/`currentUser` login with token state and an `authFetch` wrapper. At the top:

```js
const TOKEN_KEY = "rag-chat-token";
const NAME_KEY = "rag-chat-username";
let authToken = null;
let currentUsername = null;
let authMode = "login"; // or "register"
```

Add a fetch wrapper that injects the header and handles 401 globally:

```js
async function authFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    doLogout();
    throw new Error("Session expired — please log in again.");
  }
  return response;
}
```

Replace every existing `fetch(...)` to a protected endpoint with `authFetch(...)`: the calls in `ensureSession`, `loadSessions`, `deleteSession`, `startRename`, `createSession`, `openSession`, the file-upload handler, and the chat submit. Also:
- In `ensureSession` and `createSession`, the POST body becomes `JSON.stringify({})` (or `{ }` — no `user`).
- In `loadSessions`, the URL becomes `authFetch("/sessions")` (no `?user=`).

Replace `init()` to check auth first:

```js
function init() {
  setComposerEnabled(false);
  authToken = localStorage.getItem(TOKEN_KEY);
  currentUsername = localStorage.getItem(NAME_KEY);
  wireAuthForm();
  newSessionButton.addEventListener("click", createSession);
  sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
  document.getElementById("logout-btn").addEventListener("click", doLogout);
  if (authToken) enterApp();
  else showAuthGate();
}
```

Add the auth flow functions (`wireAuthForm` toggles login/register and submits to `/auth/login` or `/auth/register`; on success store token+username and call `enterApp()`; on failure show `#auth-error`). `enterApp()` hides `#auth-gate`, sets `#current-user` text, enables the composer, and calls `loadSessions()`. `showAuthGate()` shows the overlay. `doLogout()` clears `localStorage`, resets state, and shows the gate.

Remove `switchUser` and the old `#user-input` wiring entirely. Guards that were `if (!currentUser)` become `if (!authToken)`.

- [ ] **Step 4: Verify** — `node -c frontend/app.js` (syntax), and confirm every `getElementById` used has a matching element id in `index.html` (`auth-gate`, `auth-form`, `auth-mode-label`, `auth-username`, `auth-password`, `auth-submit`, `auth-error`, `auth-toggle`, `current-user`, `logout-btn`). Start the server and take a headless screenshot of `/` to confirm the login gate renders and, after registering, the chat UI appears.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: frontend login/register gate + bearer token"`

---

### Task 9: Live smoke verification

**Files:** none (verification). Requires `.env` (with `AUTH_JWT_SECRET`), network, and the **migration applied**.

- [ ] **Step 1: Apply the migration** — run `backend/migrations/002_create_users.sql` against the Powabase project (Studio SQL editor or the Database URL). Confirm `users` exists and `sessions.owner_id` was added.

- [ ] **Step 2: Restart the server** — kill uvicorn, relaunch, confirm `/health` is 200.

- [ ] **Step 3: Register two users** — `POST /auth/register` for `alice` and `bob`; capture both tokens.

- [ ] **Step 4: Owner happy path** — as alice: `POST /sessions` (Bearer alice) → session S; `POST /ingest/file` a small PDF into S; `POST /chat` about it → grounded answer.

- [ ] **Step 5: Cross-owner denial (the core proof)** — as **bob**, call `POST /chat`, `GET /sessions/{S}/messages`, and `DELETE /sessions/{S}` with **alice's session id S** → each must return **404**. `GET /sessions` (Bearer bob) must NOT list S.

- [ ] **Step 6: No-token denial** — call `/chat` and `/sessions` with no `Authorization` header → **401**.

- [ ] **Step 7: Clean up** — delete the smoke sessions (as their owner); note the two smoke users remain in the `users` table (harmless) or delete them via PostgREST.

- [ ] **Step 8: Record** — note the observed status codes in the task report. No commit.

---

## Self-Review

- **Spec coverage:** accounts + argon2 + JWT (Tasks 1,2,4,5); users table + owner_id (Task 6); `get_current_user` 401s (Task 5); ownership 404 on every session route (Tasks 6,7); drop client-supplied `user` (Task 6); frontend gate + token + logout (Task 8); live cross-owner proof (Task 9). Covered.
- **Placeholder scan:** backend steps carry complete code; the frontend task gives exact element ids, the `authFetch` wrapper, and the exact list of call sites to convert (no vague "add auth to the frontend").
- **Type/name consistency:** `hash_password`/`verify_password`/`create_access_token`/`decode_access_token`/`TokenError`, `AuthService.register`/`.authenticate`, `get_current_user`, `SessionService.create_session(owner_id, username, name)`/`.list(owner_id)`/`.get_owned_session(session_id, owner_id)`, `client.list_sessions(owner_id)`/`insert_user`/`get_user_by_username`/`get_user` are used identically across producer/consumer tasks.
- **Green ordering:** Tasks 1–5 are additive (auth surface added but not yet enforced). Task 6 atomically swaps session_service + sessions routes + `SessionCreateRequest` + their tests. Task 7 atomically adds enforcement to chat+ingest + their tests. Task 8 is frontend-only. Task 9 is verification.
