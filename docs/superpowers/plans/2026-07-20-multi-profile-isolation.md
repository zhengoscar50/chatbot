# Multi-Profile Data Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each lightweight profile its own isolated Powabase Knowledge Base and agent, so documents uploaded under one profile are never retrievable by another.

**Architecture:** A new `ProfileService` resolves a typed profile name to its own `{kb_id, agent_id}` via find-or-create against Powabase (deterministic resource names, in-memory cache, per-slug lock), created once at startup and shared on `app.state`. The `/ingest/file` and `/chat` routes gain a `profile` field, resolve it, and run against that profile's KB/agent. A new `/profile` endpoint provisions eagerly on switch. The frontend adds a profile bar; switching clears the thread/session and routes all requests to the new profile.

**Tech Stack:** Python 3.9 (env has 3.9.6; use `from __future__ import annotations` in any new file using `X | None` at module/class level), FastAPI, httpx, pytest. Plain HTML/JS/CSS frontend, no build step.

## Global Constraints

- Data isolation is the goal; security against impersonation is explicitly **out of scope** (no passwords — anyone can pick any profile name).
- Isolation is per-profile Knowledge Base: a profile's agent is linked only to that profile's KB. Never route one profile's agent to another profile's KB.
- The Powabase Service Role key stays server-side only; the frontend talks only to our backend and never receives KB/agent IDs.
- Profile resource names are deterministic from a slug: KB `profile-<slug>-kb`, agent `profile-<slug>-agent`. Slug = lowercase, trimmed, non-alphanumeric runs collapsed to `-`, leading/trailing `-` stripped.
- Every profile's agent uses the model from `POWABASE_AGENT_MODEL`. The OpenRouter provider key remains a project-level Powabase setting (unchanged).
- New files that use `X | None` in a module/class-level signature must start with `from __future__ import annotations` (Python 3.9 constraint).
- Tests use a faked Powabase client (no network). Manual end-to-end isolation check is the final task.

---

### Task 1: ProfileService — slug + find-or-create + cache

**Files:**
- Create: `backend/app/services/profile_service.py`
- Test: `backend/tests/unit/test_profile_service.py`

**Interfaces:**
- Consumes: a duck-typed client with `list_knowledge_bases() -> dict`, `create_knowledge_base(name, description="") -> dict`, `list_agents() -> dict`, `create_agent(name, model, system_prompt) -> dict`, `link_kb_to_agent(agent_id, kb_id) -> dict` (matches `PowabaseClient`).
- Produces: `slugify(name: str) -> str`. `ProfileService(client, model: str)` with `.resolve(name: str) -> {"slug": str, "kb_id": str, "agent_id": str}` (raises `ValueError` if the name slugifies to empty). `get_profile_service(request) -> ProfileService` FastAPI dependency reading `request.app.state.profile_service`. Consumed by Tasks 2–5.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_profile_service.py
import pytest

from app.services.profile_service import ProfileService, slugify


class FakeClient:
    def __init__(self, existing_kbs=None, existing_agents=None):
        self.kbs = list(existing_kbs or [])
        self.agents = list(existing_agents or [])
        self.list_kb_calls = 0
        self.list_agent_calls = 0
        self.created_kbs = []
        self.created_agents = []
        self.links = []

    def list_knowledge_bases(self):
        self.list_kb_calls += 1
        return {"items": self.kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": f"kb-{name}", "name": name}
        self.kbs.append(kb)
        self.created_kbs.append(kb)
        return kb

    def list_agents(self):
        self.list_agent_calls += 1
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt):
        agent = {"id": f"agent-{name}", "name": name}
        self.agents.append(agent)
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.links.append((agent_id, kb_id))


def test_slugify_normalizes_names():
    assert slugify("Alice") == "alice"
    assert slugify("  Bob Smith! ") == "bob-smith"
    assert slugify("a__b--c") == "a-b-c"


def test_resolve_creates_kb_and_agent_when_absent():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    result = service.resolve("Alice")

    assert result == {
        "slug": "alice",
        "kb_id": "kb-profile-alice-kb",
        "agent_id": "agent-profile-alice-agent",
    }
    assert client.created_kbs and client.created_agents
    assert client.links == [("agent-profile-alice-agent", "kb-profile-alice-kb")]


def test_resolve_reuses_existing_resources():
    client = FakeClient(
        existing_kbs=[{"id": "kb-existing", "name": "profile-alice-kb"}],
        existing_agents=[{"id": "agent-existing", "name": "profile-alice-agent"}],
    )
    service = ProfileService(client, model="test-model")

    result = service.resolve("alice")

    assert result["kb_id"] == "kb-existing"
    assert result["agent_id"] == "agent-existing"
    assert client.created_kbs == []
    assert client.created_agents == []
    assert client.links == []


def test_resolve_caches_after_first_call():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    service.resolve("alice")
    service.resolve("alice")

    assert client.list_kb_calls == 1
    assert client.list_agent_calls == 1


def test_resolve_treats_equivalent_names_as_one_profile():
    client = FakeClient()
    service = ProfileService(client, model="test-model")

    first = service.resolve("Alice")
    second = service.resolve("  alice ")

    assert first == second
    assert len(client.created_kbs) == 1


def test_resolve_rejects_names_that_slugify_to_empty():
    service = ProfileService(FakeClient(), model="test-model")

    with pytest.raises(ValueError):
        service.resolve("!!!")
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `pytest tests/unit/test_profile_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.profile_service'`

- [ ] **Step 3: Write `backend/app/services/profile_service.py`**

```python
from __future__ import annotations

import re
import threading

from fastapi import Request

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _find_by_name(items: list, name: str):
    return next((item for item in items if item.get("name") == name), None)


class ProfileService:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._slug_locks: dict = {}

    def _lock_for(self, slug: str) -> threading.Lock:
        with self._cache_lock:
            lock = self._slug_locks.get(slug)
            if lock is None:
                lock = threading.Lock()
                self._slug_locks[slug] = lock
            return lock

    def resolve(self, name: str) -> dict:
        slug = slugify(name)
        if not slug:
            raise ValueError("Profile name must contain at least one letter or number")

        with self._cache_lock:
            cached = self._cache.get(slug)
        if cached is not None:
            return cached

        with self._lock_for(slug):
            with self._cache_lock:
                cached = self._cache.get(slug)
            if cached is not None:
                return cached
            resolved = self._provision(slug)
            with self._cache_lock:
                self._cache[slug] = resolved
            return resolved

    def _provision(self, slug: str) -> dict:
        kb_name = f"profile-{slug}-kb"
        agent_name = f"profile-{slug}-agent"

        kb = _find_by_name(self.client.list_knowledge_bases().get("items", []), kb_name)
        if kb is None:
            kb = self.client.create_knowledge_base(
                kb_name, description=f"Knowledge base for profile {slug}"
            )

        agent = _find_by_name(self.client.list_agents().get("agents", []), agent_name)
        if agent is None:
            agent = self.client.create_agent(
                agent_name, model=self.model, system_prompt=SYSTEM_PROMPT
            )
            self.client.link_kb_to_agent(agent["id"], kb["id"])

        return {"slug": slug, "kb_id": kb["id"], "agent_id": agent["id"]}


def get_profile_service(request: Request) -> "ProfileService":
    """FastAPI dependency returning the shared ProfileService created at startup."""
    return request.app.state.profile_service
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_profile_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/profile_service.py backend/tests/unit/test_profile_service.py
git commit -m "feat: add ProfileService (per-profile KB/agent find-or-create)"
```

---

### Task 2: App startup — create ProfileService, connectivity check

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main_lifespan.py` (rewrite)

**Interfaces:**
- Consumes: `ProfileService` (Task 1), `PowabaseClient`/`PowabaseAPIError`, `get_settings`.
- Produces: `app.state.profile_service` (a `ProfileService`) and `app.state.powabase_client`, both available to routes. Startup validation now a single `client.list_agents()` connectivity check.

- [ ] **Step 1: Rewrite the lifespan tests**

```python
# backend/tests/unit/test_main_lifespan.py
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.clients.powabase_client import PowabaseAPIError
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


def test_app_starts_when_powabase_reachable(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(
        main_module.PowabaseClient, "list_agents", lambda self: {"agents": []}
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert isinstance(app.state.profile_service, main_module.ProfileService)
        assert isinstance(app.state.powabase_client, main_module.PowabaseClient)


def test_app_fails_to_start_when_powabase_unreachable(monkeypatch):
    set_env(monkeypatch)

    def raise_error(self):
        raise PowabaseAPIError(401, {"error": "unauthorized"})

    monkeypatch.setattr(main_module.PowabaseClient, "list_agents", raise_error)

    app = main_module.create_app()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: FAIL (`AttributeError` on `main_module.ProfileService`, and the old `get_knowledge_base`-based lifespan doesn't call `list_agents`).

- [ ] **Step 3: Update `backend/app/main.py`**

Replace the `lifespan` function and imports so the file reads:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import FRONTEND_DIR, get_settings
from app.services.profile_service import ProfileService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    try:
        try:
            client.list_agents()
        except PowabaseAPIError as e:
            raise RuntimeError(f"Powabase is not reachable: {e}") from e
        app.state.powabase_client = client
        app.state.profile_service = ProfileService(client, settings.powabase_agent_model)
        yield
    finally:
        client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Chatbot on Powabase", version="1.0.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(chat_router)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


app = create_app()
```

(The `profile` router is added to `create_app` in Task 3.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/unit/test_main_lifespan.py
git commit -m "feat: provision ProfileService at startup; connectivity-only health check"
```

---

### Task 3: Schemas + `/profile` route

**Files:**
- Modify: `backend/app/models/schemas.py`
- Create: `backend/app/api/routes/profile.py`
- Modify: `backend/app/main.py` (register the profile router)
- Test: `backend/tests/unit/test_routes_profile.py`

**Interfaces:**
- Consumes: `get_profile_service` (Task 1).
- Produces: `ProfileRequest{profile}`, `ProfileResponse{profile, slug}`, and `profile` added to `ChatRequest` (consumed in Task 5). `POST /profile` returning `{profile, slug}`.

- [ ] **Step 1: Update `backend/app/models/schemas.py`**

```python
from typing import Any, Optional

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    source_id: str
    status: str


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    profile: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: Optional[str] = None
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    profile: str = Field(..., min_length=1)


class ProfileResponse(BaseModel):
    profile: str
    slug: str
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/unit/test_routes_profile.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import profile as profile_route
from app.services.profile_service import get_profile_service


class FakeProfileService:
    def resolve(self, name):
        if name.strip() == "":
            raise ValueError("empty")
        return {"slug": "alice", "kb_id": "kb-1", "agent_id": "agent-1"}


def build_app():
    app = FastAPI()
    app.include_router(profile_route.router)
    app.dependency_overrides[get_profile_service] = lambda: FakeProfileService()
    return app


def test_profile_returns_display_name_and_slug():
    response = TestClient(build_app()).post("/profile", json={"profile": "Alice"})

    assert response.status_code == 200
    assert response.json() == {"profile": "Alice", "slug": "alice"}


def test_profile_does_not_leak_resource_ids():
    body = TestClient(build_app()).post("/profile", json={"profile": "Alice"}).json()

    assert "kb_id" not in body
    assert "agent_id" not in body


def test_profile_returns_422_on_invalid_name():
    class RejectingService(FakeProfileService):
        def resolve(self, name):
            raise ValueError("Profile name must contain at least one letter or number")

    app = FastAPI()
    app.include_router(profile_route.router)
    app.dependency_overrides[get_profile_service] = lambda: RejectingService()

    response = TestClient(app).post("/profile", json={"profile": "!!!"})

    assert response.status_code == 422
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/unit/test_routes_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.profile'`

- [ ] **Step 4: Write `backend/app/api/routes/profile.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.models.schemas import ProfileRequest, ProfileResponse
from app.services.profile_service import ProfileService, get_profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("", response_model=ProfileResponse)
async def ensure_profile(
    req: ProfileRequest,
    profiles: ProfileService = Depends(get_profile_service),
):
    try:
        resolved = await run_in_threadpool(profiles.resolve, req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ProfileResponse(profile=req.profile, slug=resolved["slug"])
```

- [ ] **Step 5: Register the router in `backend/app/main.py`**

Add the import near the other route imports:

```python
from app.api.routes.profile import router as profile_router
```

And add this line in `create_app()` after `app.include_router(chat_router)`:

```python
    app.include_router(profile_router)
```

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/unit/test_routes_profile.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/schemas.py backend/app/api/routes/profile.py backend/app/main.py backend/tests/unit/test_routes_profile.py
git commit -m "feat: add /profile route and profile-aware schemas"
```

---

### Task 4: Ingest route — profile-scoped

**Files:**
- Modify: `backend/app/api/routes/ingest.py`
- Test: `backend/tests/unit/test_routes_ingest.py` (rewrite)

**Interfaces:**
- Consumes: `get_profile_service` (Task 1), `IngestService` (unchanged), `get_powabase_client`.
- Produces: `POST /ingest/file` now requires a `profile` form field and ingests into that profile's KB.

- [ ] **Step 1: Rewrite the ingest route tests**

```python
# backend/tests/unit/test_routes_ingest.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ingest as ingest_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.profile_service import get_profile_service


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


class FakeProfileService:
    def resolve(self, name):
        return {"slug": "alice", "kb_id": "kb-1", "agent_id": "agent-1"}


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        assert kb_id == "kb-1"

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


def build_app():
    app = FastAPI()
    app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_profile_service] = lambda: FakeProfileService()
    return app


def upload(client):
    return client.post(
        "/ingest/file",
        data={"profile": "alice"},
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_ingest_file_routes_to_profile_kb_and_returns_indexed(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 200
    assert response.json() == {"source_id": "src-1", "status": "indexed"}


def test_ingest_file_requires_profile(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = TestClient(build_app()).post(
        "/ingest/file",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )

    assert response.status_code == 422


def test_ingest_file_returns_202_on_timeout(monkeypatch):
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
Expected: FAIL (route doesn't accept `profile`, and doesn't resolve via profile service — `assert kb_id == "kb-1"` in the fake would fail with the old `settings.powabase_kb_id`).

- [ ] **Step 3: Update `backend/app/api/routes/ingest.py`**

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
from app.services.profile_service import ProfileService, get_profile_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    profile: str = Form(...),
    file: UploadFile = File(...),
    client: PowabaseClient = Depends(get_powabase_client),
    profiles: ProfileService = Depends(get_profile_service),
):
    content = await file.read()
    settings = get_settings()
    try:
        resolved = await run_in_threadpool(profiles.resolve, profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    service = IngestService(
        client,
        resolved["kb_id"],
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
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/ingest.py backend/tests/unit/test_routes_ingest.py
git commit -m "feat: scope PDF ingestion to the caller's profile KB"
```

---

### Task 5: Chat route — profile-scoped

**Files:**
- Modify: `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_routes_chat.py` (rewrite)

**Interfaces:**
- Consumes: `get_profile_service` (Task 1), `ChatService` (unchanged), `ChatRequest.profile` (Task 3).
- Produces: `POST /chat` now requires `profile` and runs against that profile's agent.

- [ ] **Step 1: Rewrite the chat route tests**

```python
# backend/tests/unit/test_routes_chat.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat as chat_route
from app.clients.powabase_client import get_powabase_client
from app.core.config import get_settings
from app.services.profile_service import get_profile_service


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    get_settings.cache_clear()


class FakeProfileService:
    def resolve(self, name):
        return {"slug": "alice", "kb_id": "kb-1", "agent_id": "agent-1"}


class FakeChatService:
    def __init__(self, client, agent_id):
        assert agent_id == "agent-1"

    def ask(self, query, session_id=None):
        return {"answer": "42", "session_id": "sess-1", "citations": []}


def build_app():
    app = FastAPI()
    app.include_router(chat_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_profile_service] = lambda: FakeProfileService()
    return app


def post(client, body):
    return client.post("/chat", json=body)


def test_chat_routes_to_profile_agent_and_returns_answer(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app()), {"query": "What is the answer?", "profile": "alice"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_requires_profile(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = post(TestClient(build_app()), {"query": "hi"})

    assert response.status_code == 422


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    set_env(monkeypatch)

    class InsufficientService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", InsufficientService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    set_env(monkeypatch)

    class ProviderErrorService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderErrorService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "bad key" in detail
    assert "Powabase Studio" in detail


def test_chat_returns_502_when_agent_run_fails(monkeypatch):
    set_env(monkeypatch)

    class FailedRunService(FakeChatService):
        def ask(self, query, session_id=None):
            raise RuntimeError("litellm.APIError: insufficient OpenRouter credits")

    monkeypatch.setattr(chat_route, "ChatService", FailedRunService)

    response = post(TestClient(build_app()), {"query": "hi", "profile": "alice"})

    assert response.status_code == 502
    assert "insufficient OpenRouter credits" in response.json()["detail"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: FAIL (route ignores `profile`; `FakeChatService.__init__` asserts `agent_id == "agent-1"` but the old route passes `settings.powabase_agent_id`).

- [ ] **Step 3: Update `backend/app/api/routes/chat.py`**

```python
from fastapi import APIRouter, Depends, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import ChatService, InsufficientCreditsError, ProviderKeyError
from app.services.profile_service import ProfileService, get_profile_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    profiles: ProfileService = Depends(get_profile_service),
):
    try:
        resolved = profiles.resolve(req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    service = ChatService(client, resolved["agent_id"])
    try:
        result = service.ask(req.query, session_id=req.session_id)
        return ChatResponse(**result)
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
```

(Note: `get_settings` is no longer imported here — the model/agent now come from the resolved profile.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/chat.py backend/tests/unit/test_routes_chat.py
git commit -m "feat: scope chat to the caller's profile agent"
```

---

### Task 6: Health route + drop obsolete config fields

**Files:**
- Modify: `backend/app/api/routes/health.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/unit/test_routes_health.py` (rewrite)
- Test: `backend/tests/unit/test_config.py` (rewrite)

**Interfaces:**
- Consumes: `get_settings`.
- Produces: `GET /health` → `{status, model}` (no KB/agent IDs). `Settings` no longer has `powabase_kb_id` / `powabase_agent_id`.

Note: this is the last consumer of `powabase_kb_id`/`powabase_agent_id` — Tasks 2/4/5 already stopped using them, so removing the fields here is safe.

- [ ] **Step 1: Rewrite the health route test**

```python
# backend/tests/unit/test_routes_health.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()


def build_app():
    from app.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_reports_status_and_model(monkeypatch):
    set_env(monkeypatch)

    response = TestClient(build_app()).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "model": "gpt-4o-mini"}
```

- [ ] **Step 2: Rewrite the config test**

```python
# backend/tests/unit/test_config.py
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_requires_powabase_credentials(monkeypatch):
    for var in ("POWABASE_BASE_URL", "POWABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_loads_from_environment_with_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")

    settings = Settings(_env_file=None)

    assert settings.powabase_base_url == "https://demo.p.powabase.ai"
    assert settings.powabase_agent_model == "gpt-4o-mini"
    assert settings.poll_interval_seconds == 2.0
    assert not hasattr(settings, "powabase_kb_id")
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/unit/test_routes_health.py tests/unit/test_config.py -v`
Expected: FAIL (health still returns kb_id/agent_id; Settings still has/requires those fields).

- [ ] **Step 4: Update `backend/app/core/config.py`**

Replace the field block so the class reads:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    powabase_base_url: str
    powabase_service_role_key: str
    powabase_agent_model: str = "gpt-4o-mini"

    poll_interval_seconds: float = 2.0
    ingest_max_wait_seconds: float = 60.0
```

(Delete the `powabase_kb_id` and `powabase_agent_id` lines. Leave `FRONTEND_DIR` and `get_settings` unchanged.)

- [ ] **Step 5: Update `backend/app/api/routes/health.py`**

```python
from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    settings = get_settings()
    return {"status": "ok", "model": settings.powabase_agent_model}
```

- [ ] **Step 6: Update `backend/.env.example`**

```
POWABASE_BASE_URL=https://your-project-ref.p.powabase.ai
POWABASE_SERVICE_ROLE_KEY=your-service-role-key
POWABASE_AGENT_MODEL=gpt-4o-mini
POWABASE_PROVIDER_NAME=
POWABASE_PROVIDER_KEY=
```

- [ ] **Step 7: Run the full backend suite**

Run: `pytest -v`
Expected: All tests PASS (profile_service, main_lifespan, routes_profile, routes_ingest, routes_chat, routes_health, config, plus the unchanged sse/powabase_client/ingest_service/chat_service/bootstrap tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes/health.py backend/app/core/config.py backend/.env.example backend/tests/unit/test_routes_health.py backend/tests/unit/test_config.py
git commit -m "feat: slim health to status+model; drop fixed KB/agent config"
```

---

### Task 7: Frontend — profile bar

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`

**Interfaces:**
- Consumes: `POST /profile`, `POST /ingest/file` (now needs `profile`), `POST /chat` (now needs `profile`).
- Produces: nothing consumed downstream — top of the stack.

- [ ] **Step 1: Add the profile bar to `frontend/index.html`**

Insert this block between the `</header>` closing tag and the `<main class="thread" ...>` element:

```html
      <div class="profilebar">
        <label class="profilebar__label" for="profile-input">Profile</label>
        <input
          type="text"
          id="profile-input"
          class="profilebar__input"
          placeholder="Type a name and press Enter…"
          autocomplete="off"
          aria-label="Profile name"
        />
        <span class="profilebar__status" id="profile-status"></span>
      </div>
```

- [ ] **Step 2: Add profile-bar styles to `frontend/styles.css`**

Append:

```css
.profilebar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  max-width: 48rem;
  width: 100%;
  margin: 0 auto;
  padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--border);
}

.profilebar__label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-muted);
}

.profilebar__input {
  flex: 1 1 auto;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 0.6rem;
  padding: 0.4rem 0.65rem;
  font-family: var(--font-sans);
  font-size: 0.9rem;
  color: var(--text);
  background: var(--bg);
}

.profilebar__input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.profilebar__status {
  font-size: 0.78rem;
  color: var(--text-muted);
  white-space: nowrap;
}

.profilebar__status[data-state="ok"] {
  color: var(--ok);
}

.profilebar__status[data-state="error"] {
  color: var(--error);
}
```

- [ ] **Step 3: Rewrite `frontend/app.js` with profile logic**

```javascript
const profileInput = document.getElementById("profile-input");
const profileStatus = document.getElementById("profile-status");
const attachButton = document.getElementById("attach-button");
const fileInput = document.getElementById("file-input");
const attachmentChip = document.getElementById("attachment-chip");
const attachmentName = document.getElementById("attachment-name");
const attachmentStatus = document.getElementById("attachment-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = document.getElementById("send-button");
const messages = document.getElementById("messages");

const PROFILE_KEY = "rag-chat-profile";
let sessionId = null;
let currentProfile = null;

init();

function init() {
  setComposerEnabled(false);
  const saved = localStorage.getItem(PROFILE_KEY);
  if (saved) {
    profileInput.value = saved;
    switchProfile(saved);
  } else {
    setProfileStatus("Enter a profile name to start", null);
  }
  profileInput.addEventListener("change", () => switchProfile(profileInput.value));
  profileInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchProfile(profileInput.value);
    }
  });
}

async function switchProfile(rawName) {
  const name = rawName.trim();
  if (!name) {
    setProfileStatus("Enter a profile name to start", null);
    return;
  }
  // Leaving the old profile: clear its conversation and any attachment.
  clearThread();
  sessionId = null;
  attachmentChip.hidden = true;
  setComposerEnabled(false);
  setProfileStatus(`Setting up ${name}…`, null);

  try {
    const response = await fetch("/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: name }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      currentProfile = name;
      localStorage.setItem(PROFILE_KEY, name);
      setProfileStatus(`Profile: ${name}`, "ok");
      setComposerEnabled(true);
    } else {
      currentProfile = null;
      setProfileStatus(body.detail || response.statusText, "error");
    }
  } catch (err) {
    currentProfile = null;
    setProfileStatus(err.message, "error");
  }
}

attachButton.addEventListener("click", () => {
  fileInput.click();
});

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!currentProfile) {
    setProfileStatus("Enter a profile name first", "error");
    fileInput.value = "";
    return;
  }

  showAttachment(file.name, "Uploading and indexing…", null);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("profile", currentProfile);
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
      if (sessionId !== null) {
        sessionId = null;
        appendMessage("system", null, "New document uploaded — starting a fresh conversation.");
      }
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
  const query = chatInput.value.trim();
  if (!query) return;
  if (!currentProfile) {
    appendMessage("error", "!", "Enter a profile name first.");
    return;
  }
  appendMessage("user", null, query);
  chatInput.value = "";

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, profile: currentProfile, session_id: sessionId }),
    });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok) {
      sessionId = body.session_id;
      appendMessage("assistant", "AI", body.answer, body.citations);
    } else {
      appendMessage("error", "!", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("error", "!", err.message);
  }
});

function setComposerEnabled(enabled) {
  chatInput.disabled = !enabled;
  sendButton.disabled = !enabled;
  attachButton.disabled = !enabled;
}

function setProfileStatus(text, state) {
  profileStatus.textContent = text;
  if (state) {
    profileStatus.dataset.state = state;
  } else {
    delete profileStatus.dataset.state;
  }
}

function clearThread() {
  messages.innerHTML = "";
  const note = document.createElement("div");
  note.className = "empty-state";
  note.textContent = "Upload a PDF, then ask anything about it.";
  messages.appendChild(note);
}

function showAttachment(name, statusText, state) {
  attachmentChip.hidden = false;
  attachmentName.textContent = name;
  attachmentStatus.textContent = statusText;
  if (state) {
    attachmentStatus.dataset.state = state;
  } else {
    delete attachmentStatus.dataset.state;
  }
}

function appendMessage(role, avatarText, text, citations) {
  const existingEmpty = messages.querySelector(".empty-state");
  if (existingEmpty) {
    existingEmpty.remove();
  }

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
    if (citation.text_excerpt) {
      item.title = citation.text_excerpt;
    }

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

- [ ] **Step 4: Verify syntax and that files still serve**

Run: `node -c frontend/app.js && echo "app.js OK"`
Expected: `app.js OK` (no syntax errors).

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/app.js
git commit -m "feat: add profile bar; scope uploads and chat to the selected profile"
```

---

### Task 8: README + manual isolation verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: docs + a manual proof of isolation against live Powabase.

- [ ] **Step 1: Update `README.md`**

Add a "Profiles & data isolation" section after the existing running instructions:

```markdown
## Profiles & data isolation

Each **profile** has its own isolated Knowledge Base and agent. Type a name in
the Profile bar at the top and press Enter — the first time a name is used, its
Knowledge Base and agent are created automatically. Documents you upload are
added only to the current profile's Knowledge Base, and chats only search that
profile's documents.

Switching to a different profile clears the conversation and routes everything
to that profile's isolated data. A profile uploaded under `alice` is not visible
to `bob`.

**Scope note:** this is a demonstration of *data isolation*, not access control.
There are no passwords — anyone using the app can select any profile name.

Setup no longer needs `POWABASE_KB_ID` / `POWABASE_AGENT_ID` in `.env` (profiles
manage their own resources); `bootstrap_powabase.py` is only needed if you want a
single pre-made KB/agent for the old single-tenant flow.
```

- [ ] **Step 2: Run the full backend suite once more**

Run (from `backend/`): `pytest -v`
Expected: all tests PASS.

- [ ] **Step 3: Manual isolation proof against live Powabase**

Start the app (`uvicorn app.main:app --reload` from `backend/`, `.env` populated), open `http://127.0.0.1:8000/`, then:

- [ ] Set profile `alice`, upload a PDF, ask a question about its content — confirm a grounded answer with a citation.
- [ ] Switch to profile `bob`, ask the same question — confirm bob's chat reports it has no such document (bob's KB is empty).
- [ ] Switch back to `alice`, ask again — confirm the document is still there and answerable.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document profiles and per-profile data isolation"
```
