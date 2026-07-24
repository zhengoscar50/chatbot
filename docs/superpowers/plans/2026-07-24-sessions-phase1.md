# Saved Sessions (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-profile model with saved, resumable **sessions** — each with its own isolated Knowledge Base, agent, uploads, and message history — listed by name in a left sidebar, grouped per user.

**Architecture:** A new `public.sessions` table (Postgres, via PostgREST) records each session (user, name, kb_id, agent_id, powabase_session_id). A `SessionService` provisions a KB + agent per session and does CRUD via a set of PostgREST methods on `PowabaseClient`. `/chat` and `/ingest/file` take a `session_id` and route to that session's own agent/KB. New `/sessions` endpoints create/list sessions and load a session's messages for resume. The frontend gains a left sidebar. Terminology: **session** = our saved conversation; `powabase_session_id` = Powabase's internal message-thread id.

**Tech Stack:** Python 3.9 (env has 3.9.6; new files with module-level `X | None` need `from __future__ import annotations`), FastAPI, httpx, pytest + respx. Plain HTML/JS/CSS frontend, no build step.

## Global Constraints

- **Isolation:** a session's agent is linked only to that session's KB; uploads in a session go only to that KB. One session's (or user's) documents must never surface in another. (Phase 2 will additionally link a shared general KB into each session's agent — out of scope here.)
- Powabase resource names are deterministic from the session's uuid: KB `session-<id>-kb`, agent `session-<id>-agent`.
- A **user** is just a slug (lowercased, trimmed, non-alphanumeric runs collapsed to `-`); it owns no Powabase resources, only groups sessions in the table.
- The Service Role key stays server-side; the frontend talks only to our backend and never receives kb_id/agent_id/powabase_session_id.
- The `public.sessions` table has RLS enabled with no policies (backend service-role access only).
- New files using `X | None` at module/class level start with `from __future__ import annotations`.
- Tests use faked HTTP (respx for the client; fakes for services). End-to-end isolation is verified manually in the final task.
- Keep the suite green between tasks: the new session stack is added additively (Tasks 1–4), routes switch to it (Tasks 5–6), then profile code is removed (Task 7).

---

### Task 1: PowabaseClient — PostgREST session methods + messages + migration SQL

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Create: `backend/migrations/001_create_sessions.sql`
- Test: `backend/tests/unit/test_powabase_client_sessions.py`

**Interfaces:**
- Produces on `PowabaseClient`: `insert_session(row: dict) -> dict`, `list_sessions(user_slug: str) -> list[dict]`, `get_session_row(session_id: str) -> dict | None`, `update_session(session_id: str, fields: dict) -> None`, `get_session_messages(powabase_session_id: str) -> dict`. Consumed by `SessionService` (Task 2) and the routes.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_powabase_client_sessions.py
import httpx
import respx

from app.clients.powabase_client import PowabaseClient

BASE_URL = "https://demo.p.powabase.ai"


@respx.mock
def test_insert_session_returns_created_row():
    respx.post(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(201, json=[{"id": "s1", "name": "New session"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    row = client.insert_session({"id": "s1", "user_slug": "alice", "name": "New session"})

    assert row == {"id": "s1", "name": "New session"}


@respx.mock
def test_list_sessions_filters_by_user_and_orders():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "name": "A", "updated_at": "t"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    rows = client.list_sessions("alice")

    assert rows == [{"id": "s1", "name": "A", "updated_at": "t"}]
    request = route.calls.last.request
    assert request.url.params["user_slug"] == "eq.alice"
    assert request.url.params["order"] == "updated_at.desc"


@respx.mock
def test_get_session_row_returns_first_or_none():
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "agent_id": "a1"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_row("s1") == {"id": "s1", "agent_id": "a1"}


@respx.mock
def test_get_session_row_returns_none_when_empty():
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_row("missing") is None


@respx.mock
def test_update_session_patches_by_id():
    route = respx.patch(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(204)
    )
    client = PowabaseClient(BASE_URL, "k")

    client.update_session("s1", {"name": "Renamed"})

    assert route.calls.last.request.url.params["id"] == "eq.s1"


@respx.mock
def test_get_session_messages_calls_api():
    respx.get(f"{BASE_URL}/api/sessions/ps1/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_messages("ps1") == {"messages": []}
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `pytest tests/unit/test_powabase_client_sessions.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'insert_session'`).

- [ ] **Step 3: Add the methods to `backend/app/clients/powabase_client.py`**

Insert these methods into the `PowabaseClient` class, just before the `# Provider keys` section:

```python
    # Sessions table (PostgREST) -------------------------------------------

    def insert_session(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/sessions",
            json=row,
            headers={"Prefer": "return=representation"},
        )
        self._raise_for_status(response)
        created = response.json()
        return created[0] if isinstance(created, list) else created

    def list_sessions(self, user_slug: str) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={"user_slug": f"eq.{user_slug}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def get_session_row(self, session_id: str):
        response = self._client.get(
            "/rest/v1/sessions", params={"id": f"eq.{session_id}"}
        )
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_session(self, session_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/sessions", params={"id": f"eq.{session_id}"}, json=fields
        )
        self._raise_for_status(response)

    def get_session_messages(self, powabase_session_id: str) -> dict:
        response = self._client.get(f"/api/sessions/{powabase_session_id}/messages")
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Create `backend/migrations/001_create_sessions.sql`**

```sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_slug text not null,
  name text not null,
  kb_id text not null,
  agent_id text not null,
  powabase_session_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists sessions_user_updated_idx
  on public.sessions (user_slug, updated_at desc);
alter table public.sessions enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/unit/test_powabase_client_sessions.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/clients/powabase_client.py backend/migrations/001_create_sessions.sql backend/tests/unit/test_powabase_client_sessions.py
git commit -m "feat: add PostgREST session-row methods and sessions migration"
```

---

### Task 2: SessionService — provision + CRUD

**Files:**
- Create: `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_session_service.py`

**Interfaces:**
- Consumes: a duck-typed client with `create_knowledge_base`, `create_agent`, `link_kb_to_agent`, `insert_session`, `list_sessions`, `get_session_row`, `update_session` (matches `PowabaseClient`).
- Produces: `slugify(name) -> str`; `SessionService(client, model)` with `.create_session(user, name=None) -> dict` (returns the created row), `.list(user) -> list[dict]` (`{id, name, updated_at}`, raises `ValueError` on empty user slug), `.get(session_id) -> dict | None`, `.touch(session_id, **fields) -> None` (PATCHes with `updated_at` set to now). `get_session_service(request) -> SessionService`. Consumed by Tasks 3–6.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_session_service.py
import pytest

from app.services.session_service import SessionService, slugify


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.created_kbs = []
        self.created_agents = []
        self.links = []
        self.inserted = []
        self.updated = []

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
        self.created_kbs.append(kb)
        return kb

    def create_agent(self, name, model, system_prompt):
        agent = {"id": f"agent-{name}", "name": name}
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.links.append((agent_id, kb_id))

    def insert_session(self, row):
        self.inserted.append(row)
        return row

    def list_sessions(self, user_slug):
        return [r for r in self.rows if r["user_slug"] == user_slug]

    def get_session_row(self, session_id):
        return next((r for r in self.rows if r["id"] == session_id), None)

    def update_session(self, session_id, fields):
        self.updated.append((session_id, fields))


def test_slugify_normalizes():
    assert slugify("Alice Smith!") == "alice-smith"


def test_create_session_provisions_and_inserts():
    client = FakeClient()
    service = SessionService(client, model="m")

    row = service.create_session("Alice", name="Taxes")

    assert row["user_slug"] == "alice"
    assert row["name"] == "Taxes"
    # KB + agent named from the row id, agent linked to the session KB
    assert client.created_kbs[0]["name"] == f"session-{row['id']}-kb"
    assert client.created_agents[0]["name"] == f"session-{row['id']}-agent"
    assert client.links == [(row["agent_id"], row["kb_id"])]
    assert client.inserted and client.inserted[0]["id"] == row["id"]


def test_create_session_defaults_name():
    service = SessionService(FakeClient(), model="m")
    row = service.create_session("alice")
    assert row["name"] == "New session"


def test_create_session_rejects_empty_user():
    service = SessionService(FakeClient(), model="m")
    with pytest.raises(ValueError):
        service.create_session("!!!")


def test_list_returns_summaries_for_user():
    client = FakeClient(
        rows=[
            {"id": "s1", "user_slug": "alice", "name": "A", "updated_at": "t1"},
            {"id": "s2", "user_slug": "bob", "name": "B", "updated_at": "t2"},
        ]
    )
    service = SessionService(client, model="m")

    result = service.list("alice")

    assert result == [{"id": "s1", "name": "A", "updated_at": "t1"}]


def test_touch_sets_updated_at_and_patches():
    client = FakeClient()
    service = SessionService(client, model="m")

    service.touch("s1", name="Renamed")

    session_id, fields = client.updated[0]
    assert session_id == "s1"
    assert fields["name"] == "Renamed"
    assert "updated_at" in fields
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_session_service.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.session_service'`).

- [ ] **Step 3: Write `backend/app/services/session_service.py`**

```python
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import Request

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)
DEFAULT_NAME = "New session"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def create_session(self, user: str, name: str | None = None) -> dict:
        user_slug = slugify(user)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")

        session_id = str(uuid.uuid4())
        kb = self.client.create_knowledge_base(
            f"session-{session_id}-kb", description=f"Documents for session {session_id}"
        )
        agent = self.client.create_agent(
            f"session-{session_id}-agent", model=self.model, system_prompt=SYSTEM_PROMPT
        )
        self.client.link_kb_to_agent(agent["id"], kb["id"])

        row = {
            "id": session_id,
            "user_slug": user_slug,
            "name": name or DEFAULT_NAME,
            "kb_id": kb["id"],
            "agent_id": agent["id"],
        }
        return self.client.insert_session(row)

    def list(self, user: str) -> list:
        user_slug = slugify(user)
        if not user_slug:
            raise ValueError("User name must contain at least one letter or number")
        rows = self.client.list_sessions(user_slug)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at")}
            for r in rows
        ]

    def get(self, session_id: str):
        return self.client.get_session_row(session_id)

    def touch(self, session_id: str, **fields) -> None:
        fields["updated_at"] = _now_iso()
        self.client.update_session(session_id, fields)


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency returning the shared SessionService created at startup."""
    return request.app.state.session_service
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_session_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/session_service.py backend/tests/unit/test_session_service.py
git commit -m "feat: add SessionService (per-session KB/agent provisioning + CRUD)"
```

---

### Task 3: Wire SessionService into app startup

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Produces: `app.state.session_service` (a `SessionService`), available to routes. (`app.state.profile_service` is still created here; it is removed in Task 7.)

- [ ] **Step 1: Update the lifespan test**

Add a session-service assertion to the success test in `backend/tests/unit/test_main_lifespan.py` — change the `with TestClient(app)` block of `test_app_starts_when_powabase_reachable` to also assert:

```python
        assert isinstance(app.state.session_service, main_module.SessionService)
```

(Leave the existing `profile_service` / `powabase_client` assertions in place.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: FAIL (`AttributeError: module 'app.main' has no attribute 'SessionService'`).

- [ ] **Step 3: Update `backend/app/main.py`**

Add the import (next to the ProfileService import):

```python
from app.services.session_service import SessionService
```

And in `lifespan`, after `app.state.profile_service = ...`, add:

```python
        app.state.session_service = SessionService(client, settings.powabase_agent_model)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/unit/test_main_lifespan.py
git commit -m "feat: provision SessionService at startup"
```

---

### Task 4: Schemas + /sessions routes (create, list, messages)

**Files:**
- Modify: `backend/app/models/schemas.py`
- Create: `backend/app/api/routes/sessions.py`
- Modify: `backend/app/main.py` (register the sessions router)
- Test: `backend/tests/unit/test_routes_sessions.py`

**Interfaces:**
- Consumes: `get_session_service` (Task 2), `get_powabase_client`.
- Produces: `POST /sessions`, `GET /sessions`, `GET /sessions/{id}/messages`. New schemas `SessionCreateRequest`, `SessionResponse`, `SessionSummary`, `ChatMessage`, `MessagesResponse`.

- [ ] **Step 1: Add schemas to `backend/app/models/schemas.py`**

Append:

```python
class SessionCreateRequest(BaseModel):
    user: str = Field(..., min_length=1)
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str


class SessionSummary(BaseModel):
    id: str
    name: str
    updated_at: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    text: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class MessagesResponse(BaseModel):
    messages: list[ChatMessage]
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/unit/test_routes_sessions.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import sessions as sessions_route
from app.clients.powabase_client import get_powabase_client
from app.services.session_service import get_session_service


class FakeSessionService:
    def create_session(self, user, name=None):
        return {"id": "s1", "name": name or "New session"}

    def list(self, user):
        return [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]

    def get(self, session_id):
        if session_id == "missing":
            return None
        return {"id": session_id, "powabase_session_id": "ps1"}


class FakeClient:
    def get_session_messages(self, ps):
        return {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello", "citations": [{"key": "1"}]},
            ]
        }


def build_app():
    app = FastAPI()
    app.include_router(sessions_route.router)
    app.dependency_overrides[get_session_service] = lambda: FakeSessionService()
    app.dependency_overrides[get_powabase_client] = lambda: FakeClient()
    return app


def test_create_session_returns_id_and_name():
    r = TestClient(build_app()).post("/sessions", json={"user": "alice", "name": "Taxes"})
    assert r.status_code == 200
    assert r.json() == {"id": "s1", "name": "Taxes"}


def test_create_session_requires_user():
    r = TestClient(build_app()).post("/sessions", json={"name": "Taxes"})
    assert r.status_code == 422


def test_list_sessions_for_user():
    r = TestClient(build_app()).get("/sessions", params={"user": "alice"})
    assert r.status_code == 200
    assert r.json() == [{"id": "s1", "name": "Taxes", "updated_at": "t1"}]


def test_messages_formats_roles_and_citations():
    r = TestClient(build_app()).get("/sessions/s1/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"][0] == {"role": "user", "text": "hi", "citations": []}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["text"] == "hello"
    assert body["messages"][1]["citations"] == [{"key": "1"}]


def test_messages_404_for_missing_session():
    r = TestClient(build_app()).get("/sessions/missing/messages")
    assert r.status_code == 404
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/unit/test_routes_sessions.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.api.routes.sessions'`).

- [ ] **Step 4: Write `backend/app/api/routes/sessions.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import (
    ChatMessage,
    MessagesResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionSummary,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: SessionCreateRequest,
    sessions: SessionService = Depends(get_session_service),
):
    try:
        row = await run_in_threadpool(sessions.create_session, req.user, req.name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SessionResponse(id=row["id"], name=row["name"])


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    user: str,
    sessions: SessionService = Depends(get_session_service),
):
    try:
        return await run_in_threadpool(sessions.list, user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def session_messages(
    session_id: str,
    sessions: SessionService = Depends(get_session_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(sessions.get, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    powabase_session_id = row.get("powabase_session_id")
    if not powabase_session_id:
        return MessagesResponse(messages=[])
    try:
        raw = await run_in_threadpool(client.get_session_messages, powabase_session_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return MessagesResponse(messages=_format_messages(raw))


def _format_messages(raw) -> list:
    # Powabase's session-messages shape is verified live in the final task; this
    # defensively handles a {"messages": [...]} or bare-list payload of
    # {role, content|text, citations?} items.
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    formatted = []
    for item in items:
        role = item.get("role", "assistant")
        text = item.get("content") or item.get("text") or ""
        citations = item.get("citations") or []
        formatted.append(ChatMessage(role=role, text=text, citations=citations))
    return formatted
```

- [ ] **Step 5: Register the router in `backend/app/main.py`**

Add the import:

```python
from app.api.routes.sessions import router as sessions_router
```

And in `create_app()`, after the other `include_router` calls:

```python
    app.include_router(sessions_router)
```

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/unit/test_routes_sessions.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/schemas.py backend/app/api/routes/sessions.py backend/app/main.py backend/tests/unit/test_routes_sessions.py
git commit -m "feat: add /sessions create, list, and messages routes"
```

---

### Task 5: /chat session-scoped

**Files:**
- Modify: `backend/app/models/schemas.py`
- Modify: `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_routes_chat.py` (rewrite)

**Interfaces:**
- Consumes: `get_session_service`, `ChatService`.
- Produces: `POST /chat` now takes `{session_id, query}`, runs that session's agent, threads/saves `powabase_session_id`, auto-names a default-named session from the first message, and bumps `updated_at`. `ChatRequest` becomes `{session_id, query}`; `ChatResponse` becomes `{answer, citations}`.

- [ ] **Step 1: Update `ChatRequest` / `ChatResponse` in `backend/app/models/schemas.py`**

Replace the `ChatRequest` and `ChatResponse` classes with:

```python
class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: Rewrite the chat route tests**

```python
# backend/tests/unit/test_routes_chat.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.services.session_service import get_session_service


class FakeSessionService:
    def __init__(self):
        self.touched = []
        self.row = {"id": "s1", "agent_id": "agent-1", "name": "New session",
                    "powabase_session_id": None}

    def get(self, session_id):
        return None if session_id == "missing" else self.row

    def touch(self, session_id, **fields):
        self.touched.append((session_id, fields))


class FakeChatService:
    def __init__(self, client, agent_id):
        assert agent_id == "agent-1"

    def ask(self, query, session_id=None):
        return {"answer": "42", "session_id": "ps-new", "citations": []}


def build_app(session_service):
    app = FastAPI()
    app.include_router(chat_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: session_service
    return app


def post(client, body):
    return client.post("/chat", json=body)


def test_chat_routes_to_session_agent_and_returns_answer(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()

    response = post(TestClient(build_app(svc)), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "citations": []}


def test_chat_saves_powabase_session_and_autonames_on_first_turn(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()

    post(TestClient(build_app(svc)), {"session_id": "s1", "query": "What are my taxes?"})

    session_id, fields = svc.touched[0]
    assert session_id == "s1"
    assert fields["powabase_session_id"] == "ps-new"
    assert fields["name"] == "What are my taxes?"
    assert "updated_at" not in fields  # touch() adds updated_at itself


def test_chat_404_for_missing_session(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "missing", "query": "hi"})

    assert response.status_code == 404


def test_chat_requires_session_id(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app(FakeSessionService())), {"query": "hi"})

    assert response.status_code == 422


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    class Insufficient(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", Insufficient)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_503_when_model_busy(monkeypatch):
    class Busy(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ModelBusyError("The model is busy right now. Please wait a few seconds and try again.")

    monkeypatch.setattr(chat_route, "ChatService", Busy)

    response = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert response.status_code == 503
    assert "try again" in response.json()["detail"].lower()
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: FAIL (route still uses `profiles`/`req.profile`; new tests reference `get_session_service`).

- [ ] **Step 4: Rewrite `backend/app/api/routes/chat.py`**

```python
from fastapi import APIRouter, Depends, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.session_service import DEFAULT_NAME, SessionService, get_session_service

router = APIRouter(prefix="/chat", tags=["chat"])


def _title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    row = sessions.get(req.session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = ChatService(client, row["agent_id"])
    powabase_session_id = row.get("powabase_session_id")
    try:
        result = service.ask(req.query, session_id=powabase_session_id)
    except ModelBusyError as e:
        raise HTTPException(status_code=503, detail=e.message)
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)
    except ProviderKeyError as e:
        raise HTTPException(
            status_code=424,
            detail=f"{e.message} (configure a provider key in Powabase Studio -> Settings -> LLM Provider Keys)",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    updates: dict = {}
    if not powabase_session_id and result.get("session_id"):
        updates["powabase_session_id"] = result["session_id"]
        if row.get("name") == DEFAULT_NAME:
            updates["name"] = _title_from(req.query)
    sessions.touch(req.session_id, **updates)

    return ChatResponse(answer=result["answer"], citations=result["citations"])
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/schemas.py backend/app/api/routes/chat.py backend/tests/unit/test_routes_chat.py
git commit -m "feat: scope chat to a session; persist powabase session + auto-name"
```

---

### Task 6: /ingest/file session-scoped

**Files:**
- Modify: `backend/app/api/routes/ingest.py`
- Test: `backend/tests/unit/test_routes_ingest.py` (rewrite)

**Interfaces:**
- Consumes: `get_session_service`, `IngestService`.
- Produces: `POST /ingest/file` takes a `session_id` form field and ingests into that session's KB.

- [ ] **Step 1: Rewrite the ingest route tests**

```python
# backend/tests/unit/test_routes_ingest.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ingest as ingest_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.session_service import get_session_service


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


class FakeSessionService:
    def get(self, session_id):
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_routes_ingest.py -v`
Expected: FAIL (route still uses `profile`/`get_profile_service`).

- [ ] **Step 3: Rewrite `backend/app/api/routes/ingest.py`**

```python
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import IngestResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    content = await file.read()
    settings = get_settings()
    row = await run_in_threadpool(sessions.get, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = IngestService(
        client,
        row["kb_id"],
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )
    try:
        result = await run_in_threadpool(service.ingest_pdf, file.filename, content)
        return IngestResponse(**result)
    except AttentionRequiredError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Source {e.source_id} needs OCR re-extraction (low-quality/scanned PDF). "
                f"Call POST /api/sources/{e.source_id}/reextract with an OCR extraction_model."
            ),
        )
    except (ExtractionFailedError, IndexingFailedError) as e:
        raise HTTPException(status_code=500, detail=e.message)
    except IngestTimeoutError as e:
        return JSONResponse(
            status_code=202,
            content=IngestResponse(source_id=e.source_id, status=e.status).model_dump(),
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_routes_ingest.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/ingest.py backend/tests/unit/test_routes_ingest.py
git commit -m "feat: scope PDF ingestion to a session's knowledge base"
```

---

### Task 7: Remove the superseded profile code

**Files:**
- Delete: `backend/app/services/profile_service.py`
- Delete: `backend/app/api/routes/profile.py`
- Delete: `backend/tests/unit/test_profile_service.py`
- Delete: `backend/tests/unit/test_routes_profile.py`
- Modify: `backend/app/models/schemas.py` (drop `ProfileRequest`/`ProfileResponse`)
- Modify: `backend/app/main.py` (drop profile import, router, and `app.state.profile_service`)
- Modify: `backend/tests/unit/test_main_lifespan.py` (drop the `profile_service` assertion)

**Interfaces:**
- Produces: no profile code remains; `/chat` and `/ingest/file` (session-scoped) and `/sessions` are the surface.

- [ ] **Step 1: Delete the profile modules and their tests**

```bash
git rm backend/app/services/profile_service.py backend/app/api/routes/profile.py \
       backend/tests/unit/test_profile_service.py backend/tests/unit/test_routes_profile.py
```

- [ ] **Step 2: Remove `ProfileRequest`/`ProfileResponse` from `backend/app/models/schemas.py`**

Delete those two classes (leave the session/chat/ingest schemas intact).

- [ ] **Step 3: Update `backend/app/main.py`**

Remove the `from app.api.routes.profile import router as profile_router` import, the `from app.services.profile_service import ProfileService` import, the `app.include_router(profile_router)` line, and the `app.state.profile_service = ...` line in `lifespan`.

- [ ] **Step 4: Update `backend/tests/unit/test_main_lifespan.py`**

Remove the `assert isinstance(app.state.profile_service, main_module.ProfileService)` line from the success test (keep the `session_service` and `powabase_client` assertions).

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all PASS — no import errors from the deletions, no lingering references to profile code. (If any test or module still imports profile code, fix that reference.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove superseded per-profile code (replaced by sessions)"
```

---

### Task 8: Frontend — session sidebar

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`

**Interfaces:**
- Consumes: `POST /sessions`, `GET /sessions?user=`, `GET /sessions/{id}/messages`, `POST /chat` (`{session_id, query}`), `POST /ingest/file` (`session_id` + file).
- Produces: nothing downstream — top of the stack.

- [ ] **Step 1: Replace `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>RAG Chat</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <div class="app">
      <aside class="sidebar" id="sidebar">
        <div class="sidebar__head">
          <label class="sidebar__label" for="user-input">User</label>
          <input
            type="text"
            id="user-input"
            class="sidebar__user"
            placeholder="Type a name, Enter…"
            autocomplete="off"
            aria-label="User name"
          />
        </div>
        <button id="new-session" class="new-session" disabled>+ New session</button>
        <ul class="session-list" id="session-list" aria-label="Sessions"></ul>
        <p class="sidebar__status" id="sidebar-status"></p>
      </aside>

      <div class="main">
        <header class="topbar">
          <button id="sidebar-toggle" class="icon-btn topbar__toggle" aria-label="Toggle sessions">☰</button>
          <span class="topbar__mark" aria-hidden="true"></span>
          <span class="topbar__title" id="active-title">RAG Chat</span>
        </header>

        <main class="thread" id="messages" aria-live="polite">
          <div class="empty-state" id="empty-state">Pick or create a session to start.</div>
        </main>

        <footer class="composer-wrap">
          <div class="attachment-chip" id="attachment-chip" hidden>
            <span id="attachment-name"></span>
            <span class="attachment-chip__status" id="attachment-status"></span>
          </div>
          <form id="chat-form" class="composer">
            <button type="button" id="attach-button" class="icon-btn" aria-label="Attach a PDF" title="Attach a PDF">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a5.5 5.5 0 0 1-7.78-7.78l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a1.5 1.5 0 0 1-2.12-2.12l8.49-8.48" />
              </svg>
            </button>
            <input type="file" id="file-input" accept="application/pdf" hidden />
            <input
              type="text"
              id="chat-input"
              class="composer__input"
              placeholder="Ask a question about this session's documents…"
              aria-label="Message"
              autocomplete="off"
            />
            <button type="submit" id="send-button" class="icon-btn icon-btn--accent" aria-label="Send message">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </form>
        </footer>
      </div>
    </div>
    <script src="/app.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Replace the layout-level CSS in `frontend/styles.css`**

Change the `.app` rule (currently `display: flex; flex-direction: column; height: 100vh;`) to a two-column layout, and append the sidebar styles. Replace the existing `.app` block with:

```css
.app {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.main {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
```

Then append at the end of the file:

```css
.sidebar {
  flex: none;
  width: 16rem;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--bg-subtle);
  padding: 0.75rem;
  gap: 0.6rem;
}

.sidebar__head {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.sidebar__label {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.sidebar__user {
  border: 1px solid var(--border);
  border-radius: 0.6rem;
  padding: 0.4rem 0.6rem;
  font: inherit;
  font-size: 0.9rem;
  background: var(--bg);
  color: var(--text);
}

.sidebar__user:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.new-session {
  border: 1px solid var(--border);
  border-radius: 0.6rem;
  padding: 0.45rem 0.6rem;
  background: var(--bg);
  color: var(--text);
  font: inherit;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.new-session:hover:not(:disabled) {
  border-color: var(--accent-2);
  color: var(--accent-2);
}

.new-session:disabled {
  opacity: 0.5;
  cursor: default;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.session-list li {
  padding: 0.5rem 0.6rem;
  border-radius: 0.5rem;
  font-size: 0.88rem;
  color: var(--text);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-list li:hover {
  background: #ece9ff;
}

.session-list li.active {
  background: var(--gradient);
  color: var(--accent-contrast);
}

.sidebar__status {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
  min-height: 1em;
}

.sidebar__status[data-state="error"] {
  color: var(--error);
}

.topbar__toggle {
  display: none;
  margin-right: 0.25rem;
  font-size: 1.1rem;
  line-height: 1;
}

@media (max-width: 640px) {
  .sidebar {
    position: fixed;
    z-index: 20;
    inset: 0 auto 0 0;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .topbar__toggle {
    display: inline-flex;
  }
}
```

- [ ] **Step 3: Replace `frontend/app.js`**

```javascript
const userInput = document.getElementById("user-input");
const sidebarStatus = document.getElementById("sidebar-status");
const sessionList = document.getElementById("session-list");
const newSessionButton = document.getElementById("new-session");
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebar-toggle");
const activeTitle = document.getElementById("active-title");
const attachButton = document.getElementById("attach-button");
const fileInput = document.getElementById("file-input");
const attachmentChip = document.getElementById("attachment-chip");
const attachmentName = document.getElementById("attachment-name");
const attachmentStatus = document.getElementById("attachment-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = document.getElementById("send-button");
const messages = document.getElementById("messages");

const USER_KEY = "rag-chat-user";
let currentUser = null;
let currentSessionId = null;
let isAsking = false;

init();

function init() {
  setComposerEnabled(false);
  const saved = localStorage.getItem(USER_KEY);
  if (saved) {
    userInput.value = saved;
    switchUser(saved);
  }
  userInput.addEventListener("change", () => switchUser(userInput.value));
  userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchUser(userInput.value);
    }
  });
  newSessionButton.addEventListener("click", createSession);
  sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
}

async function switchUser(rawName) {
  const name = rawName.trim();
  currentSessionId = null;
  clearThread("Pick or create a session to start.");
  setComposerEnabled(false);
  activeTitle.textContent = "RAG Chat";
  if (!name) {
    currentUser = null;
    newSessionButton.disabled = true;
    sessionList.innerHTML = "";
    setSidebarStatus("Enter a user name to start", null);
    return;
  }
  currentUser = name;
  localStorage.setItem(USER_KEY, name);
  newSessionButton.disabled = false;
  await loadSessions();
}

async function loadSessions() {
  setSidebarStatus("Loading sessions…", null);
  try {
    const response = await fetch(`/sessions?user=${encodeURIComponent(currentUser)}`);
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(body.detail || response.statusText, "error");
      return;
    }
    renderSessionList(body);
    setSidebarStatus(body.length ? "" : "No sessions yet — create one.", null);
  } catch (err) {
    setSidebarStatus(err.message, "error");
  }
}

function renderSessionList(sessions) {
  sessionList.innerHTML = "";
  sessions.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s.name;
    li.dataset.id = s.id;
    if (s.id === currentSessionId) li.classList.add("active");
    li.addEventListener("click", () => openSession(s.id, s.name));
    sessionList.appendChild(li);
  });
}

async function createSession() {
  if (!currentUser) return;
  try {
    const response = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser }),
    });
    const body = await response.json();
    if (!response.ok) {
      setSidebarStatus(body.detail || response.statusText, "error");
      return;
    }
    await loadSessions();
    openSession(body.id, body.name);
  } catch (err) {
    setSidebarStatus(err.message, "error");
  }
}

async function openSession(id, name) {
  currentSessionId = id;
  activeTitle.textContent = name;
  attachmentChip.hidden = true;
  sidebar.classList.remove("open");
  markActive();
  setComposerEnabled(true);
  clearThread("Upload a PDF, then ask about it — or just ask.");

  try {
    const response = await fetch(`/sessions/${id}/messages`);
    const body = await response.json();
    if (response.ok && body.messages && body.messages.length) {
      messages.innerHTML = "";
      body.messages.forEach((m) => {
        if (m.role === "user") appendMessage("user", null, m.text);
        else appendMessage("assistant", "AI", m.text, m.citations);
      });
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  }
}

function markActive() {
  Array.from(sessionList.children).forEach((li) => {
    li.classList.toggle("active", li.dataset.id === currentSessionId);
  });
}

attachButton.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!currentSessionId) {
    fileInput.value = "";
    return;
  }
  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", currentSessionId);
  try {
    const response = await fetch("/ingest/file", { method: "POST", body: formData });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok || response.status === 202) {
      showAttachment(file.name, body.status, body.status === "indexed" ? "ok" : null);
    } else {
      showAttachment(file.name, body.detail || response.statusText, "error");
    }
  } catch (err) {
    showAttachment(file.name, err.message, "error");
  }
  fileInput.value = "";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isAsking) return;
  const query = chatInput.value.trim();
  if (!query) return;
  if (!currentSessionId) {
    appendMessage("error", "!", "Pick or create a session first.");
    return;
  }
  appendMessage("user", null, query);
  chatInput.value = "";

  isAsking = true;
  sendButton.disabled = true;
  const thinking = appendThinking();
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId, query }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      appendMessage("assistant", "AI", body.answer, body.citations);
      loadSessions(); // refresh titles/order (first message names the session)
    } else {
      appendMessage("error", "!", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  } finally {
    thinking.remove();
    isAsking = false;
    sendButton.disabled = false;
  }
});

function setComposerEnabled(enabled) {
  chatInput.disabled = !enabled;
  sendButton.disabled = !enabled;
  attachButton.disabled = !enabled;
}

function setSidebarStatus(text, state) {
  sidebarStatus.textContent = text;
  if (state) sidebarStatus.dataset.state = state;
  else delete sidebarStatus.dataset.state;
}

function clearThread(note) {
  messages.innerHTML = "";
  const el = document.createElement("div");
  el.className = "empty-state";
  el.textContent = note;
  messages.appendChild(el);
}

function showAttachment(name, statusText, state) {
  attachmentChip.hidden = false;
  attachmentName.textContent = name;
  attachmentStatus.textContent = statusText;
  if (state) attachmentStatus.dataset.state = state;
  else delete attachmentStatus.dataset.state;
}

function appendThinking() {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) existingEmpty.remove();
  const row = document.createElement("div");
  row.className = "row row--assistant";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = "AI";
  row.appendChild(avatar);
  const content = document.createElement("div");
  content.className = "content";
  const thinking = document.createElement("div");
  thinking.className = "thinking";
  thinking.setAttribute("aria-label", "Thinking");
  for (let i = 0; i < 3; i++) thinking.appendChild(document.createElement("span"));
  content.appendChild(thinking);
  row.appendChild(content);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function appendMessage(role, avatarText, text, citations) {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) existingEmpty.remove();

  const row = document.createElement("div");
  row.className = `row row--${role}`;

  if (role === "user") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const p = document.createElement("p");
    p.textContent = text;
    bubble.appendChild(p);
    row.appendChild(bubble);
  } else if (role === "system") {
    const content = document.createElement("div");
    content.className = "content";
    const p = document.createElement("p");
    p.textContent = text;
    content.appendChild(p);
    row.appendChild(content);
  } else {
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = avatarText;
    row.appendChild(avatar);
    const content = document.createElement("div");
    content.className = "content";
    const p = document.createElement("p");
    p.textContent = text;
    content.appendChild(p);
    if (citations && citations.length > 0) {
      content.appendChild(buildReferenceList(citations));
    }
    row.appendChild(content);
  }

  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function buildReferenceList(citations) {
  const list = document.createElement("ul");
  list.className = "refs";
  citations.forEach((citation, index) => {
    const item = document.createElement("li");
    if (citation.text_excerpt) item.title = citation.text_excerpt;
    const tag = document.createElement("span");
    tag.className = "ref__tag";
    tag.textContent = `[${citation.key || index + 1}]`;
    item.appendChild(tag);
    const name = citation.source_name || citation.source_id || "source";
    item.appendChild(document.createTextNode(` ${name}`));
    list.appendChild(item);
  });
  return list;
}
```

- [ ] **Step 4: Verify syntax and backend suite**

Run: `node -c frontend/app.js && echo "app.js OK"` (expect `app.js OK`).
Run (from `backend/`): `pytest -q` (expect all green — frontend change doesn't affect backend).

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/app.js
git commit -m "feat: session sidebar UI (create, switch, resume, per-session upload)"
```

---

### Task 9: README + migration doc + manual verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: docs + a live isolation/resume proof.

- [ ] **Step 1: Update `README.md`**

Replace the "Profiles & data isolation" section with a "Sessions" section and add the migration step:

```markdown
## Sessions & per-session isolation

Type a **user** name in the sidebar, then create **sessions** — saved,
resumable conversations. Each session has its own isolated documents: a PDF
uploaded in one session is never visible to another session or user. Sessions
are listed by name in the left sidebar; click one to resume it. The first
message you send names the session.

**One-time setup:** create the `sessions` table by pasting
`backend/migrations/001_create_sessions.sql` into the Powabase Studio **SQL
Editor** (or running it via the Database URL). This is required before the app
can save sessions.

**Scope note:** still a demonstration of data isolation, not access control —
users are passwordless names. (Admin-curated shared "general knowledge" is
Phase 2.)
```

- [ ] **Step 2: Run the full backend suite**

Run (from `backend/`): `pytest -q`
Expected: all green.

- [ ] **Step 3: Manual isolation + resume proof (live Powabase)**

Prereq: run the migration SQL against the live project first. Then start the app (`uvicorn app.main:app --reload`, `.env` populated) and, in the browser:

- [ ] User `alice`, click **New session**, upload a PDF, ask about it → cited answer; the session gets named from your first message.
- [ ] Click **New session** again, ask about the *first* session's document → not found (second session can't see it).
- [ ] Reopen the first session from the sidebar → its messages are still there and it answers about its document again.
- [ ] Change the user to `bob` → alice's sessions are not listed.
- [ ] Refresh the page as `alice` → sessions still listed (persisted in the table).

Confirm the resume path renders historical messages; if Powabase's session-messages shape differs from the `_format_messages` assumption, adjust that helper and re-verify.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document sessions, the migration step, and verification"
```
