# Admin User-Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin page (behind the existing shared `ADMIN_PASSWORD`) to list users + session counts, view/read any user's sessions and conversations, reset a password, rename a user, and delete a user (cascading their data) — plus a human-readable username validation error.

**Architecture:** New PostgREST client methods + an `admin_users` service, six `X-Admin-Password`-gated endpoints added to the existing admin router, and a Users panel added to the existing `/admin` page. Cascade-delete reuses `session_service.delete`.

**Tech Stack:** FastAPI, httpx, PostgREST, argon2 (`hash_password`), pytest + respx, vanilla JS.

## Global Constraints

- **Python 3.9.6** — new modules with module-level `X | None` need `from __future__ import annotations`.
- **Never commit secrets** (`.env` gitignored).
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 126 passing).
- All new admin endpoints reuse `_require_admin` (403 if `ADMIN_PASSWORD` unset, 401 on mismatch) via an `X-Admin-Password` header. Passwords are never returned/echoed/logged — reset-only.
- 404 for unknown `user_id`/`session_id`; rename to a taken username → 409; reset password <8 or invalid rename → 422.
- Commands assume CWD `backend/` and interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/clients/powabase_client.py` — `list_users`, `list_all_sessions`, `update_user`, `delete_user`.
- Modify `backend/app/models/schemas.py` — shared `validate_username`; refactor `RegisterRequest`; add `AdminResetPasswordRequest`, `AdminRenameRequest`.
- Create `backend/app/services/admin_users.py` — list-with-counts, cascade delete, reset password, rename.
- Modify `backend/app/api/routes/admin.py` — `require_admin_header` dependency + the six endpoints.
- Modify `frontend/admin.html`, `frontend/admin.js`, `frontend/styles.css` — Users panel.

---

### Task 1: Client methods for users & sessions

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client_sessions.py`

**Interfaces:**
- Produces: `list_users() -> list`; `list_all_sessions() -> list`; `update_user(user_id, fields) -> None`; `delete_user(user_id) -> None`.

- [ ] **Step 1: Write failing tests** — append (respx):

```python
@respx.mock
def test_list_users_orders_by_created():
    route = respx.get(f"{BASE_URL}/rest/v1/users").mock(
        return_value=httpx.Response(200, json=[{"id": "u1", "username": "a"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.list_users()[0]["id"] == "u1"
    assert route.calls[0].request.url.params["order"] == "created_at.desc"


@respx.mock
def test_list_all_sessions_returns_rows():
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "owner_id": "u1"}])
    )
    assert PowabaseClient(BASE_URL, "k").list_all_sessions()[0]["owner_id"] == "u1"


@respx.mock
def test_update_user_patches_by_id():
    route = respx.patch(f"{BASE_URL}/rest/v1/users").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").update_user("u1", {"username": "b"})
    assert route.calls[0].request.url.params["id"] == "eq.u1"
    assert json.loads(route.calls[0].request.content) == {"username": "b"}


@respx.mock
def test_delete_user_deletes_by_id():
    route = respx.delete(f"{BASE_URL}/rest/v1/users").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").delete_user("u1")
    assert route.calls[0].request.url.params["id"] == "eq.u1"
```

(`json`, `httpx`, `respx`, `BASE_URL`, `PowabaseClient` are already imported in this file.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_powabase_client_sessions.py -q` → FAIL.

- [ ] **Step 3: Implement** — in the `# Users (PostgREST)` section of `powabase_client.py`:

```python
    def list_users(self) -> list:
        response = self._client.get("/rest/v1/users", params={"order": "created_at.desc"})
        self._raise_for_status(response)
        return response.json()

    def list_all_sessions(self) -> list:
        response = self._client.get("/rest/v1/sessions", params={"order": "updated_at.desc"})
        self._raise_for_status(response)
        return response.json()

    def update_user(self, user_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/users", params={"id": f"eq.{user_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_user(self, user_id: str) -> None:
        response = self._client.delete("/rest/v1/users", params={"id": f"eq.{user_id}"})
        self._raise_for_status(response)
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_powabase_client_sessions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: client list_users/list_all_sessions/update_user/delete_user"`

---

### Task 2: Shared username validator + admin schemas

**Files:**
- Modify: `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_schemas_username.py` (new), `backend/tests/unit/test_routes_auth.py`

**Interfaces:**
- Produces: `validate_username(value: str) -> str` (friendly `ValueError`); `AdminResetPasswordRequest{password}`; `AdminRenameRequest{username}`. `RegisterRequest` now validates via `validate_username` (no raw `pattern=`).

- [ ] **Step 1: Write failing tests** — `test_schemas_username.py`:

```python
import pytest
from app.models.schemas import validate_username, RegisterRequest

def test_valid_usernames_pass():
    assert validate_username("Oscar.Zheng") == "Oscar.Zheng"
    assert validate_username("a_b-1") == "a_b-1"

@pytest.mark.parametrize("bad", ["Oscar Zheng", "ab", "___", "a"*33, "no!bang"])
def test_bad_usernames_friendly_message(bad):
    with pytest.raises(ValueError) as e:
        validate_username(bad)
    assert "letters, numbers" in str(e.value)

def test_register_request_rejects_space_with_friendly_message():
    with pytest.raises(ValueError) as e:
        RegisterRequest(username="Oscar Zheng", password="password123")
    assert "letters, numbers" in str(e.value)
```

Add to `test_routes_auth.py` (confirms the route still 422s and no longer leaks the raw regex):

```python
def test_register_space_username_422():
    r = TestClient(build_app(FakeClient())).post(
        "/auth/register", json={"username": "Oscar Zheng", "password": "password123"}
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_schemas_username.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `schemas.py`, add near the top (after imports):

```python
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def validate_username(value: str) -> str:
    v = value.strip()
    if not _USERNAME_RE.match(v) or not re.search(r"[A-Za-z0-9]", v):
        raise ValueError(
            "Username can only contain letters, numbers, dots, dashes, and "
            "underscores (3–32 characters)."
        )
    return v
```

Replace `RegisterRequest` with (drop the `pattern=` and the old alphanumeric validator):

```python
class RegisterRequest(BaseModel):
    username: str
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return validate_username(v)
```

Add the two admin schemas (e.g. after `AdminVerifyRequest`):

```python
class AdminResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


class AdminRenameRequest(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return validate_username(v)
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_schemas_username.py tests/unit/test_routes_auth.py -q` → PASS, then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: friendly username validator + admin reset/rename schemas"`

---

### Task 3: admin_users service

**Files:**
- Create: `backend/app/services/admin_users.py`
- Test: `backend/tests/unit/test_admin_users.py`

**Interfaces:**
- Consumes: `client.list_users`/`list_all_sessions`/`get_user`/`get_user_by_username`/`update_user`/`delete_user`/`list_sessions`; `session_service.delete`; `hash_password`.
- Produces: `list_users_with_counts(client) -> list`; `delete_user(client, session_service, user_id) -> bool`; `reset_password(client, user_id, new_password) -> bool`; `rename_user(client, user_id, new_username) -> dict`; `class UsernameTakenError(Exception)`.

- [ ] **Step 1: Write failing tests** — `test_admin_users.py`:

```python
import pytest
from app.services import admin_users
from app.services.admin_users import UsernameTakenError
from app.core.security import verify_password


class FakeClient:
    def __init__(self):
        self.users = [
            {"id": "u1", "username": "alice", "created_at": "t1", "password_hash": "h"},
            {"id": "u2", "username": "bob", "created_at": "t2", "password_hash": "h"},
        ]
        self.sessions = [
            {"id": "s1", "owner_id": "u1"}, {"id": "s2", "owner_id": "u1"},
        ]
        self.updated = []
        self.deleted_users = []

    def list_users(self): return list(self.users)
    def list_all_sessions(self): return list(self.sessions)
    def list_sessions(self, owner_id): return [s for s in self.sessions if s["owner_id"] == owner_id]
    def get_user(self, uid): return next((u for u in self.users if u["id"] == uid), None)
    def get_user_by_username(self, name): return next((u for u in self.users if u["username"] == name), None)
    def update_user(self, uid, fields): self.updated.append((uid, fields))
    def delete_user(self, uid): self.deleted_users.append(uid)


class FakeSessionService:
    def __init__(self): self.deleted = []
    def delete(self, sid): self.deleted.append(sid); return True


def test_list_users_with_counts():
    rows = admin_users.list_users_with_counts(FakeClient())
    by_id = {r["id"]: r for r in rows}
    assert by_id["u1"]["session_count"] == 2
    assert by_id["u2"]["session_count"] == 0
    assert "password_hash" not in by_id["u1"]  # never exposed


def test_delete_user_cascades_sessions_then_user():
    client, ss = FakeClient(), FakeSessionService()
    assert admin_users.delete_user(client, ss, "u1") is True
    assert set(ss.deleted) == {"s1", "s2"}
    assert client.deleted_users == ["u1"]


def test_delete_user_missing_returns_false():
    client, ss = FakeClient(), FakeSessionService()
    assert admin_users.delete_user(client, ss, "ghost") is False
    assert client.deleted_users == []


def test_reset_password_hashes_and_updates():
    client = FakeClient()
    assert admin_users.reset_password(client, "u1", "newpass123") is True
    uid, fields = client.updated[0]
    assert uid == "u1" and verify_password("newpass123", fields["password_hash"])


def test_reset_password_missing_user_false():
    assert admin_users.reset_password(FakeClient(), "ghost", "newpass123") is False


def test_rename_user_updates():
    client = FakeClient()
    result = admin_users.rename_user(client, "u1", "alice2")
    assert result == {"id": "u1", "username": "alice2"}
    assert client.updated[0] == ("u1", {"username": "alice2"})


def test_rename_user_taken_raises():
    with pytest.raises(UsernameTakenError):
        admin_users.rename_user(FakeClient(), "u1", "bob")


def test_rename_user_same_name_ok():
    # renaming to your own (lowercased) name is not a conflict
    client = FakeClient()
    assert admin_users.rename_user(client, "u1", "Alice")["username"] == "alice"
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_admin_users.py -q` → FAIL.

- [ ] **Step 3: Implement** — `admin_users.py`:

```python
from __future__ import annotations

from app.core.security import hash_password
from app.models.schemas import validate_username


class UsernameTakenError(Exception):
    pass


def list_users_with_counts(client) -> list:
    counts: dict = {}
    for s in client.list_all_sessions():
        owner = s.get("owner_id")
        if owner:
            counts[owner] = counts.get(owner, 0) + 1
    return [
        {
            "id": u["id"],
            "username": u["username"],
            "created_at": u.get("created_at"),
            "session_count": counts.get(u["id"], 0),
        }
        for u in client.list_users()
    ]


def delete_user(client, session_service, user_id: str) -> bool:
    if client.get_user(user_id) is None:
        return False
    for s in client.list_sessions(user_id):
        session_service.delete(s["id"])  # cascades KB + agent + row (best-effort)
    client.delete_user(user_id)
    return True


def reset_password(client, user_id: str, new_password: str) -> bool:
    if client.get_user(user_id) is None:
        return False
    client.update_user(user_id, {"password_hash": hash_password(new_password)})
    return True


def rename_user(client, user_id: str, new_username: str) -> dict:
    uname = validate_username(new_username).lower()
    existing = client.get_user_by_username(uname)
    if existing is not None and existing["id"] != user_id:
        raise UsernameTakenError(uname)
    client.update_user(user_id, {"username": uname})
    return {"id": user_id, "username": uname}
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_admin_users.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: admin_users service (counts, cascade delete, reset, rename)"`

---

### Task 4: Admin dependency + user-management routes

**Files:**
- Modify: `backend/app/api/routes/admin.py`
- Test: `backend/tests/unit/test_routes_admin.py`

**Interfaces:**
- Consumes: `admin_users` (Task 3); `session_service`; `client`; `_require_admin` (already in admin.py); `AdminResetPasswordRequest`/`AdminRenameRequest`.
- Produces the six endpoints (see plan header table), each gated by `require_admin_header`.

- [ ] **Step 1: Write failing tests** — add to `test_routes_admin.py` (follow the file's existing app-builder pattern; the fake session service exposes `delete` and the fake client exposes the user/session methods used by `admin_users` + `get_session_row`/`get_session_messages`). Cover:
  - `GET /admin/users` with no `X-Admin-Password` → 403 when unset OR 401 when wrong password (match how the file's other admin tests set `ADMIN_PASSWORD`); with the correct header → 200 and the counts list.
  - `GET /admin/users/{id}/sessions` → 200 list; unknown user → 404.
  - `GET /admin/sessions/{id}/messages` → 200 messages; unknown session → 404.
  - `POST /admin/users/{id}/reset-password` `{password:"short"}` → 422; valid → 204; unknown → 404.
  - `PATCH /admin/users/{id}` `{username:"taken"}` → 409; valid → 200; unknown → 404.
  - `DELETE /admin/users/{id}` → 204; unknown → 404.

  Use the existing test's approach for setting `ADMIN_PASSWORD` (via `monkeypatch.setenv` + `get_settings.cache_clear()`), and send `headers={"X-Admin-Password": "<pw>"}`.

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_admin.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `admin.py` add the dependency + routes:

```python
from fastapi import Header
from app.api.routes.sessions import _format_messages
from app.models.schemas import (
    AdminRenameRequest, AdminResetPasswordRequest, MessagesResponse, SessionSummary,
)
from app.services import admin_users
from app.services.admin_users import UsernameTakenError
from app.services.session_service import SessionService, get_session_service


def require_admin_header(x_admin_password: str = Header(None)) -> None:
    _require_admin(x_admin_password or "")


@router.get("/admin/users")
def admin_list_users(
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    return admin_users.list_users_with_counts(client)


@router.get("/admin/users/{user_id}/sessions", response_model=list[SessionSummary])
def admin_user_sessions(
    user_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if client.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [
        {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
        for r in client.list_sessions(user_id)
    ]


@router.get("/admin/sessions/{session_id}/messages", response_model=MessagesResponse)
def admin_session_messages(
    session_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = client.get_session_row(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    psid = row.get("powabase_session_id")
    if not psid:
        return MessagesResponse(messages=[])
    try:
        raw = client.get_session_messages(psid)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return MessagesResponse(messages=_format_messages(raw))


@router.post("/admin/users/{user_id}/reset-password", status_code=204)
def admin_reset_password(
    user_id: str,
    req: AdminResetPasswordRequest,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if not admin_users.reset_password(client, user_id, req.password):
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=204)


@router.patch("/admin/users/{user_id}")
def admin_rename_user(
    user_id: str,
    req: AdminRenameRequest,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
):
    if client.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        return admin_users.rename_user(client, user_id, req.username)
    except UsernameTakenError:
        raise HTTPException(status_code=409, detail="Username already taken")


@router.delete("/admin/users/{user_id}", status_code=204)
def admin_delete_user(
    user_id: str,
    _: None = Depends(require_admin_header),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    if not admin_users.delete_user(client, sessions, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=204)
```

Add `Response` to the `fastapi` import line in `admin.py` (`from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile`).

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_routes_admin.py -q` → PASS, then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: admin user-management endpoints"`

---

### Task 5: Frontend Users panel

**Files:**
- Modify: `frontend/admin.html`, `frontend/admin.js`, `frontend/styles.css`

- [ ] **Step 1: Add the panel to `admin.html`** — after the `#uploader` section, add:

```html
<section class="admin-card" id="users-panel" hidden>
  <div class="admin-row admin-row--between">
    <p class="admin-hint">Manage user accounts. Passwords can be reset, never viewed.</p>
    <button type="button" id="users-refresh" class="btn-solid">Refresh</button>
  </div>
  <div id="users-table"></div>
  <p class="admin-status" id="users-status"></p>
</section>
```

- [ ] **Step 2: Reveal the panel on unlock** — in `admin.js`, where a successful `/admin/verify` currently does `uploader.hidden = false;`, also show the panel and load it: `usersPanel.hidden = false; loadUsers();` (add `const usersPanel = document.getElementById("users-panel");` etc. up top).

- [ ] **Step 3: Implement the Users logic in `admin.js`** — add an `adminFetch(url, options)` helper that injects `headers["X-Admin-Password"] = adminPassword` (merging any existing headers) and on 401 resets to the gate. Then:
  - `loadUsers()` → `adminFetch("/admin/users")` → render a table: each row shows username, created date, session count, and buttons **Sessions**, **Reset PW**, **Rename**, **Delete**.
  - **Sessions** → `adminFetch("/admin/users/{id}/sessions")` → list names+dates below the row; each session gets a **Read** button → `adminFetch("/admin/sessions/{sid}/messages")` → render the messages (role + text) inline.
  - **Reset PW** → `window.prompt("New password (8+ chars):")` → `POST /admin/users/{id}/reset-password` `{password}`; show result in `#users-status`.
  - **Rename** → `window.prompt("New username:", currentName)` → `PATCH /admin/users/{id}` `{username}`; reload on success.
  - **Delete** → `window.confirm("Delete <username> and ALL their sessions/data?")` → `DELETE /admin/users/{id}`; reload on success.
  - Render errors readably (parse `body.detail`; if it is an array, join the `msg` fields — mirror the main app's `errorText`).
  - Build DOM with `document.createElement` + `textContent` (no `innerHTML` with user data — usernames/message text must not be injected as HTML).

- [ ] **Step 4: Style in `styles.css`** — a simple `#users-table` layout (rows with spaced columns + small action buttons), `.admin-row--between` (space-between), and a `.user-sessions`/`.user-messages` nested block, consistent with the existing admin card aesthetic and theme.

- [ ] **Step 5: Verify** — `node -c frontend/admin.js`; confirm every `getElementById` id exists in `admin.html`; grep that all admin data calls go through `adminFetch` (carry the header) and that message/username text is set via `textContent`, not `innerHTML`.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: admin Users panel (list, sessions, read chats, reset, rename, delete)"`

---

### Task 6: Live smoke verification

**Files:** none. Requires `.env` (`ADMIN_PASSWORD` set), network, migration 002 already applied.

- [ ] **Step 1** Restart the server; confirm `/health` 200.
- [ ] **Step 2** Register two users (`padmin_a`, `padmin_b`), each creating one session (send a chat) so they have data.
- [ ] **Step 3** `GET /admin/users` with `X-Admin-Password: <pw>` → both users present with `session_count` ≥1. Wrong/absent header → 401/403.
- [ ] **Step 4** `GET /admin/users/{a}/sessions` → a's session; `GET /admin/sessions/{sid}/messages` → the messages (read-chats works).
- [ ] **Step 5** Rename `padmin_a` → `padmin_a2`; confirm login with the new username works and the old one fails.
- [ ] **Step 6** Reset `padmin_b`'s password; confirm login with the new password works, old fails.
- [ ] **Step 7** `DELETE /admin/users/{b}` → 204; confirm `GET /admin/users` no longer lists b, b can't log in, and b's session row is gone.
- [ ] **Step 8** Clean up remaining smoke users/sessions. Record status codes in the task report. No commit.

---

## Self-Review

- **Spec coverage:** list+counts (Tasks 3,4), view sessions (4), read chats (4), reset password (3,4), rename (3,4), delete cascade (3,4), shared-password gate via header (4), friendly username error (2), frontend panel (5), live proof (6). Covered.
- **Placeholder scan:** backend steps carry complete code; the frontend task names exact ids, the `adminFetch` header behavior, the exact endpoints per action, and the no-`innerHTML` rule.
- **Type/name consistency:** `list_users`/`list_all_sessions`/`update_user`/`delete_user`, `validate_username`, `admin_users.list_users_with_counts`/`delete_user`/`reset_password`/`rename_user`/`UsernameTakenError`, `require_admin_header` are used identically across producer/consumer tasks.
- **Green ordering:** Tasks 1–3 additive; Task 2 refactors RegisterRequest but keeps its tests green (valid names pass, bad names still 422); Task 4 adds routes (additive); Task 5 frontend-only; Task 6 verification.
