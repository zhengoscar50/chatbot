# RAG Chatbot on Powabase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI backend + plain HTML/JS frontend that lets a user upload a PDF and chat about it, using Powabase (Sources → Knowledge Base → Agent) for ingestion, retrieval, and generation.

**Architecture:** FastAPI backend holds the Powabase Service Role key server-side and proxies two operations — `POST /ingest/file` (upload → poll extraction → add to KB → poll indexing) and `POST /chat` (call the agent's `/run/stream`, parse the buffered SSE response, return one JSON answer). It also serves the static frontend directly via `StaticFiles`. A one-time `bootstrap_powabase.py` script creates the Knowledge Base and Agent via the API.

**Tech Stack:** Python 3.11+, FastAPI, httpx, pydantic-settings, pytest, respx (HTTP mocking for tests). No LangChain, no direct Postgres/pgvector — all of that is handled by Powabase.

## Global Constraints

- The Powabase **Service Role key must never reach the browser** — only the backend holds it; the frontend talks only to our own FastAPI endpoints.
- `model` on the Powabase agent is a LiteLLM model ID (from `.env`, `POWABASE_AGENT_MODEL`) — no hardcoded provider assumption, so any OpenAI-compatible/BYOK provider works.
- v1 scope: PDF ingestion only, single KB, single agent, no per-user auth/RLS, no streaming to the browser (backend buffers Powabase's SSE and returns one JSON response per chat turn), no automated tests beyond unit tests with mocked HTTP — end-to-end verification against the live Powabase API is manual (see Task 9).
- All Powabase HTTP calls use the two-header pattern: `apikey` and `Authorization: Bearer` both set to the Service Role key (see `PowabaseClient` in Task 2).

---

### Task 1: Backend scaffolding — packages, config, dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/requirements-dev.txt`
- Create: `backend/.env.example`
- Create: `backend/pytest.ini`
- Create: `backend/app/__init__.py` (empty)
- Create: `backend/app/core/__init__.py` (empty)
- Create: `backend/app/core/config.py`
- Create: `backend/app/clients/__init__.py` (empty)
- Create: `backend/app/services/__init__.py` (empty)
- Create: `backend/app/api/__init__.py` (empty)
- Create: `backend/app/api/routes/__init__.py` (empty)
- Create: `backend/app/models/__init__.py` (empty)
- Create: `backend/scripts/__init__.py` (empty)
- Create: `frontend/index.html` (placeholder — replaced with the real UI in Task 8; needed now so `StaticFiles` has a directory to mount in Task 6)
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings model) with fields `powabase_base_url: str`, `powabase_service_role_key: str`, `powabase_kb_id: str`, `powabase_agent_id: str`, `powabase_agent_model: str = "gpt-4o-mini"`, `poll_interval_seconds: float = 2.0`, `ingest_max_wait_seconds: float = 60.0`. Produces `get_settings() -> Settings`, an `lru_cache`-wrapped factory (later tasks call `get_settings()`, never construct `Settings()` directly, and must call `get_settings.cache_clear()` in tests after changing env vars). Produces `FRONTEND_DIR: Path` pointing at the repo-root `frontend/` directory.

- [ ] **Step 1: Create directory structure and empty package files**

```bash
mkdir -p backend/app/core backend/app/clients backend/app/services backend/app/api/routes backend/app/models backend/scripts backend/tests/unit frontend
touch backend/app/__init__.py backend/app/core/__init__.py backend/app/clients/__init__.py backend/app/services/__init__.py backend/app/api/__init__.py backend/app/api/routes/__init__.py backend/app/models/__init__.py backend/scripts/__init__.py
```

- [ ] **Step 2: Write `backend/requirements.txt`**

```
fastapi
uvicorn[standard]
httpx
pydantic-settings
python-multipart
python-dotenv
```

- [ ] **Step 3: Write `backend/requirements-dev.txt`**

```
-r requirements.txt
pytest
respx
```

- [ ] **Step 4: Write `backend/.env.example`**

```
POWABASE_BASE_URL=https://your-project-ref.p.powabase.ai
POWABASE_SERVICE_ROLE_KEY=your-service-role-key
POWABASE_KB_ID=
POWABASE_AGENT_ID=
POWABASE_AGENT_MODEL=gpt-4o-mini
POWABASE_PROVIDER_NAME=
POWABASE_PROVIDER_KEY=
```

- [ ] **Step 5: Write `backend/pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 6: Write `frontend/index.html` placeholder**

```html
<!doctype html>
<html>
  <head><title>RAG Chatbot</title></head>
  <body><p>RAG Chatbot — coming soon.</p></body>
</html>
```

- [ ] **Step 7: Write the failing test for `Settings`**

```python
# backend/tests/unit/test_config.py
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_requires_powabase_credentials(monkeypatch):
    for var in (
        "POWABASE_BASE_URL",
        "POWABASE_SERVICE_ROLE_KEY",
        "POWABASE_KB_ID",
        "POWABASE_AGENT_ID",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_loads_from_environment_with_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")

    settings = Settings(_env_file=None)

    assert settings.powabase_base_url == "https://demo.p.powabase.ai"
    assert settings.powabase_agent_model == "gpt-4o-mini"
    assert settings.poll_interval_seconds == 2.0
```

- [ ] **Step 8: Run test to verify it fails (module doesn't exist yet)**

Run (from `backend/`): `python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && pytest tests/unit/test_config.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'app.core.config'`

- [ ] **Step 9: Write `backend/app/core/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    powabase_base_url: str
    powabase_service_role_key: str
    powabase_kb_id: str
    powabase_agent_id: str
    powabase_agent_model: str = "gpt-4o-mini"

    poll_interval_seconds: float = 2.0
    ingest_max_wait_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 11: Commit**

```bash
git add backend frontend
git commit -m "chore: scaffold backend packages and Settings config"
```

---

### Task 2: Powabase HTTP client — SSE parsing + `PowabaseClient`

**Files:**
- Create: `backend/app/clients/sse.py`
- Create: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_sse.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `parse_sse(text: str) -> list[dict]` (each dict is `{"event": str, "data": dict}`). Produces `PowabaseAPIError(status_code: int, body)` exception. Produces `PowabaseClient(base_url: str, service_role_key: str)` with methods used by later tasks: `upload_source(filename, content) -> dict`, `get_source(source_id) -> dict`, `list_knowledge_bases() -> dict`, `create_knowledge_base(name, description="") -> dict`, `get_knowledge_base(kb_id) -> dict`, `add_source_to_kb(kb_id, source_id) -> dict`, `list_kb_sources(kb_id) -> dict`, `list_agents() -> dict`, `create_agent(name, model, system_prompt) -> dict`, `get_agent(agent_id) -> dict`, `link_kb_to_agent(agent_id, kb_id) -> dict`, `create_provider_key(provider, api_key) -> dict`, `run_agent(agent_id, message, session_id=None, citations_enabled=True) -> list[dict]` (returns `parse_sse` output).

- [ ] **Step 1: Write the failing tests for `parse_sse`**

```python
# backend/tests/unit/test_sse.py
from app.clients.sse import parse_sse


def test_parse_sse_multiple_events():
    text = (
        "event: start\n"
        'data: {"session_id": "sess-1"}\n'
        "\n"
        "event: complete\n"
        'data: {"answer": "hi"}\n'
        "\n"
    )

    events = parse_sse(text)

    assert events == [
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"answer": "hi"}},
    ]


def test_parse_sse_defaults_event_name_to_message():
    text = 'data: {"value": 1}\n\n'

    events = parse_sse(text)

    assert events == [{"event": "message", "data": {"value": 1}}]


def test_parse_sse_non_json_payload_falls_back_to_raw():
    text = "event: ping\ndata: not-json\n\n"

    events = parse_sse(text)

    assert events == [{"event": "ping", "data": {"raw": "not-json"}}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_sse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.clients.sse'`

- [ ] **Step 3: Write `backend/app/clients/sse.py`**

```python
import json


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        payload = "\n".join(data_lines)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"raw": payload}
        events.append({"event": event_name or "message", "data": data})

    for line in text.splitlines():
        if line == "":
            flush()
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    flush()
    return events
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_sse.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing tests for `PowabaseClient`**

```python
# backend/tests/unit/test_powabase_client.py
import httpx
import pytest
import respx

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient

BASE_URL = "https://demo.p.powabase.ai"


@respx.mock
def test_upload_source_returns_new_source_id():
    respx.post(f"{BASE_URL}/api/sources/upload").mock(
        return_value=httpx.Response(
            201, json={"id": "src-1", "extraction_status": "pending"}
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    result = client.upload_source("doc.pdf", b"%PDF-1.4 fake bytes")

    assert result["id"] == "src-1"


@respx.mock
def test_upload_source_reuses_duplicate_on_409():
    respx.post(f"{BASE_URL}/api/sources/upload").mock(
        return_value=httpx.Response(
            409,
            json={"error": "duplicate_source", "duplicate": {"id": "src-existing"}},
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    result = client.upload_source("doc.pdf", b"%PDF-1.4 fake bytes")

    assert result["id"] == "src-existing"


@respx.mock
def test_get_source_raises_powabase_api_error_on_404():
    respx.get(f"{BASE_URL}/api/sources/missing").mock(
        return_value=httpx.Response(404, json={"error": "not_found"})
    )
    client = PowabaseClient(BASE_URL, "test-key")

    with pytest.raises(PowabaseAPIError) as exc_info:
        client.get_source("missing")

    assert exc_info.value.status_code == 404


@respx.mock
def test_run_agent_parses_sse_events():
    sse_body = (
        "event: start\n"
        'data: {"session_id": "sess-1"}\n'
        "\n"
        "event: complete\n"
        'data: {"answer": "The answer.", "citations": []}\n'
        "\n"
    )
    respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(
            200, text=sse_body, headers={"content-type": "text/event-stream"}
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    events = client.run_agent("agent-1", "hello")

    assert events[0] == {"event": "start", "data": {"session_id": "sess-1"}}
    assert events[1]["event"] == "complete"
    assert events[1]["data"]["answer"] == "The answer."


@respx.mock
def test_run_agent_retries_once_on_503():
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream")
    route.side_effect = [
        httpx.Response(503, json={"error": "billing service unreachable"}),
        httpx.Response(
            200,
            text='event: complete\ndata: {"answer": "ok"}\n\n',
            headers={"content-type": "text/event-stream"},
        ),
    ]
    client = PowabaseClient(BASE_URL, "test-key")

    events = client.run_agent("agent-1", "hello")

    assert events[0]["data"]["answer"] == "ok"
    assert route.call_count == 2
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/unit/test_powabase_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.clients.powabase_client'`

- [ ] **Step 7: Write `backend/app/clients/powabase_client.py`**

```python
import time

import httpx

from app.clients.sse import parse_sse


class PowabaseAPIError(Exception):
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Powabase API error {status_code}: {body}")


class PowabaseClient:
    def __init__(self, base_url: str, service_role_key: str):
        headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        }
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._client.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise PowabaseAPIError(response.status_code, body)

    # Sources -----------------------------------------------------------

    def upload_source(self, filename: str, content: bytes) -> dict:
        response = self._client.post(
            "/api/sources/upload", files={"file": (filename, content)}
        )
        if response.status_code == 409:
            body = response.json()
            return body.get("duplicate", body)
        self._raise_for_status(response)
        return response.json()

    def get_source(self, source_id: str) -> dict:
        response = self._client.get(f"/api/sources/{source_id}")
        self._raise_for_status(response)
        return response.json()

    # Knowledge bases -----------------------------------------------------

    def list_knowledge_bases(self) -> dict:
        response = self._client.get("/api/knowledge-bases")
        self._raise_for_status(response)
        return response.json()

    def create_knowledge_base(self, name: str, description: str = "") -> dict:
        response = self._client.post(
            "/api/knowledge-bases", json={"name": name, "description": description}
        )
        self._raise_for_status(response)
        return response.json()

    def get_knowledge_base(self, kb_id: str) -> dict:
        response = self._client.get(f"/api/knowledge-bases/{kb_id}")
        self._raise_for_status(response)
        return response.json()

    def add_source_to_kb(self, kb_id: str, source_id: str) -> dict:
        response = self._client.post(
            f"/api/knowledge-bases/{kb_id}/sources", json={"source_id": source_id}
        )
        self._raise_for_status(response)
        return response.json()

    def list_kb_sources(self, kb_id: str) -> dict:
        response = self._client.get(f"/api/knowledge-bases/{kb_id}/sources")
        self._raise_for_status(response)
        return response.json()

    # Agents --------------------------------------------------------------

    def list_agents(self) -> dict:
        response = self._client.get("/api/agents")
        self._raise_for_status(response)
        return response.json()

    def create_agent(self, name: str, model: str, system_prompt: str) -> dict:
        response = self._client.post(
            "/api/agents",
            json={"name": name, "model": model, "system_prompt": system_prompt},
        )
        self._raise_for_status(response)
        return response.json()

    def get_agent(self, agent_id: str) -> dict:
        response = self._client.get(f"/api/agents/{agent_id}")
        self._raise_for_status(response)
        return response.json()

    def link_kb_to_agent(self, agent_id: str, kb_id: str) -> dict:
        response = self._client.post(
            f"/api/agents/{agent_id}/knowledge-bases",
            json={"knowledge_base_id": kb_id},
        )
        self._raise_for_status(response)
        return response.json()

    def run_agent(
        self,
        agent_id: str,
        message: str,
        session_id: str | None = None,
        citations_enabled: bool = True,
    ) -> list[dict]:
        payload: dict = {"message": message, "citations_enabled": citations_enabled}
        if session_id:
            payload["session_id"] = session_id

        response = self._client.post(
            f"/api/agents/{agent_id}/run/stream", json=payload, timeout=120.0
        )
        if response.status_code == 503:
            time.sleep(1.0)
            response = self._client.post(
                f"/api/agents/{agent_id}/run/stream", json=payload, timeout=120.0
            )
        self._raise_for_status(response)
        return parse_sse(response.text)

    # Provider keys ---------------------------------------------------------

    def create_provider_key(self, provider: str, api_key: str) -> dict:
        response = self._client.post(
            "/api/ai-provider-keys", json={"provider": provider, "api_key": api_key}
        )
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/unit/test_powabase_client.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/clients backend/tests/unit/test_sse.py backend/tests/unit/test_powabase_client.py
git commit -m "feat: add PowabaseClient with SSE parsing and 409/503 handling"
```

---

### Task 3: Ingest service — upload → extraction poll → index poll

**Files:**
- Create: `backend/app/services/ingest_service.py`
- Test: `backend/tests/unit/test_ingest_service.py`

**Interfaces:**
- Consumes: a duck-typed client object with `upload_source`, `get_source`, `add_source_to_kb`, `list_kb_sources` (matches `PowabaseClient` from Task 2, but tests use a lightweight fake — no import of `PowabaseClient` needed here).
- Produces: `IngestService(client, kb_id, poll_interval=2.0, max_wait=60.0)` with `.ingest_pdf(filename, content) -> dict` returning `{"source_id": str, "status": str}`. Produces exceptions `AttentionRequiredError(source_id)`, `ExtractionFailedError(source_id, message)`, `IndexingFailedError(source_id, message)`, `IngestTimeoutError(source_id, status)` — all with those exact attribute names, consumed by Task 5's ingest route.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_ingest_service.py
import pytest

from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)


class FakeClient:
    def __init__(self, source_statuses, index_statuses):
        self.source_statuses = list(source_statuses)
        self.index_statuses = list(index_statuses)
        self.added_to_kb = []

    def upload_source(self, filename, content):
        return {"id": "src-1"}

    def get_source(self, source_id):
        status = (
            self.source_statuses.pop(0)
            if len(self.source_statuses) > 1
            else self.source_statuses[0]
        )
        return {"extraction_status": status, "error_message": "boom"}

    def add_source_to_kb(self, kb_id, source_id):
        self.added_to_kb.append((kb_id, source_id))
        return {"id": "indexed-1"}

    def list_kb_sources(self, kb_id):
        status = (
            self.index_statuses.pop(0)
            if len(self.index_statuses) > 1
            else self.index_statuses[0]
        )
        return {
            "items": [
                {"source_id": "src-1", "index_status": status, "error_message": "boom"}
            ]
        }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.services.ingest_service.time.sleep", lambda seconds: None)


def test_ingest_pdf_success_path():
    client = FakeClient(
        source_statuses=["extracting", "extracted"],
        index_statuses=["indexing", "indexed"],
    )
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    result = service.ingest_pdf("doc.pdf", b"bytes")

    assert result == {"source_id": "src-1", "status": "indexed"}
    assert client.added_to_kb == [("kb-1", "src-1")]


def test_ingest_pdf_raises_on_attention_required():
    client = FakeClient(source_statuses=["attention_required"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(AttentionRequiredError) as exc_info:
        service.ingest_pdf("doc.pdf", b"bytes")

    assert exc_info.value.source_id == "src-1"


def test_ingest_pdf_raises_on_extraction_failed():
    client = FakeClient(source_statuses=["failed"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(ExtractionFailedError):
        service.ingest_pdf("doc.pdf", b"bytes")


def test_ingest_pdf_raises_on_indexing_failed():
    client = FakeClient(source_statuses=["extracted"], index_statuses=["failed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=5.0)

    with pytest.raises(IndexingFailedError):
        service.ingest_pdf("doc.pdf", b"bytes")


def test_ingest_pdf_raises_timeout_when_extraction_never_terminates():
    client = FakeClient(source_statuses=["extracting"], index_statuses=["indexed"])
    service = IngestService(client, kb_id="kb-1", poll_interval=0.01, max_wait=0)

    with pytest.raises(IngestTimeoutError) as exc_info:
        service.ingest_pdf("doc.pdf", b"bytes")

    assert exc_info.value.status == "extracting"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_ingest_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ingest_service'`

- [ ] **Step 3: Write `backend/app/services/ingest_service.py`**

```python
import time


class AttentionRequiredError(Exception):
    def __init__(self, source_id: str):
        self.source_id = source_id
        super().__init__(f"Source {source_id} needs OCR re-extraction")


class ExtractionFailedError(Exception):
    def __init__(self, source_id: str, message: str):
        self.source_id = source_id
        self.message = message
        super().__init__(message)


class IndexingFailedError(Exception):
    def __init__(self, source_id: str, message: str):
        self.source_id = source_id
        self.message = message
        super().__init__(message)


class IngestTimeoutError(Exception):
    def __init__(self, source_id: str, status: str):
        self.source_id = source_id
        self.status = status
        super().__init__(f"Source {source_id} still {status} after max wait")


class IngestService:
    def __init__(self, client, kb_id: str, poll_interval: float = 2.0, max_wait: float = 60.0):
        self.client = client
        self.kb_id = kb_id
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def ingest_pdf(self, filename: str, content: bytes) -> dict:
        source = self.client.upload_source(filename, content)
        source_id = source["id"]
        self._wait_for_extraction(source_id)
        self.client.add_source_to_kb(self.kb_id, source_id)
        status = self._wait_for_indexing(source_id)
        return {"source_id": source_id, "status": status}

    def _wait_for_extraction(self, source_id: str) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            source = self.client.get_source(source_id)
            status = source["extraction_status"]
            if status == "extracted":
                return
            if status == "attention_required":
                raise AttentionRequiredError(source_id)
            if status in ("failed", "cancelled"):
                raise ExtractionFailedError(source_id, source.get("error_message", status))
            if time.monotonic() >= deadline:
                raise IngestTimeoutError(source_id, status)
            time.sleep(self.poll_interval)

    def _wait_for_indexing(self, source_id: str) -> str:
        deadline = time.monotonic() + self.max_wait
        while True:
            sources = self.client.list_kb_sources(self.kb_id)
            entry = next(
                (item for item in sources["items"] if item.get("source_id") == source_id),
                None,
            )
            if entry is None:
                if time.monotonic() >= deadline:
                    raise IngestTimeoutError(source_id, "pending")
                time.sleep(self.poll_interval)
                continue
            status = entry["index_status"]
            if status == "indexed":
                return status
            if status in ("failed", "cancelled"):
                raise IndexingFailedError(source_id, entry.get("error_message", status))
            if time.monotonic() >= deadline:
                raise IngestTimeoutError(source_id, status)
            time.sleep(self.poll_interval)
```

> Note: `list_kb_sources` items are assumed to carry a `source_id` field (alongside `id` as the `indexed_source_id`, per the "two-IDs trap" documented for Powabase). Verify this field name against `https://docs.powabase.ai` during Task 9's manual end-to-end verification, since the reference snapshot doesn't spell out every field on this response.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_ingest_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ingest_service.py backend/tests/unit/test_ingest_service.py
git commit -m "feat: add IngestService with extraction/indexing polling"
```

---

### Task 4: Chat service — run agent, collect answer, map errors

**Files:**
- Create: `backend/app/services/chat_service.py`
- Test: `backend/tests/unit/test_chat_service.py`

**Interfaces:**
- Consumes: a duck-typed client with `run_agent(agent_id, message, session_id=None, citations_enabled=True) -> list[dict]` (matches `PowabaseClient` from Task 2).
- Produces: `ChatService(client, agent_id)` with `.ask(query, session_id=None) -> dict` returning `{"answer": str, "session_id": str | None, "citations": list}`. Produces `InsufficientCreditsError(message)` and `ProviderKeyError(message)` (both with a `.message` attribute), consumed by Task 5's chat route.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_chat_service.py
import pytest

from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ProviderKeyError,
)


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def run_agent(self, agent_id, message, session_id=None, citations_enabled=True):
        self.calls.append({"agent_id": agent_id, "message": message, "session_id": session_id})
        return self.events


def test_ask_returns_answer_and_session_id():
    client = FakeClient(
        events=[
            {"event": "start", "data": {"session_id": "sess-1"}},
            {
                "event": "complete",
                "data": {"answer": "42", "citations": [{"source_id": "src-1"}]},
            },
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    result = service.ask("What is the answer?")

    assert result == {
        "answer": "42",
        "session_id": "sess-1",
        "citations": [{"source_id": "src-1"}],
    }
    assert client.calls[0]["agent_id"] == "agent-1"


def test_ask_raises_insufficient_credits():
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {"error": "insufficient_credits", "message": "no credits"},
            }
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(InsufficientCreditsError):
        service.ask("hello")


def test_ask_raises_provider_key_error():
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {"error": "provider_key_decrypt_failed", "message": "bad key"},
            }
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(ProviderKeyError):
        service.ask("hello")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_chat_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.chat_service'`

- [ ] **Step 3: Write `backend/app/services/chat_service.py`**

```python
class InsufficientCreditsError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ProviderKeyError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ChatService:
    def __init__(self, client, agent_id: str):
        self.client = client
        self.agent_id = agent_id

    def ask(self, query: str, session_id: str | None = None) -> dict:
        events = self.client.run_agent(
            self.agent_id, query, session_id=session_id, citations_enabled=True
        )
        answer = None
        citations: list = []
        result_session_id = session_id

        for event in events:
            name = event["event"]
            data = event["data"]
            if name == "start":
                result_session_id = data.get("session_id", result_session_id)
            elif name == "error":
                self._raise_for_error(data)
            elif name == "complete":
                answer = data.get("answer")
                citations = data.get("citations", [])

        if answer is None:
            raise RuntimeError("Agent run completed without a final answer")

        return {"answer": answer, "session_id": result_session_id, "citations": citations}

    def _raise_for_error(self, data: dict) -> None:
        code = data.get("error", "")
        message = data.get("message", str(data))
        if code == "insufficient_credits":
            raise InsufficientCreditsError(message)
        if code == "provider_key_decrypt_failed":
            raise ProviderKeyError(message)
        raise RuntimeError(message)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_chat_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chat_service.py backend/tests/unit/test_chat_service.py
git commit -m "feat: add ChatService with citation/error handling"
```

---

### Task 5: Schemas + API routes (health, ingest, chat)

**Files:**
- Create: `backend/app/models/schemas.py`
- Create: `backend/app/api/routes/health.py`
- Create: `backend/app/api/routes/ingest.py`
- Create: `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_routes_health.py`
- Test: `backend/tests/unit/test_routes_ingest.py`
- Test: `backend/tests/unit/test_routes_chat.py`

**Interfaces:**
- Consumes: `get_settings` (Task 1), `PowabaseClient`/`PowabaseAPIError` (Task 2), `IngestService` + its exceptions (Task 3), `ChatService` + its exceptions (Task 4).
- Produces: `health_router`, `ingest_router`, `chat_router` (FastAPI `APIRouter` instances), imported by `main.py` in Task 6.

- [ ] **Step 1: Write `backend/app/models/schemas.py`**

```python
from typing import Any, Optional

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    source_id: str
    status: str


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: Optional[str] = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: Write the failing test for the health route**

```python
# backend/tests/unit/test_routes_health.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def build_app():
    from app.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_returns_configured_ids(monkeypatch):
    set_env(monkeypatch)

    client = TestClient(build_app())
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kb_id"] == "kb-123"
    assert body["agent_id"] == "agent-456"
    assert body["model"] == "gpt-4o-mini"
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/unit/test_routes_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.health'`

- [ ] **Step 4: Write `backend/app/api/routes/health.py`**

```python
from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "kb_id": settings.powabase_kb_id,
        "agent_id": settings.powabase_agent_id,
        "model": settings.powabase_agent_model,
    }
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/unit/test_routes_health.py -v`
Expected: PASS

- [ ] **Step 6: Write the failing tests for the ingest route**

```python
# backend/tests/unit/test_routes_ingest.py
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ingest as ingest_route
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def build_app():
    app = FastAPI()
    app.include_router(ingest_route.router)
    return app


class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        pass

    def ingest_pdf(self, filename, content):
        return {"source_id": "src-1", "status": "indexed"}


def upload(client):
    return client.post(
        "/ingest/file",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_ingest_file_returns_indexed_status(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "IngestService", FakeIngestService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 200
    assert response.json() == {"source_id": "src-1", "status": "indexed"}


def test_ingest_file_returns_422_when_attention_required(monkeypatch):
    set_env(monkeypatch)

    class AttentionService(FakeIngestService):
        def ingest_pdf(self, filename, content):
            raise ingest_route.AttentionRequiredError("src-2")

    monkeypatch.setattr(ingest_route, "IngestService", AttentionService)

    response = upload(TestClient(build_app()))

    assert response.status_code == 422
    assert "src-2" in response.json()["detail"]


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

- [ ] **Step 7: Run to verify it fails**

Run: `pytest tests/unit/test_routes_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.ingest'`

- [ ] **Step 8: Write `backend/app/api/routes/ingest.py`**

```python
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import get_settings
from app.models.schemas import IngestResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...)):
    content = await file.read()
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    service = IngestService(
        client,
        settings.powabase_kb_id,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )
    try:
        result = service.ingest_pdf(file.filename, content)
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

- [ ] **Step 9: Run to verify it passes**

Run: `pytest tests/unit/test_routes_ingest.py -v`
Expected: PASS (3 tests)

- [ ] **Step 10: Write the failing tests for the chat route**

```python
# backend/tests/unit/test_routes_chat.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat as chat_route
from app.core.config import get_settings


def set_env(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def build_app():
    app = FastAPI()
    app.include_router(chat_route.router)
    return app


class FakeChatService:
    def __init__(self, client, agent_id):
        pass

    def ask(self, query, session_id=None):
        return {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_returns_answer(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)

    response = TestClient(build_app()).post("/chat", json={"query": "What is the answer?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "42", "session_id": "sess-1", "citations": []}


def test_chat_returns_402_on_insufficient_credits(monkeypatch):
    set_env(monkeypatch)

    class InsufficientService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.InsufficientCreditsError("no credits left")

    monkeypatch.setattr(chat_route, "ChatService", InsufficientService)

    response = TestClient(build_app()).post("/chat", json={"query": "hi"})

    assert response.status_code == 402
    assert response.json()["detail"] == "no credits left"


def test_chat_returns_424_on_provider_key_error(monkeypatch):
    set_env(monkeypatch)

    class ProviderErrorService(FakeChatService):
        def ask(self, query, session_id=None):
            raise chat_route.ProviderKeyError("bad key")

    monkeypatch.setattr(chat_route, "ChatService", ProviderErrorService)

    response = TestClient(build_app()).post("/chat", json={"query": "hi"})

    assert response.status_code == 424
    assert response.json()["detail"] == "bad key"
```

- [ ] **Step 11: Run to verify it fails**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.chat'`

- [ ] **Step 12: Write `backend/app/api/routes/chat.py`**

```python
from fastapi import APIRouter, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import ChatService, InsufficientCreditsError, ProviderKeyError

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest):
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    service = ChatService(client, settings.powabase_agent_id)
    try:
        result = service.ask(req.query, session_id=req.session_id)
        return ChatResponse(**result)
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)
    except ProviderKeyError as e:
        raise HTTPException(status_code=424, detail=e.message)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

- [ ] **Step 13: Run to verify it passes**

Run: `pytest tests/unit/test_routes_chat.py -v`
Expected: PASS (3 tests)

- [ ] **Step 14: Commit**

```bash
git add backend/app/models backend/app/api backend/tests/unit/test_routes_health.py backend/tests/unit/test_routes_ingest.py backend/tests/unit/test_routes_chat.py
git commit -m "feat: add schemas and health/ingest/chat routes"
```

---

### Task 6: FastAPI app assembly with startup validation

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: `get_settings`, `FRONTEND_DIR` (Task 1), `PowabaseClient`, `PowabaseAPIError` (Task 2), `health_router`/`ingest_router`/`chat_router` (Task 5).
- Produces: `create_app() -> FastAPI` and module-level `app`, used to run `uvicorn app.main:app`.

- [ ] **Step 1: Write the failing tests**

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
    monkeypatch.setenv("POWABASE_KB_ID", "kb-123")
    monkeypatch.setenv("POWABASE_AGENT_ID", "agent-456")
    get_settings.cache_clear()


def test_app_starts_when_kb_and_agent_are_reachable(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(
        main_module.PowabaseClient, "get_knowledge_base", lambda self, kb_id: {"id": kb_id}
    )
    monkeypatch.setattr(
        main_module.PowabaseClient, "get_agent", lambda self, agent_id: {"id": agent_id}
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200


def test_app_fails_to_start_when_kb_is_unreachable(monkeypatch):
    set_env(monkeypatch)

    def raise_error(self, kb_id):
        raise PowabaseAPIError(404, {"error": "not_found"})

    monkeypatch.setattr(main_module.PowabaseClient, "get_knowledge_base", raise_error)

    app = main_module.create_app()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `backend/app/main.py`**

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient
from app.core.config import FRONTEND_DIR, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = PowabaseClient(settings.powabase_base_url, settings.powabase_service_role_key)
    try:
        client.get_knowledge_base(settings.powabase_kb_id)
    except PowabaseAPIError as e:
        raise RuntimeError(
            f"Powabase Knowledge Base {settings.powabase_kb_id} is not reachable: {e}"
        ) from e
    try:
        client.get_agent(settings.powabase_agent_id)
    except PowabaseAPIError as e:
        raise RuntimeError(
            f"Powabase Agent {settings.powabase_agent_id} is not reachable: {e}"
        ) from e
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Chatbot on Powabase", version="1.0.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(chat_router)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


app = create_app()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_main_lifespan.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite so far**

Run: `pytest -v`
Expected: All tests PASS (config, sse, powabase_client, ingest_service, chat_service, routes x3, main_lifespan)

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/unit/test_main_lifespan.py
git commit -m "feat: assemble FastAPI app with Powabase startup validation"
```

---

### Task 7: Bootstrap script — create KB + Agent via API

**Files:**
- Create: `backend/scripts/bootstrap_powabase.py`
- Test: `backend/tests/unit/test_bootstrap_powabase.py`

**Interfaces:**
- Consumes: `PowabaseClient` (Task 2) — only in `main()`; the testable `bootstrap()` function takes a duck-typed client.
- Produces: `bootstrap(client, model, provider=None, provider_key=None) -> dict` returning `{"kb_id": str, "agent_id": str}`, and a `main()` CLI entry point that reads env vars and prints the IDs.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_bootstrap_powabase.py
from scripts.bootstrap_powabase import bootstrap


class FakeClient:
    def __init__(self, existing_kbs=None, existing_agents=None):
        self.existing_kbs = existing_kbs or []
        self.existing_agents = existing_agents or []
        self.created_kbs = []
        self.created_agents = []
        self.linked = []
        self.provider_keys = []

    def create_provider_key(self, provider, api_key):
        self.provider_keys.append((provider, api_key))

    def list_knowledge_bases(self):
        return {"items": self.existing_kbs}

    def create_knowledge_base(self, name, description=""):
        kb = {"id": "kb-new", "name": name}
        self.created_kbs.append(kb)
        return kb

    def list_agents(self):
        return {"agents": self.existing_agents}

    def create_agent(self, name, model, system_prompt):
        agent = {"id": "agent-new", "name": name}
        self.created_agents.append(agent)
        return agent

    def link_kb_to_agent(self, agent_id, kb_id):
        self.linked.append((agent_id, kb_id))


def test_bootstrap_creates_kb_and_agent_when_none_exist():
    client = FakeClient()

    result = bootstrap(client, model="gpt-4o-mini")

    assert result == {"kb_id": "kb-new", "agent_id": "agent-new"}
    assert client.linked == [("agent-new", "kb-new")]


def test_bootstrap_reuses_existing_kb_and_agent():
    client = FakeClient(
        existing_kbs=[{"id": "kb-existing", "name": "rag-chatbot-kb"}],
        existing_agents=[{"id": "agent-existing", "name": "rag-chatbot-agent"}],
    )

    result = bootstrap(client, model="gpt-4o-mini")

    assert result == {"kb_id": "kb-existing", "agent_id": "agent-existing"}
    assert client.created_kbs == []
    assert client.created_agents == []
    assert client.linked == []


def test_bootstrap_registers_provider_key_when_supplied():
    client = FakeClient()

    bootstrap(client, model="gpt-4o-mini", provider="groq", provider_key="secret-key")

    assert client.provider_keys == [("groq", "secret-key")]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_bootstrap_powabase.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.bootstrap_powabase'`

- [ ] **Step 3: Write `backend/scripts/bootstrap_powabase.py`**

```python
"""One-time setup: create the Powabase Knowledge Base and Agent for this project.

Run from backend/ with: python -m scripts.bootstrap_powabase
"""
import os

from app.clients.powabase_client import PowabaseClient

KB_NAME = "rag-chatbot-kb"
AGENT_NAME = "rag-chatbot-agent"
SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer questions using the linked knowledge "
    "base. If the knowledge base doesn't contain the answer, say so plainly "
    "instead of guessing."
)


def find_by_name(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if item.get("name") == name), None)


def bootstrap(
    client: PowabaseClient,
    model: str,
    provider: str | None = None,
    provider_key: str | None = None,
) -> dict:
    if provider and provider_key:
        client.create_provider_key(provider, provider_key)

    existing_kbs = client.list_knowledge_bases().get("items", [])
    kb = find_by_name(existing_kbs, KB_NAME)
    if kb is None:
        kb = client.create_knowledge_base(KB_NAME, description="RAG chatbot knowledge base")

    existing_agents = client.list_agents().get("agents", [])
    agent = find_by_name(existing_agents, AGENT_NAME)
    if agent is None:
        agent = client.create_agent(AGENT_NAME, model=model, system_prompt=SYSTEM_PROMPT)
        client.link_kb_to_agent(agent["id"], kb["id"])

    return {"kb_id": kb["id"], "agent_id": agent["id"]}


def main() -> None:
    base_url = os.environ["POWABASE_BASE_URL"]
    service_role_key = os.environ["POWABASE_SERVICE_ROLE_KEY"]
    model = os.environ.get("POWABASE_AGENT_MODEL", "gpt-4o-mini")
    provider = os.environ.get("POWABASE_PROVIDER_NAME") or None
    provider_key = os.environ.get("POWABASE_PROVIDER_KEY") or None

    client = PowabaseClient(base_url, service_role_key)
    result = bootstrap(client, model, provider=provider, provider_key=provider_key)

    print(f"POWABASE_KB_ID={result['kb_id']}")
    print(f"POWABASE_AGENT_ID={result['agent_id']}")


if __name__ == "__main__":
    main()
```

> Note: `list_knowledge_bases()`'s response shape is assumed to be `{"items": [...]}` (consistent with the other paginated list endpoints in the reference docs); `list_agents()`'s `{"agents": [...]}` shape is directly confirmed by the docs' example. Verify the KB list shape live during Task 9 if `bootstrap.py` errors on a fresh project.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_bootstrap_powabase.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/bootstrap_powabase.py backend/tests/unit/test_bootstrap_powabase.py
git commit -m "feat: add bootstrap script to create Powabase KB and Agent"
```

---

### Task 8: Real frontend — upload + chat UI

**Files:**
- Modify: `frontend/index.html` (replace Task 1's placeholder)
- Create: `frontend/app.js`
- Create: `frontend/styles.css`

**Interfaces:**
- Consumes: `POST /ingest/file` and `POST /chat` (Task 5), served by the app assembled in Task 6.
- Produces: nothing consumed by other tasks — this is the top of the stack.

- [ ] **Step 1: Replace `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>RAG Chatbot</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main>
      <section id="upload-panel">
        <h2>Upload a document</h2>
        <input type="file" id="file-input" accept="application/pdf" />
        <button id="upload-button">Upload</button>
        <p id="upload-status"></p>
      </section>
      <section id="chat-panel">
        <h2>Chat</h2>
        <div id="messages"></div>
        <form id="chat-form">
          <input type="text" id="chat-input" placeholder="Ask a question..." autocomplete="off" />
          <button type="submit">Send</button>
        </form>
      </section>
    </main>
    <script src="/app.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Write `frontend/app.js`**

```javascript
const uploadButton = document.getElementById("upload-button");
const fileInput = document.getElementById("file-input");
const uploadStatus = document.getElementById("upload-status");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const messages = document.getElementById("messages");

let sessionId = null;

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    uploadStatus.textContent = "Choose a PDF first.";
    return;
  }
  uploadStatus.textContent = "Uploading and indexing...";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/ingest/file", { method: "POST", body: formData });
    const body = await response.json();
    if (response.ok || response.status === 202) {
      uploadStatus.textContent = `Status: ${body.status} (source ${body.source_id})`;
    } else {
      uploadStatus.textContent = `Error: ${body.detail || response.statusText}`;
    }
  } catch (err) {
    uploadStatus.textContent = `Error: ${err.message}`;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = chatInput.value.trim();
  if (!query) return;
  appendMessage("You", query);
  chatInput.value = "";

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, session_id: sessionId }),
    });
    const body = await response.json();
    if (response.ok) {
      sessionId = body.session_id;
      appendMessage("Assistant", body.answer);
    } else {
      appendMessage("Error", body.detail || response.statusText);
    }
  } catch (err) {
    appendMessage("Error", err.message);
  }
});

function appendMessage(who, text) {
  const el = document.createElement("p");
  el.innerHTML = `<strong>${who}:</strong> ${escapeHtml(text)}`;
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
```

- [ ] **Step 3: Write `frontend/styles.css`**

```css
body {
  font-family: system-ui, sans-serif;
  max-width: 720px;
  margin: 2rem auto;
  padding: 0 1rem;
}

section {
  margin-bottom: 2rem;
}

#messages {
  border: 1px solid #ccc;
  border-radius: 8px;
  padding: 1rem;
  height: 300px;
  overflow-y: auto;
  margin-bottom: 0.5rem;
}

#chat-form {
  display: flex;
  gap: 0.5rem;
}

#chat-input {
  flex: 1;
  padding: 0.5rem;
}
```

- [ ] **Step 4: Manual smoke test**

Run (from `backend/`, with `.env` populated after Task 9's setup): `uvicorn app.main:app --reload`
Open `http://127.0.0.1:8000/` in a browser, confirm the page loads with an upload panel and a chat panel (full upload/chat verification happens in Task 9 once Powabase credentials are real).

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: build upload + chat frontend"
```

---

### Task 9: README + end-to-end manual verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: nothing — this is documentation plus a manual verification pass against the live Powabase API.

- [ ] **Step 1: Write `README.md`**

```markdown
# RAG Chatbot on Powabase

A RAG chatbot backend (FastAPI) + simple frontend, backed by
[Powabase](https://powabase.ai)'s native Sources/Knowledge-Base/Agent
pipeline for ingestion, retrieval, and generation.

## 1. Create a Powabase project (one-time, human step)

1. Sign up / log in at https://app.powabase.ai and create a project.
2. Open **Connect** in the project header, copy the **Project URL** and
   **Service Role (Secret) Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `POWABASE_BASE_URL` and `POWABASE_SERVICE_ROLE_KEY`.
4. Decide on a model provider. Set `POWABASE_AGENT_MODEL` to any LiteLLM
   model ID (e.g. `gpt-4o-mini`, `groq/llama-3.1-70b-versatile`,
   `openrouter/<org>/<model>`). Then either:
   - add the provider's key by hand in Studio → **Settings → LLM Provider
     Keys**, or
   - set `POWABASE_PROVIDER_NAME` / `POWABASE_PROVIDER_KEY` in `.env` and
     let the bootstrap script register it for you.

## 2. Install dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 3. Run the bootstrap script

Creates the Knowledge Base and Agent (idempotent — safe to re-run):

```bash
python -m scripts.bootstrap_powabase
```

Copy the printed `POWABASE_KB_ID` and `POWABASE_AGENT_ID` into `backend/.env`.

## 4. Run the app

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ for the chat UI, or http://127.0.0.1:8000/docs
for the Swagger UI.

## 5. Manual verification checklist

- [ ] `GET /health` returns `200` with your configured `kb_id`/`agent_id`/`model`.
- [ ] `POST /ingest/file` with a real PDF returns `{"source_id": ..., "status": "indexed"}`.
- [ ] `POST /chat` with a question about that PDF's content returns an answer
      grounded in it, with non-empty `citations`.
- [ ] Re-running `POST /ingest/file` with the *same* file succeeds without
      error (exercises the `409 duplicate_source` path).

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/ingest/file \
  -F 'file=@/path/to/your.pdf'

curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What does this document say about X?"}'
```

## Running tests

```bash
cd backend
pytest -v
```
```

- [ ] **Step 2: Run the full automated test suite one more time**

Run (from `backend/`): `pytest -v`
Expected: All tests still PASS.

- [ ] **Step 3: Perform the manual verification checklist from the README**

Follow steps 1–5 in `README.md` against your real Powabase project: run
bootstrap, start the app, and walk through the `curl` checklist with a real
PDF. Confirm each checkbox before considering the project done.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add setup, running, and verification instructions"
```
