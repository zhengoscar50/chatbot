# LLM-Gated, Detached KB Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM "gate" that decides per message whether the knowledge base is needed; when it is, retrieve explicitly via a Powabase context-handler and inject the chunks — detaching the KB from the agent so we stop wasting retrieval credits/tokens on messages that don't need it.

**Architecture:** Per chat message, a shared KB-less "router" agent classifies `needs_kb` (biased to `true` when unsure). If true, we create a context-handler over `[session KB, general KB]` and run the session's agent with that `context_handler_id`; if false, we run the agent with no context. Sessions keep their per-session agent, but we stop linking KBs to it. Conversation memory/resume are unchanged (we still pass the session's `powabase_session_id`).

**Tech Stack:** FastAPI, httpx (sync), pytest + respx, Powabase `/api/*`.

## Global Constraints

- **Python 3.9.6** — any new module using module-level `X | None` annotations must start with `from __future__ import annotations`.
- **Never** commit secrets; `.env` is gitignored. No new required env vars — every new setting has a default.
- **Keep the test suite green after every task.** The suite is `python -m pytest` from `backend/` (currently 86 passing).
- **Verified live API shapes (use verbatim):**
  - Sync run `POST /api/agents/{id}/run` → JSON body with `content` as a **string** (e.g. `"{\"needs_kb\":false}"`), plus `status`, `usage`, etc.
  - Context handler `POST /api/context-handlers` body `{ "query", "knowledge_bases": [{"id","top_k"}], "max_context_tokens"? }` → JSON with `id` (the handler id), `formatted_context`, `retrieved_context`, `metadata`, `errors`.
  - Streaming run accepts `context_handler_id` in its body; the `complete` SSE event then carries `content`, `citations` (list of `{key,item_id,source_id,source_name,text_excerpt,meta}`), and `retrieved_items`.
  - `GET /api/agents` → `{"agents": [...]}`.
- **Gate fail-safe:** if the gate call errors or returns unparseable output, treat as `needs_kb = true` (retrieve).
- Commands below assume CWD `backend/` and the venv interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/core/config.py` — 4 new settings (router model, top_k, max context tokens, history turns).
- Modify `backend/app/clients/powabase_client.py` — `run_agent_sync`, `create_context_handler`, `run_agent(context_handler_id=…)`, `create_agent(settings=…)`.
- Create `backend/app/services/router_agent.py` — shared router-agent bootstrap + response-format constant + dependency.
- Modify `backend/app/main.py` — ensure the router agent at startup, store `app.state.router_agent_id`.
- Create `backend/app/services/gate_service.py` — `GateService.needs_kb(query, history)`.
- Modify `backend/app/services/session_service.py` — stop linking KBs in `create_session`.
- Modify `backend/app/services/chat_service.py` — `ChatService` orchestrates gate → retrieve/inject → answer.
- Modify `backend/app/api/routes/chat.py` — new dependencies, fetch recent history, build gate + `ChatService`.

Test files touched: `test_config.py`, `test_powabase_client.py`, new `test_router_agent.py`, `test_main_lifespan.py`, new `test_gate_service.py`, `test_session_service.py`, `test_chat_service.py`, `test_routes_chat.py`.

---

### Task 1: Config settings

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.router_agent_model: str`, `Settings.retrieval_top_k: int`, `Settings.retrieval_max_context_tokens: int`, `Settings.gate_history_turns: int`.

- [ ] **Step 1: Write the failing test** — append to `test_config.py`:

```python
def test_gating_defaults(monkeypatch):
    monkeypatch.setenv("POWABASE_BASE_URL", "https://demo.p.powabase.ai")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "k")
    from app.core.config import Settings
    s = Settings()
    assert s.router_agent_model == "gpt-4o-mini"
    assert s.retrieval_top_k == 4
    assert s.retrieval_max_context_tokens == 2000
    assert s.gate_history_turns == 2
```

- [ ] **Step 2: Run it, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → FAIL (AttributeError).

- [ ] **Step 3: Implement** — in `config.py`, add inside `Settings` after `ingest_max_wait_seconds`:

```python
    router_agent_model: str = "gpt-4o-mini"
    retrieval_top_k: int = 4
    retrieval_max_context_tokens: int = 2000
    gate_history_turns: int = 2
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: config settings for KB-retrieval gate"`

---

### Task 2: Powabase client methods

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Produces:
  - `run_agent_sync(agent_id: str, message: str, response_format: dict | None = None) -> dict`
  - `create_context_handler(query: str, knowledge_bases: list[dict], max_context_tokens: int | None = None) -> dict`
  - `run_agent(..., context_handler_id: str | None = None)` (new trailing kwarg)
  - `create_agent(name, model, system_prompt, settings: dict | None = None)` (new trailing kwarg)

- [ ] **Step 1: Write failing tests** — append to `test_powabase_client.py`:

```python
@respx.mock
def test_run_agent_sync_returns_json_body():
    respx.post(f"{BASE_URL}/api/agents/agent-1/run").mock(
        return_value=httpx.Response(200, json={"content": '{"needs_kb": true}', "status": "completed"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    body = client.run_agent_sync("agent-1", "hi", response_format={"type": "json_schema"})
    assert body["content"] == '{"needs_kb": true}'


@respx.mock
def test_create_context_handler_posts_query_and_kbs():
    route = respx.post(f"{BASE_URL}/api/context-handlers").mock(
        return_value=httpx.Response(201, json={"id": "handler-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    result = client.create_context_handler(
        "what is x", [{"id": "kb-1", "top_k": 4}], max_context_tokens=2000
    )
    assert result["id"] == "handler-1"
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"query": "what is x", "knowledge_bases": [{"id": "kb-1", "top_k": 4}], "max_context_tokens": 2000}


@respx.mock
def test_run_agent_includes_context_handler_id_when_set():
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(200, text='data: {"event": "complete", "content": "ok"}\n\n',
                                    headers={"content-type": "text/event-stream"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.run_agent("agent-1", "hi", context_handler_id="handler-1")
    sent = json.loads(route.calls[0].request.content)
    assert sent["context_handler_id"] == "handler-1"


@respx.mock
def test_create_agent_includes_settings_when_set():
    route = respx.post(f"{BASE_URL}/api/agents").mock(
        return_value=httpx.Response(201, json={"id": "a-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.create_agent("r", model="gpt-4o-mini", system_prompt="p", settings={"temperature": 0})
    sent = json.loads(route.calls[0].request.content)
    assert sent["settings"] == {"temperature": 0}
```

Add `import json` at the top of the test file if not already present.

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `powabase_client.py`:

Extend `create_agent` (add `settings` param, include when set):

```python
    def create_agent(self, name: str, model: str, system_prompt: str, settings: dict | None = None) -> dict:
        body = {"name": name, "model": model, "system_prompt": system_prompt}
        if settings is not None:
            body["settings"] = settings
        response = self._client.post("/api/agents", json=body)
        self._raise_for_status(response)
        return response.json()
```

Add a sync-run method (place after `run_agent`):

```python
    def run_agent_sync(self, agent_id: str, message: str, response_format: dict | None = None) -> dict:
        payload: dict = {"message": message}
        if response_format is not None:
            payload["response_format"] = response_format
        response = self._client.post(f"/api/agents/{agent_id}/run", json=payload, timeout=60.0)
        self._raise_for_status(response)
        return response.json()
```

Add the context-handler method (place in a new `# Context handlers` section):

```python
    def create_context_handler(
        self, query: str, knowledge_bases: list, max_context_tokens: int | None = None
    ) -> dict:
        body: dict = {"query": query, "knowledge_bases": knowledge_bases}
        if max_context_tokens is not None:
            body["max_context_tokens"] = max_context_tokens
        response = self._client.post("/api/context-handlers", json=body, timeout=60.0)
        self._raise_for_status(response)
        return response.json()
```

Extend `run_agent` — add the parameter and payload line (keep the existing 503 retry unchanged):

```python
    def run_agent(
        self,
        agent_id: str,
        message: str,
        session_id: str | None = None,
        citations_enabled: bool = True,
        context_handler_id: str | None = None,
    ) -> list[dict]:
        payload: dict = {"message": message, "citations_enabled": citations_enabled}
        if session_id:
            payload["session_id"] = session_id
        if context_handler_id:
            payload["context_handler_id"] = context_handler_id
        # ... existing POST + 503 retry + parse_sse unchanged ...
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: client methods for sync run, context handlers, and context injection"`

---

### Task 3: Router-agent bootstrap module

**Files:**
- Create: `backend/app/services/router_agent.py`
- Test: `backend/tests/unit/test_router_agent.py`

**Interfaces:**
- Consumes: `client.list_agents() -> {"agents": [...]}`, `client.create_agent(name, model, system_prompt, settings)`.
- Produces: `ROUTER_AGENT_NAME: str`, `ROUTER_SYSTEM_PROMPT: str`, `GATE_RESPONSE_FORMAT: dict`, `ensure_router_agent(client, model) -> str`, `get_router_agent_id(request) -> str`.

- [ ] **Step 1: Write failing test** — `test_router_agent.py`:

```python
from app.services.router_agent import (
    ROUTER_AGENT_NAME,
    ensure_router_agent,
    GATE_RESPONSE_FORMAT,
)


class FakeClient:
    def __init__(self, existing=None):
        self.agents = list(existing or [])
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        agent = {"id": f"agent-{name}", "name": name, "settings": settings}
        self.agents.append(agent)
        self.created.append(agent)
        return agent


def test_ensure_router_agent_creates_when_absent():
    client = FakeClient()
    agent_id = ensure_router_agent(client, "gpt-4o-mini")
    assert agent_id == f"agent-{ROUTER_AGENT_NAME}"
    assert client.created[0]["name"] == ROUTER_AGENT_NAME
    assert client.created[0]["settings"] == {"temperature": 0}


def test_ensure_router_agent_reuses_when_present():
    client = FakeClient(existing=[{"id": "router-existing", "name": ROUTER_AGENT_NAME}])
    assert ensure_router_agent(client, "gpt-4o-mini") == "router-existing"
    assert client.created == []


def test_gate_response_format_shape():
    schema = GATE_RESPONSE_FORMAT["json_schema"]["schema"]
    assert schema["properties"]["needs_kb"]["type"] == "boolean"
    assert schema["required"] == ["needs_kb"]
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_router_agent.py -q` → FAIL (import error).

- [ ] **Step 3: Implement** — `router_agent.py`:

```python
from fastapi import Request

ROUTER_AGENT_NAME = "kb-router-agent"

ROUTER_SYSTEM_PROMPT = (
    "You decide whether answering a user's message requires retrieving from a "
    "knowledge base of the user's uploaded documents and curated general "
    "knowledge.\n"
    "- Return needs_kb=true if a good answer could depend on specific facts, "
    "documents, policies, data, product or domain details, or anything that "
    "would live in such a knowledge base — or if you are unsure.\n"
    "- Return needs_kb=false ONLY when the message clearly needs no such lookup: "
    "greetings, small talk, thanks, meta questions about the conversation "
    "itself, or basic general knowledge you already know.\n"
    "When in doubt, choose true. Respond only as JSON."
)

GATE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "kb_gate",
        "schema": {
            "type": "object",
            "properties": {"needs_kb": {"type": "boolean"}},
            "required": ["needs_kb"],
            "additionalProperties": False,
        },
    },
}


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def ensure_router_agent(client, model: str) -> str:
    """Find-or-create the shared KB-router agent; return its id."""
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, ROUTER_AGENT_NAME)
    if agent is None:
        agent = client.create_agent(
            ROUTER_AGENT_NAME,
            model=model,
            system_prompt=ROUTER_SYSTEM_PROMPT,
            settings={"temperature": 0},
        )
    return agent["id"]


def get_router_agent_id(request: Request) -> str:
    """FastAPI dependency returning the router agent id resolved at startup."""
    return request.app.state.router_agent_id
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_router_agent.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: shared KB-router agent bootstrap"`

---

### Task 4: Bootstrap the router agent at startup

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: `ensure_router_agent(client, model)` (Task 3), `settings.router_agent_model` (Task 1).
- Produces: `app.state.router_agent_id`.

- [ ] **Step 1: Update the failing test** — in `test_main_lifespan.py`, `test_app_starts_when_powabase_reachable`, add after the `ensure_general_kb` monkeypatch:

```python
    monkeypatch.setattr(main_module, "ensure_router_agent", lambda client, model: "router-1")
```

and inside the `with TestClient(app)` block add:

```python
        assert app.state.router_agent_id == "router-1"
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_main_lifespan.py -q` → FAIL (AttributeError: ensure_router_agent, then missing state).

- [ ] **Step 3: Implement** — in `main.py`:

Add import: `from app.services.router_agent import ensure_router_agent`.

In `lifespan`, inside the inner `try`, after `general_kb_id = ensure_general_kb(client)`:

```python
            router_agent_id = ensure_router_agent(client, settings.router_agent_model)
```

After `app.state.general_kb_id = general_kb_id` add:

```python
        app.state.router_agent_id = router_agent_id
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_main_lifespan.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: ensure router agent at startup"`

---

### Task 5: GateService

**Files:**
- Create: `backend/app/services/gate_service.py`
- Test: `backend/tests/unit/test_gate_service.py`

**Interfaces:**
- Consumes: `client.run_agent_sync(agent_id, message, response_format)` (Task 2), `GATE_RESPONSE_FORMAT` (Task 3).
- Produces: `GateService(client, router_agent_id)`, `GateService.needs_kb(query: str, history: list | None = None) -> bool`.

- [ ] **Step 1: Write failing tests** — `test_gate_service.py`:

```python
from app.services.gate_service import GateService


class FakeClient:
    def __init__(self, content=None, raises=None):
        self.content = content
        self.raises = raises
        self.calls = []

    def run_agent_sync(self, agent_id, message, response_format=None):
        self.calls.append({"agent_id": agent_id, "message": message, "response_format": response_format})
        if self.raises:
            raise self.raises
        return {"content": self.content}


def test_needs_kb_true_when_gate_says_true():
    client = FakeClient(content='{"needs_kb": true}')
    assert GateService(client, "router-1").needs_kb("what does the doc say?") is True
    assert client.calls[0]["agent_id"] == "router-1"
    assert client.calls[0]["response_format"] is not None


def test_needs_kb_false_when_gate_says_false():
    client = FakeClient(content='{"needs_kb": false}')
    assert GateService(client, "router-1").needs_kb("hello there") is False


def test_needs_kb_fails_safe_to_true_on_unparseable_content():
    client = FakeClient(content="not json")
    assert GateService(client, "router-1").needs_kb("hi") is True


def test_needs_kb_fails_safe_to_true_on_client_error():
    client = FakeClient(raises=RuntimeError("boom"))
    assert GateService(client, "router-1").needs_kb("hi") is True


def test_needs_kb_includes_history_in_message():
    client = FakeClient(content='{"needs_kb": true}')
    GateService(client, "router-1").needs_kb(
        "and the second one?",
        history=[{"role": "user", "text": "what is clause 1?"},
                 {"role": "assistant", "text": "Clause 1 says X."}],
    )
    msg = client.calls[0]["message"]
    assert "what is clause 1?" in msg
    assert "and the second one?" in msg
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_gate_service.py -q` → FAIL (import error).

- [ ] **Step 3: Implement** — `gate_service.py`:

```python
from __future__ import annotations

import json

from app.services.router_agent import GATE_RESPONSE_FORMAT


class GateService:
    """Decides whether a chat message needs the knowledge base.

    Uses the shared router agent (a single cheap sync LLM call). Biased to
    retrieve: any error or unparseable output resolves to True.
    """

    def __init__(self, client, router_agent_id: str):
        self.client = client
        self.router_agent_id = router_agent_id

    def needs_kb(self, query: str, history: list | None = None) -> bool:
        message = self._build_message(query, history or [])
        try:
            response = self.client.run_agent_sync(
                self.router_agent_id, message, response_format=GATE_RESPONSE_FORMAT
            )
            return bool(json.loads(response["content"])["needs_kb"])
        except Exception:
            # Fail safe: when the gate can't decide, retrieve (grounded answer).
            return True

    @staticmethod
    def _build_message(query: str, history: list) -> str:
        lines = []
        if history:
            lines.append("Recent conversation:")
            for turn in history:
                lines.append(f"{turn.get('role', 'user')}: {turn.get('text', '')}")
            lines.append("")
        lines.append(f"Current user message: {query}")
        lines.append("Does answering the current message require the knowledge base?")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_gate_service.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: GateService (LLM decides if KB retrieval is needed)"`

---

### Task 6: Stop linking KBs in session provisioning

**Files:**
- Modify: `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_session_service.py`

**Interfaces:**
- Changes `create_session` behavior: still creates KB + agent, **no longer** calls `link_kb_to_agent`.

- [ ] **Step 1: Update the tests** — in `test_session_service.py`:

Replace the link assertion in `test_create_session_provisions_and_inserts` (`assert client.links == [(row["agent_id"], row["kb_id"])]`) with:

```python
    assert client.links == []  # detached model: KBs are no longer linked to the agent
```

Replace the two link-behavior tests `test_create_session_links_general_kb_when_set` and `test_create_session_links_only_session_kb_when_general_none` with:

```python
def test_create_session_does_not_link_kbs_even_with_general_kb(self=None):
    client = FakeClient()
    service = SessionService(client, model="m", general_kb_id="gkb-1")
    service.create_session("alice")
    assert client.links == []  # detached model: no knowledge_search linking
```

(Remove the old `def test_create_session_links_*` functions entirely.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_session_service.py -q` → FAIL (links still populated).

- [ ] **Step 3: Implement** — in `session_service.py` `create_session`, delete these lines:

```python
        self.client.link_kb_to_agent(agent["id"], kb["id"])
        if self.general_kb_id:
            self.client.link_kb_to_agent(agent["id"], self.general_kb_id)
```

Keep the KB + agent creation and the row insert. (`general_kb_id` stays on the service; it is still used by the chat layer for retrieval — do not remove the constructor param.)

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/unit/test_session_service.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: detach KB from agent (stop linking on session create)"`

---

### Task 7: Wire gating into the chat path

**Files:**
- Modify: `backend/app/services/chat_service.py`
- Modify: `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_chat_service.py`
- Test: `backend/tests/unit/test_routes_chat.py`

**Interfaces:**
- Consumes: `GateService` (Task 5), `client.create_context_handler` + `run_agent(context_handler_id=…)` (Task 2), `get_general_kb_id` (existing), `get_router_agent_id` (Task 3), `get_settings` (existing), `client.get_session_messages` (existing).
- Produces: `ChatService(client, agent_id, gate, retrieval_kb_ids, top_k, max_context_tokens)`; `ChatService.ask(query, session_id=None, history=None) -> {answer, session_id, citations}`.

- [ ] **Step 1: Update `test_chat_service.py`** — replace the `FakeClient` and `ChatService(...)` construction so every test uses the new signature and a fake gate. At the top, after imports, add:

```python
class FakeGate:
    def __init__(self, needs=True):
        self.needs = needs
        self.calls = []

    def needs_kb(self, query, history=None):
        self.calls.append((query, history))
        return self.needs
```

Change `FakeClient` to record context-handler creation and accept the new kwarg:

```python
class FakeClient:
    def __init__(self, events, handler_id="handler-1"):
        self.events = events
        self.handler_id = handler_id
        self.calls = []
        self.handler_calls = []

    def create_context_handler(self, query, knowledge_bases, max_context_tokens=None):
        self.handler_calls.append({"query": query, "knowledge_bases": knowledge_bases,
                                   "max_context_tokens": max_context_tokens})
        return {"id": self.handler_id}

    def run_agent(self, agent_id, message, session_id=None, citations_enabled=True, context_handler_id=None):
        self.calls.append({"agent_id": agent_id, "message": message, "session_id": session_id,
                           "context_handler_id": context_handler_id})
        return self.events
```

In every existing test, change `ChatService(client, agent_id="agent-1")` to:

```python
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)
```

(Use `FakeGate(needs=False)` for the error-path tests so they don't require a handler; the error events fire regardless.) Then add two new routing tests:

```python
def test_ask_retrieves_and_injects_when_gate_true():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "grounded", "citations": [{"source_id": "s"}]}},
    ])
    service = ChatService(client, "agent-1", FakeGate(needs=True), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("what does the doc say?", session_id="ps-1")

    assert result["answer"] == "grounded"
    assert client.handler_calls[0]["knowledge_bases"] == [{"id": "kb-s", "top_k": 4}, {"id": "gkb-1", "top_k": 4}]
    assert client.calls[0]["context_handler_id"] == "handler-1"


def test_ask_skips_retrieval_when_gate_false():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "hi there", "citations": []}},
    ])
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("hello")

    assert result["answer"] == "hi there"
    assert client.handler_calls == []                      # no retrieval
    assert client.calls[0]["context_handler_id"] is None   # no injection
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_chat_service.py -q` → FAIL.

- [ ] **Step 3: Implement `chat_service.py`** — change the class to orchestrate; keep the SSE parse loop and `_raise_for_error` exactly as they are today:

```python
class ChatService:
    def __init__(self, client, agent_id, gate, retrieval_kb_ids, top_k, max_context_tokens):
        self.client = client
        self.agent_id = agent_id
        self.gate = gate
        self.retrieval_kb_ids = retrieval_kb_ids
        self.top_k = top_k
        self.max_context_tokens = max_context_tokens

    def ask(self, query: str, session_id: str | None = None, history: list | None = None) -> dict:
        context_handler_id = None
        if self.gate.needs_kb(query, history or []):
            knowledge_bases = [
                {"id": kb_id, "top_k": self.top_k}
                for kb_id in self.retrieval_kb_ids if kb_id
            ]
            handler = self.client.create_context_handler(
                query, knowledge_bases, self.max_context_tokens
            )
            context_handler_id = handler["id"]

        events = self.client.run_agent(
            self.agent_id, query, session_id=session_id,
            citations_enabled=True, context_handler_id=context_handler_id,
        )
        # ---- existing parse loop unchanged from here ----
        answer = None
        citations: list = []
        result_session_id = session_id
        for event in events:
            name = event["event"]
            data = event["data"]
            if name == "start":
                result_session_id = data.get("session_id", result_session_id)
            elif name == "error":
                self._raise_for_error(
                    data.get("code", ""),
                    data.get("message") or data.get("error") or str(data),
                )
            elif name == "complete":
                if data.get("status") == "failed" or data.get("error"):
                    self._raise_for_error("", data.get("error") or "Agent run failed")
                answer = data.get("content")
                citations = data.get("citations", [])
        if not answer:
            raise RuntimeError("Agent run completed without a final answer")
        return {"answer": answer, "session_id": result_session_id, "citations": citations}
```

Keep the `_THROTTLE_SIGNALS`, exception classes, `_looks_throttled`, and `_raise_for_error` exactly as they are.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_chat_service.py -q` → PASS.

- [ ] **Step 5: Update `test_routes_chat.py`** — expand `build_app` and `FakeChatService` for the new wiring:

```python
from types import SimpleNamespace
from app.services.general_kb import get_general_kb_id
from app.services.router_agent import get_router_agent_id
from app.core.config import get_settings


class FakeChatService:
    def __init__(self, client, agent_id, gate, retrieval_kb_ids, top_k, max_context_tokens):
        assert agent_id == "agent-1"

    def ask(self, query, session_id=None, history=None):
        return {"answer": "42", "session_id": "ps-new", "citations": []}
```

In `build_app`, add overrides:

```python
    app.dependency_overrides[get_general_kb_id] = lambda: "gkb-1"
    app.dependency_overrides[get_router_agent_id] = lambda: "router-1"
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        retrieval_top_k=4, retrieval_max_context_tokens=2000, gate_history_turns=2
    )
```

Update every subclass in this file (`Insufficient`, `Busy`, `ProviderError`, `FailedRun`) so their `ask` signature is `def ask(self, query, session_id=None, history=None)`. (The `FakeSessionService.row` has `powabase_session_id=None`, so the route skips history-fetch and never touches the `object()` client — no further change needed.)

- [ ] **Step 6: Implement `chat.py`** — rewrite the route:

```python
from fastapi import APIRouter, Depends, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatService, InsufficientCreditsError, ModelBusyError, ProviderKeyError,
)
from app.services.gate_service import GateService
from app.services.general_kb import get_general_kb_id
from app.services.router_agent import get_router_agent_id
from app.services.session_service import DEFAULT_NAME, SessionService, get_session_service

router = APIRouter(prefix="/chat", tags=["chat"])


def _title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


def _recent_turns(raw, turns: int) -> list:
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    history = [
        {"role": m.get("role", "user"), "text": m.get("content") or m.get("text") or ""}
        for m in items
    ]
    return history[-(turns * 2):] if turns > 0 else []


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    general_kb_id: str = Depends(get_general_kb_id),
    router_agent_id: str = Depends(get_router_agent_id),
    settings=Depends(get_settings),
):
    row = sessions.get(req.session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    powabase_session_id = row.get("powabase_session_id")

    history: list = []
    if powabase_session_id:
        try:
            history = _recent_turns(
                client.get_session_messages(powabase_session_id), settings.gate_history_turns
            )
        except PowabaseAPIError:
            history = []

    gate = GateService(client, router_agent_id)
    service = ChatService(
        client, row["agent_id"], gate,
        [row["kb_id"], general_kb_id],
        settings.retrieval_top_k, settings.retrieval_max_context_tokens,
    )
    try:
        result = service.ask(req.query, session_id=powabase_session_id, history=history)
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

    try:
        sessions.touch(req.session_id, **updates)
    except (PowabaseAPIError, RuntimeError):
        pass

    return ChatResponse(answer=result["answer"], citations=result["citations"])
```

- [ ] **Step 7: Run the full suite** — `.venv/bin/python -m pytest -q` → all PASS.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat: gate KB retrieval per message and inject context explicitly"`

---

### Task 8: Live smoke verification

**Files:** none (verification only). Requires the real `.env` and network.

- [ ] **Step 1: Restart the server** — kill any running uvicorn, then:
  `cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &`
  Confirm `GET /health` returns `{"status":"ok","model":"gpt-4o-mini"}`.

- [ ] **Step 2: Skip path** — create a session (`POST /sessions {"user":"smoke"}`), then `POST /chat {"session_id": …, "query": "hi, how are you?"}`. Expect a normal reply with **`citations: []`** (gate skipped retrieval).

- [ ] **Step 3: Retrieve path** — upload a small PDF with a distinctive fact to that session (`POST /ingest/file`), then `POST /chat` asking about that fact. Expect the answer to contain the fact **and `citations` length ≥ 1** (gate retrieved + injected).

- [ ] **Step 4: Follow-up path** — send a short follow-up ("tell me more about that") and confirm it still answers with citations (history routed it to retrieve).

- [ ] **Step 5: Clean up** — delete the smoke session (`DELETE /sessions/{id}`).

- [ ] **Step 6: Record** — note the observed citations counts in the task report. No commit.

---

## Self-Review

- **Spec coverage:** gate (Tasks 3–5), detached retrieval/injection (Tasks 2, 7), keep per-session agents but stop linking (Task 6), retrieve-when-unsure bias (Task 5 prompt + fail-safe), history-aware gate (Tasks 5, 7), config (Task 1), startup wiring (Task 4), tests + live smoke (all tasks, Task 8). Covered.
- **Placeholder scan:** none — every code step has complete code.
- **Type/name consistency:** `run_agent_sync`, `create_context_handler`, `run_agent(context_handler_id=…)`, `ensure_router_agent`, `GATE_RESPONSE_FORMAT`, `GateService.needs_kb`, `ChatService(client, agent_id, gate, retrieval_kb_ids, top_k, max_context_tokens)`, `get_router_agent_id`, `get_general_kb_id`, `get_settings` are used identically across producer and consumer tasks.
- **Ordering:** each task leaves the suite green (Tasks 1–5 additive/independent; Task 6 independent; Task 7 swaps ChatService + route + their tests atomically).
