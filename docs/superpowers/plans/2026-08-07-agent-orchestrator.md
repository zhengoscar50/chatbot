# Agent Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One chat backed by the user's whole roster — an orchestrator routes each message to the best-suited agent and relays that agent's answer verbatim.

**Architecture:** A single JSON-schema call returns `{agent_id, needs_kb}`, replacing the separate retrieval gate. `sessions.agent_id` is dropped so chats belong to users, not agents. A shared general assistant answers when no specialist fits. `ChatService` stops owning the gate and takes a plain `retrieve` boolean instead.

**Tech Stack:** FastAPI, httpx, PostgREST/Powabase, pytest + respx, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-08-07-agent-orchestrator-design.md`

## Global Constraints

- **Python 3.9.6.** New modules with module-level `X | None` need `from __future__ import annotations`.
- **`app/models/schemas.py` has NO `from __future__ import annotations`** and must not get one. Use `Optional[X]`, never `X | None`. `list[X]`/`dict[K, V]` are fine.
- **Never commit secrets.**
- **Keep the suite green after every task**: `cd backend && .venv/bin/python -m pytest -q`. Baseline is **256 passing**.
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.
- **Ownership rule:** not yours → **404**, never 403.
- **Fail-safe orchestration:** any error, unparseable output, or an `agent_id` not in the caller's roster resolves to the general assistant with `needs_kb: True`. Never raise from routing.
- **The general assistant must never see a specialist's permanent KBs.** That would leak one agent's documents into an answer attributed to another.

---

## File Structure

**Delete:**
- `backend/app/services/gate_service.py`, `backend/app/services/router_agent.py`
- `backend/tests/unit/test_gate_service.py`, `backend/tests/unit/test_router_agent.py` (whichever exist)

**Create:**
- `backend/migrations/005_agent_orchestrator.sql`
- `backend/app/services/orchestrator.py` — routing agent bootstrap + `OrchestratorService`
- `backend/app/services/general_assistant.py` — shared fallback agent bootstrap
- `backend/tests/unit/test_orchestrator.py`
- `backend/tests/unit/test_general_assistant.py`

**Modify:**
- `backend/app/core/config.py` — `general_assistant_model`
- `backend/app/main.py` — swap router-agent bootstrap for orchestrator + general assistant
- `backend/app/models/schemas.py` — `description`; `ChatResponse.answered_by`; `SessionCreateRequest` loses `agent_id`
- `backend/app/services/agent_service.py` — carry `description`
- `backend/app/services/retrieval_scope.py` — general-assistant branch
- `backend/app/services/chat_service.py` — `gate` → `retrieve: bool`
- `backend/app/api/routes/agents.py`, `chat.py`, `sessions.py`
- `frontend/index.html`, `frontend/app.js`, `frontend/agents.js`, `frontend/styles.css`
- `README.md`

---

### Task 1: Migration + agent descriptions

**Files:**
- Create: `backend/migrations/005_agent_orchestrator.sql`
- Modify: `backend/app/models/schemas.py`, `backend/app/services/agent_service.py`, `backend/app/api/routes/agents.py`
- Test: `backend/tests/unit/test_agent_service.py`, `backend/tests/unit/test_routes_agents.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `agents.description` persisted end to end; `AgentService.create(owner_id, name, instructions, description, model, grounding, use_general_kb)`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/005_agent_orchestrator.sql`:

```sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Non-destructive: existing chats keep their history. sessions.agent_id is
-- dropped because a chat is no longer bound to one agent — the orchestrator
-- picks per message from the whole roster. Dropping the column also drops its
-- index and foreign key.

alter table public.agents
  add column if not exists description text not null default '';

drop index if exists sessions_agent_idx;
alter table public.sessions drop column if exists agent_id;
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/unit/test_agent_service.py`:

```python
def test_create_persists_the_routing_description():
    c = FakeClient()
    row = AgentService(c).create(
        "o1", "Tutor", "Be terse.", "Answers AP Chemistry questions.",
        "gpt-4o-mini", "strict", False,
    )
    assert row["description"] == "Answers AP Chemistry questions."


def test_description_is_local_only_and_skips_the_remote_patch():
    # The description exists for routing; Powabase knows nothing about it.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "old desc", "m", "strict", False)
    c.updated_agents.clear()

    svc.update(row, {"description": "new desc"})

    assert c.updated_agents == []
    assert c.rows["ag-1"]["description"] == "new desc"
```

Append to `backend/tests/unit/test_routes_agents.py`:

```python
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
```

Update `FakeAgentService.create` in that file to accept `description` as the 4th positional argument and store it, and `AgentSummary` assertions accordingly.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_agent_service.py tests/unit/test_routes_agents.py -q`
Expected: FAIL — `create() takes 7 positional arguments but 8 were given`

- [ ] **Step 4: Add `description` to the schemas**

In `app/models/schemas.py`, add to `AgentCreateRequest`:

```python
    description: str = Field(default="", max_length=500)
```

to `AgentUpdateRequest`:

```python
    description: Optional[str] = Field(default=None, max_length=500)
```

and to both `AgentResponse` and `AgentSummary`:

```python
    description: str = ""
```

- [ ] **Step 5: Thread it through AgentService**

In `app/services/agent_service.py`, `create` takes `description` after `instructions` and writes it into the row:

```python
    def create(
        self,
        owner_id: str,
        name: str,
        instructions: str,
        description: str,
        model: str,
        grounding: str,
        use_general_kb: bool,
    ) -> dict:
        self.probe_model(model)
        prompt = compose_system_prompt(instructions, grounding)
        agent = self.client.create_agent(
            f"user-agent-{name}", model=model, system_prompt=prompt
        )
        return self.client.insert_agent_row({
            "owner_id": owner_id,
            "name": name,
            "instructions": instructions,
            "description": description,
            "model": model,
            "grounding": grounding,
            "use_general_kb": use_general_kb,
            "powabase_agent_id": agent["id"],
            "kb_id": None,
            "kb_full_id": None,
        })
```

`REMOTE_FIELDS` is unchanged — `description` is local-only, so editing it must not patch the remote agent.

- [ ] **Step 6: Thread it through the routes**

In `app/api/routes/agents.py`, `_to_response` gains `description=row.get("description") or ""`, the `AgentSummary` construction in `list_agents` gains the same, and `create_agent` passes `req.description` after `req.instructions`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Apply the migration**

Paste `backend/migrations/005_agent_orchestrator.sql` into the Powabase Studio SQL Editor and run it. Verify:

```bash
.venv/bin/python -c "
import httpx
from dotenv import dotenv_values
c=dotenv_values('.env'); b=c['POWABASE_BASE_URL'].rstrip('/'); k=c['POWABASE_SERVICE_ROLE_KEY']
h={'apikey':k,'Authorization':'Bearer '+k}
print('description col ->', httpx.get(b+'/rest/v1/agents?limit=1&select=description',headers=h,timeout=20).status_code, '(200 = present)')
print('agent_id col    ->', httpx.get(b+'/rest/v1/sessions?limit=1&select=agent_id',headers=h,timeout=20).status_code, '(400 = dropped)')
"
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: agents carry a routing description; drop sessions.agent_id"
```

---

### Task 2: The orchestrator

**Files:**
- Create: `backend/app/services/orchestrator.py`, `backend/tests/unit/test_orchestrator.py`
- Delete: `backend/app/services/gate_service.py`, `backend/app/services/router_agent.py`, and their tests

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ensure_orchestrator_agent(client, model) -> str`
  - `get_orchestrator_agent_id(request) -> str`
  - `OrchestratorService(client, orchestrator_agent_id)` with
    `route(query, roster, history=None) -> Decision`
  - `Decision` = `namedtuple("Decision", "agent_id needs_kb")`; `agent_id` is None for the general assistant.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_orchestrator.py`:

```python
import json

from app.services.orchestrator import (
    ORCHESTRATOR_AGENT_NAME,
    OrchestratorService,
    ensure_orchestrator_agent,
)

ROSTER = [
    {"id": "ag-chem", "name": "Chem tutor", "description": "AP Chemistry course material."},
    {"id": "ag-legal", "name": "Contracts", "description": "Our vendor contracts and NDAs."},
]


class FakeClient:
    def __init__(self, content=None, raises=False):
        self.content = content
        self.raises = raises
        self.calls = []
        self.agents = []

    def run_agent_sync(self, agent_id, message, response_format=None):
        self.calls.append((agent_id, message, response_format))
        if self.raises:
            raise RuntimeError("provider down")
        return {"content": self.content}

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        a = {"id": f"a-{name}", "name": name}
        self.agents.append(a)
        return a


def decision(content, roster=ROSTER, **kw):
    c = FakeClient(content=content)
    return c, OrchestratorService(c, "orch-1").route("q", roster, **kw)


def test_routes_to_the_named_agent():
    _, d = decision(json.dumps({"agent_id": "ag-legal", "needs_kb": True}))
    assert d.agent_id == "ag-legal"
    assert d.needs_kb is True


def test_null_agent_id_means_the_general_assistant():
    _, d = decision(json.dumps({"agent_id": None, "needs_kb": False}))
    assert d.agent_id is None
    assert d.needs_kb is False


def test_an_agent_id_outside_the_roster_is_rejected():
    # A hallucinated id must never be trusted — it could name another user's
    # agent, or nothing at all.
    _, d = decision(json.dumps({"agent_id": "ag-not-mine", "needs_kb": True}))
    assert d.agent_id is None
    assert d.needs_kb is True


def test_unparseable_output_falls_back_to_the_general_assistant():
    _, d = decision("not json at all")
    assert d.agent_id is None
    assert d.needs_kb is True


def test_missing_needs_kb_defaults_to_retrieving():
    _, d = decision(json.dumps({"agent_id": "ag-chem"}))
    assert d.agent_id == "ag-chem"
    assert d.needs_kb is True


def test_a_provider_error_never_raises():
    c = FakeClient(raises=True)
    d = OrchestratorService(c, "orch-1").route("q", ROSTER)
    assert d.agent_id is None and d.needs_kb is True


def test_an_empty_roster_skips_the_llm_call_entirely():
    # Nothing to choose between: don't pay for a routing call.
    c = FakeClient(content=json.dumps({"agent_id": "x", "needs_kb": True}))
    d = OrchestratorService(c, "orch-1").route("q", [])
    assert d.agent_id is None and d.needs_kb is True
    assert c.calls == []


def test_the_prompt_carries_the_roster_and_recent_turns():
    c, _ = decision(
        json.dumps({"agent_id": "ag-chem", "needs_kb": True}),
        history=[{"role": "user", "text": "what is a mole?"}],
    )
    message = c.calls[0][1]
    assert "ag-chem" in message and "AP Chemistry course material." in message
    assert "ag-legal" in message
    assert "what is a mole?" in message


def test_bootstrap_is_find_or_create():
    c = FakeClient()
    first = ensure_orchestrator_agent(c, "gpt-4o-mini")
    c.agents.append({"id": first, "name": ORCHESTRATOR_AGENT_NAME})
    assert ensure_orchestrator_agent(c, "gpt-4o-mini") == first
    assert len(c.agents) == 2  # one created, one appended by this test
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_orchestrator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.orchestrator'`

- [ ] **Step 3: Implement**

Create `backend/app/services/orchestrator.py`:

```python
from __future__ import annotations

import json
from collections import namedtuple

from fastapi import Request

ORCHESTRATOR_AGENT_NAME = "agent-orchestrator"

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You route a user's message to the assistant best suited to answer it.\n"
    "You are given a roster of the user's assistants, each with an id, a name "
    "and a description of what it covers, plus the recent conversation.\n"
    "- Set agent_id to the id of the assistant whose description covers the "
    "message. Choose exactly one.\n"
    "- Set agent_id to null when no assistant covers it, or for greetings, "
    "small talk and general questions — a general assistant handles those.\n"
    "- For a follow-up that continues the previous exchange (\"explain that "
    "again\", \"why?\"), keep the assistant that just answered.\n"
    "- Set needs_kb to true if answering could depend on specific documents, "
    "facts or data; false only for greetings, small talk, or questions needing "
    "no lookup. When unsure, choose true.\n"
    "Respond only as JSON."
)

ROUTE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_route",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": ["string", "null"]},
                "needs_kb": {"type": "boolean"},
            },
            "required": ["agent_id", "needs_kb"],
            "additionalProperties": False,
        },
    },
}

# agent_id is None when the general assistant should answer.
Decision = namedtuple("Decision", "agent_id needs_kb")

GENERAL = Decision(None, True)


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def ensure_orchestrator_agent(client, model: str) -> str:
    """Find-or-create the shared routing agent; return its id."""
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, ORCHESTRATOR_AGENT_NAME)
    if agent is None:
        agent = client.create_agent(
            ORCHESTRATOR_AGENT_NAME,
            model=model,
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            settings={"temperature": 0},
        )
    return agent["id"]


class OrchestratorService:
    """Chooses which agent answers, and whether to retrieve.

    One cheap sync LLM call decides both, so adding routing costs no extra
    round trip over the retrieval gate it replaces.

    Never raises: any failure resolves to the general assistant with retrieval
    on, so a broken router degrades to a working chatbot.
    """

    def __init__(self, client, orchestrator_agent_id: str):
        self.client = client
        self.orchestrator_agent_id = orchestrator_agent_id

    def route(self, query: str, roster: list, history: list | None = None) -> Decision:
        if not roster:
            # Nothing to choose between — don't pay for the call.
            return GENERAL
        try:
            response = self.client.run_agent_sync(
                self.orchestrator_agent_id,
                self._build_message(query, roster, history or []),
                response_format=ROUTE_RESPONSE_FORMAT,
            )
            data = json.loads(response["content"])
        except Exception:
            return GENERAL

        needs_kb = data.get("needs_kb")
        needs_kb = True if needs_kb is None else bool(needs_kb)

        agent_id = data.get("agent_id")
        # Never trust an id the model invented: it could name another user's
        # agent, or nothing at all.
        if agent_id not in {a["id"] for a in roster}:
            agent_id = None
        return Decision(agent_id, needs_kb)

    @staticmethod
    def _build_message(query: str, roster: list, history: list) -> str:
        lines = ["Available assistants:"]
        for agent in roster:
            description = (agent.get("description") or "").strip() or "(no description)"
            lines.append(f"- id={agent['id']} | name={agent['name']} | covers: {description}")
        if history:
            lines.append("")
            lines.append("Recent conversation:")
            for turn in history:
                lines.append(f"{turn.get('role', 'user')}: {turn.get('text', '')}")
        lines.append("")
        lines.append(f"Current user message: {query}")
        lines.append("Which assistant should answer, and is a document lookup needed?")
        return "\n".join(lines)


def get_orchestrator_agent_id(request: Request) -> str:
    """FastAPI dependency returning the orchestrator agent id from startup."""
    return request.app.state.orchestrator_agent_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_orchestrator.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Delete the gate**

```bash
git rm app/services/gate_service.py app/services/router_agent.py
git rm tests/unit/test_gate_service.py tests/unit/test_router_agent.py
```

Leave the imports in `chat.py` broken for now — Task 6 fixes them.

- [ ] **Step 6: Rename the two settings the gate named**

With the gate gone, `router_agent_model` and `gate_history_turns` describe code that no longer exists. Neither is set in `.env` (defaults only), so renaming is safe. In `app/core/config.py`:

```python
    orchestrator_model: str = "gpt-4o-mini"
    history_turns: int = 2
```

Update every reference — `grep -rn "router_agent_model\|gate_history_turns" app tests` must come back empty — including the assertions in `tests/unit/test_config.py`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: orchestrator routes to an agent and decides retrieval in one call"
```

---

### Task 3: The general assistant

**Files:**
- Create: `backend/app/services/general_assistant.py`, `backend/tests/unit/test_general_assistant.py`
- Modify: `backend/app/core/config.py`

**Interfaces:**
- Consumes: `compose_system_prompt` (existing).
- Produces: `ensure_general_assistant(client, model) -> str`, `get_general_assistant_id(request) -> str`, `GENERAL_ASSISTANT_NAME`, `Settings.general_assistant_model`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_general_assistant.py`:

```python
from app.services.general_assistant import (
    GENERAL_ASSISTANT_NAME,
    ensure_general_assistant,
)
from app.services.prompts import OPEN_CLAUSE


class FakeClient:
    def __init__(self):
        self.agents = []
        self.created = []

    def list_agents(self):
        return {"agents": self.agents}

    def create_agent(self, name, model, system_prompt, settings=None):
        self.created.append((name, model, system_prompt))
        agent = {"id": f"a-{name}", "name": name}
        self.agents.append(agent)
        return agent


def test_creates_the_shared_assistant_when_absent():
    c = FakeClient()
    agent_id = ensure_general_assistant(c, "gpt-4o-mini")
    assert agent_id == f"a-{GENERAL_ASSISTANT_NAME}"
    assert c.created[0][1] == "gpt-4o-mini"


def test_is_find_or_create():
    c = FakeClient()
    first = ensure_general_assistant(c, "gpt-4o-mini")
    assert ensure_general_assistant(c, "gpt-4o-mini") == first
    assert len(c.created) == 1


def test_uses_open_grounding():
    # It answers questions no specialist covers, so refusing without context
    # would make it useless.
    c = FakeClient()
    ensure_general_assistant(c, "gpt-4o-mini")
    assert OPEN_CLAUSE in c.created[0][2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_general_assistant.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `backend/app/services/general_assistant.py`:

```python
from __future__ import annotations

from fastapi import Request

from app.services.prompts import compose_system_prompt

GENERAL_ASSISTANT_NAME = "general-assistant"

GENERAL_ASSISTANT_INSTRUCTIONS = (
    "You are a helpful general assistant. You answer questions that none of the "
    "user's specialist assistants cover, along with greetings and small talk."
)


def _find_by_name(items, name):
    return next((item for item in items if item.get("name") == name), None)


def ensure_general_assistant(client, model: str) -> str:
    """Find-or-create the shared fallback assistant; return its id.

    Open grounding, not strict: it exists to answer what no specialist covers,
    so refusing whenever there is no retrieved context would make it useless.
    """
    existing = client.list_agents().get("agents", [])
    agent = _find_by_name(existing, GENERAL_ASSISTANT_NAME)
    if agent is None:
        agent = client.create_agent(
            GENERAL_ASSISTANT_NAME,
            model=model,
            system_prompt=compose_system_prompt(GENERAL_ASSISTANT_INSTRUCTIONS, "open"),
        )
    return agent["id"]


def get_general_assistant_id(request: Request) -> str:
    """FastAPI dependency returning the general assistant id from startup."""
    return request.app.state.general_assistant_id
```

- [ ] **Step 4: Add the setting**

In `app/core/config.py`, below `default_agent_model`:

```python
    general_assistant_model: str = "gpt-4o-mini"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_general_assistant.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: shared general assistant for queries no specialist covers"
```

---

### Task 4: Retrieval scope for the general assistant

**Files:**
- Modify: `backend/app/services/retrieval_scope.py`
- Test: `backend/tests/unit/test_retrieval_scope.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `kb_ids_for(agent_row, session_row, general_kb_id)` accepting `agent_row=None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_retrieval_scope.py`:

```python
def test_general_assistant_sees_only_the_chat_scratch_and_general_kb():
    # No agent_row means the general assistant is answering. It must NEVER see
    # a specialist's permanent KBs — that would leak one agent's documents into
    # an answer the UI attributes to another.
    assert kb_ids_for(None, {"kb_id": "sc"}, "gen") == ["sc", "gen"]


def test_general_assistant_with_no_chat_uploads():
    assert kb_ids_for(None, None, "gen") == ["gen"]


def test_general_assistant_with_no_general_kb():
    assert kb_ids_for(None, {"kb_id": "sc"}, None) == ["sc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'get'`

- [ ] **Step 3: Implement**

Replace the body of `kb_ids_for` in `app/services/retrieval_scope.py`:

```python
def kb_ids_for(agent_row, session_row, general_kb_id) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    With a specialist: its permanent KBs (the curated tier), then this chat's
    scratch KB, then the shared general KB if the agent opted in.

    With ``agent_row=None`` the general assistant is answering: it sees this
    chat's scratch KB and the general KB, and never a specialist's permanent
    KBs — that would leak one agent's documents into an answer attributed to
    another.

    Falsy ids are dropped, so an untrained agent with no uploads yields [] and
    answers from the model, which is correct rather than a failure.
    """
    ids: list = []
    if agent_row:
        ids.extend([agent_row.get("kb_id"), agent_row.get("kb_full_id")])
    if session_row:
        ids.append(session_row.get("kb_id"))
    if agent_row is None or agent_row.get("use_general_kb"):
        ids.append(general_kb_id)

    out: list = []
    for kb_id in ids:
        if kb_id and kb_id not in out:
            out.append(kb_id)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: retrieval scope for the general assistant"
```

---

### Task 5: ChatService takes a decision, not a gate

**Files:**
- Modify: `backend/app/services/chat_service.py`
- Test: `backend/tests/unit/test_chat_service.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ChatService(client, agent_id, retrieval_kb_ids, top_k, max_context_tokens)` — the `gate` parameter is **removed** — and `ask(query, session_id=None, retrieve=True)`.

- [ ] **Step 1: Update the tests**

In `backend/tests/unit/test_chat_service.py`, delete `FakeGate` and rewrite every `ChatService(...)` construction to drop the gate argument. The two gate-behavior tests become:

```python
def test_ask_retrieves_when_the_decision_says_to():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "grounded", "citations": [{"source_id": "s"}]}},
    ])
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("what does the doc say?", session_id="ps-1", retrieve=True)

    assert result["answer"] == "grounded"
    assert client.handler_calls[0]["knowledge_bases"] == [
        {"id": "kb-s", "top_k": 4}, {"id": "gkb-1", "top_k": 4},
    ]
    assert client.calls[0]["context_handler_id"] == "handler-1"


def test_ask_skips_retrieval_when_the_decision_says_not_to():
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "hi there", "citations": []}},
    ])
    service = ChatService(client, "agent-1", ["kb-s"], top_k=4, max_context_tokens=2000)

    result = service.ask("hello", retrieve=False)

    assert result["answer"] == "hi there"
    assert client.handler_calls == []
    assert client.calls[0]["context_handler_id"] is None


def test_ask_skips_retrieval_when_there_are_no_knowledge_bases():
    # Sending an empty knowledge_bases list makes Powabase 400.
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "I'm an assistant.", "citations": []}},
    ])
    service = ChatService(client, "agent-1", [], top_k=4, max_context_tokens=2000)

    service.ask("what are you?", retrieve=True)

    assert client.handler_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_chat_service.py -q`
Expected: FAIL — `__init__() takes 6 positional arguments but 7 were given`

- [ ] **Step 3: Implement**

In `app/services/chat_service.py`, drop the `gate` parameter and take the decision as an argument:

```python
class ChatService:
    def __init__(self, client, agent_id, retrieval_kb_ids, top_k, max_context_tokens):
        self.client = client
        self.agent_id = agent_id
        self.retrieval_kb_ids = retrieval_kb_ids
        self.top_k = top_k
        self.max_context_tokens = max_context_tokens

    def ask(self, query: str, session_id: str | None = None, retrieve: bool = True) -> dict:
        context_handler_id = None
        knowledge_bases = [
            {"id": kb_id, "top_k": self.top_k}
            for kb_id in self.retrieval_kb_ids if kb_id
        ]
        # `retrieve` comes from the orchestrator, which decided routing and
        # retrieval in one call. An empty scope still skips retrieval: Powabase
        # rejects an empty knowledge_bases list.
        if retrieve and knowledge_bases:
            handler = self.client.create_context_handler(
                query, knowledge_bases, self.max_context_tokens
            )
            context_handler_id = handler["id"]
```

The rest of `ask` (the event loop and `_raise_for_error`) is unchanged. Delete the now-unused `history` parameter — history goes to the orchestrator now, not here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_chat_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: ChatService takes a retrieve decision instead of owning the gate"
```

---

### Task 6: Wire the chat route

**Files:**
- Modify: `backend/app/api/routes/chat.py`, `backend/app/models/schemas.py`, `backend/app/main.py`
- Test: `backend/tests/unit/test_routes_chat.py`, `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: Tasks 2-5.
- Produces: `POST /chat` routing per message; `ChatResponse.answered_by`.

- [ ] **Step 1: Add the response schema**

In `app/models/schemas.py`:

```python
class AnsweredBy(BaseModel):
    id: Optional[str] = None      # None when the general assistant answered
    name: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    answered_by: Optional[AnsweredBy] = None
```

- [ ] **Step 2: Write the failing tests**

Rewrite the fixtures in `backend/tests/unit/test_routes_chat.py`: `FakeSessionService.row` drops `agent_id`, `FakeAgentService` grows `list(owner_id)` returning the roster, and the app overrides `get_orchestrator_agent_id` and `get_general_assistant_id`. Add:

```python
def test_chat_routes_to_the_agent_the_orchestrator_picked(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route",
                        lambda self, q, roster, history=None: Decision("ag-1", True))
    LAST_CHAT_ARGS.clear()

    r = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "q"})

    assert LAST_CHAT_ARGS["agent_id"] == "pa-1"
    assert LAST_CHAT_ARGS["kb_ids"] == ["ag-chunk", "ag-full", "kb-s"]
    assert r.json()["answered_by"] == {"id": "ag-1", "name": "Chem tutor"}


def test_chat_falls_back_to_the_general_assistant(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route",
                        lambda self, q, roster, history=None: Decision(None, True))
    LAST_CHAT_ARGS.clear()

    r = post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert LAST_CHAT_ARGS["agent_id"] == "general-1"
    # Never a specialist's permanent KBs.
    assert LAST_CHAT_ARGS["kb_ids"] == ["kb-s", "gkb-1"]
    assert r.json()["answered_by"]["id"] is None


def test_chat_passes_the_retrieval_decision_through(monkeypatch):
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    monkeypatch.setattr(chat_route.OrchestratorService, "route",
                        lambda self, q, roster, history=None: Decision(None, False))

    post(TestClient(build_app(FakeSessionService())), {"session_id": "s1", "query": "hi"})

    assert LAST_CHAT_ARGS["retrieve"] is False
```

`FakeChatService.ask` records `retrieve` into `LAST_CHAT_ARGS`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_chat.py -q`
Expected: FAIL

- [ ] **Step 4: Implement the route**

In `app/api/routes/chat.py`, replace the agent resolution and `ChatService` construction:

```python
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    powabase_session_id = row.get("powabase_session_id")
    history = _load_history(client, powabase_session_id, settings.history_turns)

    roster = agents.list(user["id"])
    decision = OrchestratorService(client, orchestrator_agent_id).route(
        req.query, roster, history
    )

    agent_row = next((a for a in roster if a["id"] == decision.agent_id), None)
    if agent_row is not None:
        answering_agent_id = agent_row["powabase_agent_id"]
        answered_by = AnsweredBy(id=agent_row["id"], name=agent_row["name"])
    else:
        answering_agent_id = general_assistant_id
        answered_by = AnsweredBy(id=None, name="General assistant")

    service = ChatService(
        client, answering_agent_id,
        kb_ids_for(agent_row, row, general_kb_id),
        settings.retrieval_top_k, settings.retrieval_max_context_tokens,
    )
    try:
        result = service.ask(req.query, session_id=powabase_session_id,
                             retrieve=decision.needs_kb)
```

Add `answered_by=answered_by` to the returned `ChatResponse`. Swap the dependencies: drop `get_router_agent_id`, add

```python
    orchestrator_agent_id: str = Depends(get_orchestrator_agent_id),
    general_assistant_id: str = Depends(get_general_assistant_id),
```

Extract the existing history-loading block into `_load_history(client, powabase_session_id, turns)` so the route body stays readable.

- [ ] **Step 5: Update the lifespan**

In `app/main.py`, replace the `ensure_router_agent` import and call:

```python
from app.services.general_assistant import ensure_general_assistant
from app.services.orchestrator import ensure_orchestrator_agent
```

```python
            orchestrator_agent_id = ensure_orchestrator_agent(
                client, settings.orchestrator_model
            )
            general_assistant_id = ensure_general_assistant(
                client, settings.general_assistant_model
            )
```

```python
        app.state.orchestrator_agent_id = orchestrator_agent_id
        app.state.general_assistant_id = general_assistant_id
```

Remove `app.state.router_agent_id`. Update `tests/unit/test_main_lifespan.py` accordingly: patch `ensure_orchestrator_agent` and `ensure_general_assistant`, and assert the two new state attributes.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: chat routes per message and attributes the answering agent"
```

---

### Task 7: Chats stop taking an agent

**Files:**
- Modify: `backend/app/models/schemas.py`, `backend/app/api/routes/sessions.py`, `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_routes_sessions.py`, `backend/tests/unit/test_session_service.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `POST /sessions` accepting only `{name?}`; `SessionService.create_session(owner_id, name=None)`.

- [ ] **Step 1: Update the schema and route**

`SessionCreateRequest` loses `agent_id`:

```python
class SessionCreateRequest(BaseModel):
    name: Optional[str] = None
```

In `app/api/routes/sessions.py`, delete the agent-ownership check and the `AgentService` dependency from `create_session`, and call `sessions.create_session(user["id"], req.name)`.

- [ ] **Step 2: Update SessionService**

```python
    def create_session(self, owner_id: str, name: str | None = None) -> dict:
        """Create a chat. Chats belong to the user, not to an agent — the
        orchestrator picks an agent per message."""
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "name": name or DEFAULT_NAME,
        })
```

- [ ] **Step 3: Update the tests**

In `test_session_service.py`, drop `agent_id` from every `create_session` call and assertion; delete `test_create_session_binds_to_the_agent_and_creates_no_agent` and replace with:

```python
def test_create_session_creates_no_agent():
    # Chats belong to the user; the orchestrator picks an agent per message.
    c = FakeClient()
    row = SessionService(c).create_session("o1", "My chat")

    assert row["owner_id"] == "o1"
    assert row["name"] == "My chat"
    assert "agent_id" not in row
    assert c.created_agents == []
```

In `test_routes_sessions.py`, drop the `get_agent_service` override and the two agent-binding tests, and remove `agent_id` from create payloads.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: chats belong to the user, not to one agent"
```

---

### Task 8: Frontend

**Files:**
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/agents.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: Tasks 1, 6, 7.
- Produces: a Manage-agents list, a description field, per-message attribution badges, and no agent picker.

- [ ] **Step 1: Replace the agent bar with a manage button**

In `frontend/index.html`, replace the `.agent-bar` block:

```html
<button type="button" id="manage-agents" class="new-session">⚙ Manage agents</button>
```

Add a description field to the agent form, after the Name label:

```html
<label>What is this for?
  <input id="agent-description" maxlength="500"
         placeholder="Answers questions about our AP Chemistry course materials." />
</label>
```

Add an agent-list modal before the existing `#agent-modal`:

```html
<div id="agent-list-modal" class="modal" hidden>
  <div class="modal-card">
    <h2>Your agents</h2>
    <p class="muted">The orchestrator picks one per message, using the descriptions below.</p>
    <ul id="agent-list"></ul>
    <div class="modal-actions">
      <button type="button" id="agent-list-close">Close</button>
      <button type="button" id="agent-list-new">New agent</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Rework `frontend/agents.js`**

Delete `agentSelect`, `renderAgentSelect`, `updateAgentTooltip`, `selectedAgent`, `currentAgentId` and the `agentSelect` listener. Replace with a list renderer:

```javascript
const agentListModal = document.getElementById("agent-list-modal");
const agentList = document.getElementById("agent-list");
const agentDescriptionInput = document.getElementById("agent-description");

function openAgentList() {
  agentListModal.hidden = false;
  renderAgentList();
}

function renderAgentList() {
  agentList.innerHTML = "";
  if (agents.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No agents yet. Create one to give the orchestrator something to route to.";
    agentList.appendChild(li);
    return;
  }
  agents.forEach((a) => {
    const li = document.createElement("li");
    const main = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = a.name;
    main.appendChild(name);
    const desc = document.createElement("div");
    desc.className = "muted";
    desc.textContent = a.description || "No description — the orchestrator can't route to this reliably.";
    main.appendChild(desc);
    li.appendChild(main);
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => {
      agentListModal.hidden = true;
      openAgentModal(a.id);
    });
    li.appendChild(edit);
    agentList.appendChild(li);
  });
}
```

Wire in `wireAgents()`:

```javascript
  document.getElementById("manage-agents").addEventListener("click", openAgentList);
  document.getElementById("agent-list-close").addEventListener("click", () => {
    agentListModal.hidden = true;
  });
  document.getElementById("agent-list-new").addEventListener("click", () => {
    agentListModal.hidden = true;
    openAgentModal(null);
  });
```

`saveAgent` adds `description: agentDescriptionInput.value.trim()` to its payload; `loadAgentDetail` sets `agentDescriptionInput.value = a.description || ""`; `openAgentModal(null)` clears it. `loadAgents` drops the picker rendering and just refreshes `agents` plus `renderAgentList()` when the list modal is open. Delete `onAgentChanged` usage — `loadAgents` no longer drives chat state.

- [ ] **Step 3: Update `frontend/app.js`**

- Delete `onAgentChanged` and every `currentAgentId` reference.
- `enterApp()` calls `loadAgents()` then `loadSessions()`.
- `ensureSession()` and `createSession()` post `{}` / `{name}` — no `agent_id`, and no "create an agent first" guard: the general assistant handles a user with no agents.
- `appendMessage` for assistant turns accepts the attribution and renders a badge:

```javascript
function appendAgentBadge(contentEl, answeredBy) {
  if (!answeredBy || !answeredBy.name) return;
  const badge = document.createElement("span");
  badge.className = "agent-badge";
  badge.textContent = answeredBy.name;
  contentEl.appendChild(badge);
}
```

Call it where the assistant's reply is rendered, passing `body.answered_by`.

- [ ] **Step 4: Styles**

Append to `frontend/styles.css`:

```css
.agent-badge {
  display: inline-block;
  margin-top: 0.4rem;
  padding: 0.1rem 0.45rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.72rem;
  color: var(--text-muted);
}

#agent-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

#agent-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
}
```

- [ ] **Step 5: Verify both scripts parse**

Run: `node -c frontend/app.js && node -c frontend/agents.js`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: manage-agents list, routing descriptions, per-message attribution"
```

---

### Task 9: Live smoke verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm the migration ran** (Task 1, Step 8)

- [ ] **Step 2: Start the server**

```bash
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: startup complete, with `agent-orchestrator` and `general-assistant` present in the project's agent list.

- [ ] **Step 3: Verify each criterion**

- [ ] A user with **zero agents** gets a useful answer (general assistant), and `answered_by.id` is null.
- [ ] Create two agents with clearly distinct descriptions (e.g. a chemistry tutor and a contracts reviewer), train each on a document with a distinctive fact.
- [ ] A chemistry question routes to the chemistry agent — `answered_by.name` says so, and the answer cites its document.
- [ ] A contracts question **in the same chat** routes to the contracts agent.
- [ ] Neither answer contains the other agent's distinctive fact — no cross-agent leakage.
- [ ] "hi" routes to the general assistant and is answered normally, not refused.
- [ ] A follow-up ("say that again") stays with the agent that just answered.
- [ ] A PDF attached in the chat is answerable regardless of which agent answers.
- [ ] Deleting an agent mid-conversation leaves the chat usable — later messages route to whoever remains.

- [ ] **Step 4: Record results** in `.superpowers/sdd/progress.md` (gitignored).

- [ ] **Step 5: Clean up smoke agents and users.**

---

## Verification

Full suite: `cd backend && .venv/bin/python -m pytest -q`
Frontend: `node -c frontend/app.js && node -c frontend/agents.js`
No stale references: `grep -rn "GateService\|router_agent\|currentAgentId" backend/app frontend` → no matches
