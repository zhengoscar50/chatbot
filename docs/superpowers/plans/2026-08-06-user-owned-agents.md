# User-Owned Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace session-owned ephemeral agents with user-created, persistently-trained agents, and remove Deep Research.

**Architecture:** A new `public.agents` table owns the durable configuration (name, instructions, model, grounding, general-KB opt-in) and two lazily-created permanent knowledge bases. One Powabase agent is created per row — never per session. Sessions become conversations bound to an agent via `sessions.agent_id`, keeping only a chunk-only scratch KB. Retrieval composes up to four KB ids from the agent row, the session row, and the general KB.

**Tech Stack:** FastAPI, httpx, PostgREST/Powabase, pytest + respx, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-08-06-user-owned-agents-design.md`

## Global Constraints

- **Python 3.9.6.** New modules with module-level `X | None` need `from __future__ import annotations`.
- **`app/models/schemas.py` has NO `from __future__ import annotations`** and must not get one (Pydantic resolves these annotations at class-definition time). In that file use `Optional[X]`, never `X | None` — PEP 604 unions are 3.10+. `list[X]` and `dict[K, V]` are fine (PEP 585 landed in 3.9) and are already used there.
- **Ingest settings are named `poll_interval_seconds` and `ingest_max_wait_seconds`** (there is no `max_wait_seconds`). The background upload path uses `ingest_background_max_wait_seconds`.
- **Never commit secrets.** `.env` is gitignored; only `.env.example` is tracked.
- **Keep the suite green after every task**: `cd backend && .venv/bin/python -m pytest -q`. Baseline before Task 1 is **210 passing**; Task 1 removes ~35 research tests.
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.
- **Ownership rule, everywhere:** a resource that is not yours returns **404**, never 403. Mirror `SessionService.get_owned_session`.
- **Grounding values are exactly `"strict"` and `"open"`.** No other value is written or accepted.
- **Never delete a Powabase Source to untrain.** `upload_source` reuses duplicates on 409, so one source can belong to several KBs. Untrain removes the source *from the KB* only.
- Remote cleanup (agents, KBs) is **best-effort**: catch `PowabaseAPIError` and continue, so a stale remote resource never blocks a local row delete. This is the pattern `SessionService.delete` already uses.

---

## File Structure

**Delete:**
- `backend/app/api/routes/research.py`
- `backend/app/services/research_service.py`
- `backend/app/services/research_pipeline.py`
- `backend/tests/unit/test_research_service.py`
- `backend/tests/unit/test_research_pipeline.py`
- `backend/tests/unit/test_routes_research.py`

**Create:**
- `backend/migrations/004_user_owned_agents.sql` — agents table; drop/recreate sessions
- `backend/app/services/prompts.py` — `compose_system_prompt`, grounding clauses
- `backend/app/services/retrieval_scope.py` — `kb_ids_for`, the pure KB-composition function
- `backend/app/services/agent_service.py` — `AgentService` (CRUD, lazy KB provisioning, cascade)
- `backend/app/api/routes/agents.py` — the seven `/agents` routes
- `backend/tests/unit/test_prompts.py`
- `backend/tests/unit/test_retrieval_scope.py`
- `backend/tests/unit/test_agent_service.py`
- `backend/tests/unit/test_routes_agents.py`
- `frontend/agents.js` — agent list, create form, manage view

**Modify:**
- `backend/app/clients/powabase_client.py` — drop orchestration methods; add `update_agent`, agent-row CRUD, `remove_source_from_kb`, `list_sessions_for_agent`
- `backend/app/core/config.py` — drop 7 `research_*` settings; add `default_agent_model`
- `backend/app/main.py` — drop research bootstrap + `research_jobs`; add `AgentService` to state
- `backend/app/models/schemas.py` — drop research schemas; add agent schemas; `SessionCreateRequest.agent_id`
- `backend/app/services/session_service.py` — sessions bind to an agent; no agent creation; no `kb_full_id`
- `backend/app/api/routes/sessions.py` — require and validate `agent_id`
- `backend/app/api/routes/chat.py` — retrieval scope from agent + session + general
- `backend/app/api/routes/ingest.py` — scratch KB is chunk-only
- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`
- `README.md`

`agent_service.py` holds orchestration of remote + local state. The two pure functions live in their own modules (`prompts.py`, `retrieval_scope.py`) because they carry the design's load-bearing logic and must be testable without any client at all.

---

### Task 1: Remove Deep Research

**Files:**
- Delete: `backend/app/api/routes/research.py`, `backend/app/services/research_service.py`, `backend/app/services/research_pipeline.py`, `backend/tests/unit/test_research_service.py`, `backend/tests/unit/test_research_pipeline.py`, `backend/tests/unit/test_routes_research.py`
- Modify: `backend/app/main.py`, `backend/app/core/config.py`, `backend/app/models/schemas.py`, `backend/app/clients/powabase_client.py`, `backend/tests/unit/test_config.py`, `backend/tests/unit/test_main_lifespan.py`, `backend/tests/unit/test_powabase_client.py`, `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`, `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a codebase with no `research` symbol and no orchestration client methods.

- [ ] **Step 1: Delete the six feature files**

```bash
cd backend
git rm app/api/routes/research.py app/services/research_service.py app/services/research_pipeline.py
git rm tests/unit/test_research_service.py tests/unit/test_research_pipeline.py tests/unit/test_routes_research.py
```

- [ ] **Step 2: Strip research from `app/main.py`**

Remove the `research_router` import and `include_router` call, the `ensure_research_pipeline` import, the `research_orchestration_id` assignment, and `app.state.research_jobs = {}`. The lifespan body becomes:

```python
        try:
            client.list_agents()
            reranker_config = reranker_retrieval_config(
                settings.reranker_model, settings.reranker_candidate_count
            )
            general_kb_id = ensure_general_kb(client, reranker_config)
            router_agent_id = ensure_router_agent(client, settings.router_agent_model)
        except PowabaseAPIError as e:
            raise RuntimeError(f"Powabase is not reachable: {e}") from e
        app.state.powabase_client = client
        app.state.general_kb_id = general_kb_id
        app.state.router_agent_id = router_agent_id
        app.state.session_service = SessionService(
            client, settings.powabase_agent_model, general_kb_id, reranker_config
        )
```

- [ ] **Step 3: Strip research settings from `app/core/config.py`**

Delete these seven lines:

```python
    research_top_k: int = 12
    research_max_context_tokens: int = 24000
    research_max_concurrent_per_user: int = 2
    research_job_ttl_seconds: int = 1800
    research_researcher_model: str = "gpt-4o-mini"
    research_analyst_model: str = "claude-sonnet-5"
    research_writer_model: str = "gpt-4o-mini"
```

- [ ] **Step 4: Strip research schemas and orchestration client methods**

In `app/models/schemas.py`, delete `ResearchRequest`, `ResearchStartResponse`, `ResearchStatusResponse`.

In `app/clients/powabase_client.py`, delete `create_orchestration`, `add_orchestration_entity`, `list_orchestrations`, and `run_orchestration_stream`. If `json` is now unused at module level, leave it — `_flush` was its only consumer inside the deleted method, but other methods use `response.json()`, not the module. Verify with `grep -n "json\." app/clients/powabase_client.py`.

- [ ] **Step 5: Strip research from the frontend**

In `frontend/index.html`, delete the `<button id="research-button">` element.

In `frontend/app.js`, delete: the `researchButton` const, its click listener, `pollResearch`, `appendResearchCard`, `renderResearchDone`, `renderResearchFailed`, and the `researchButton.disabled` line inside `setComposerEnabled`.

In `frontend/styles.css`, delete the `.research-btn`, `.research-stage`, and `.research-report` rule blocks.

- [ ] **Step 6: Strip research from the remaining tests**

In `tests/unit/test_config.py`, delete assertions referencing any `research_*` setting.
In `tests/unit/test_main_lifespan.py`, delete assertions referencing `research_orchestration_id` or `research_jobs`.
In `tests/unit/test_powabase_client.py`, delete the four `test_run_orchestration_stream_*` tests and `test_create_orchestration_*`/`test_add_orchestration_entity*` if present.

- [ ] **Step 7: Update README**

Delete section 8 ("Deep Research") and renumber section 9 back to 8. In "Notes on the Powabase wire format", delete the two orchestration bullets (context injection, `list_orchestrations`/`sequential_step`) — they document deleted code.

- [ ] **Step 8: Verify nothing references research**

Run: `grep -rin "research\|orchestration" backend/app backend/tests frontend README.md`
Expected: no matches.

- [ ] **Step 9: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, ~175 tests (210 minus the ~35 research tests).

- [ ] **Step 10: Verify the frontend still parses**

Run: `node -c frontend/app.js`
Expected: no output (success).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat!: remove Deep Research

Removes the feature and its orchestration client methods ahead of the
user-owned agents rework. No remaining code depends on it."
```

- [ ] **Step 12: Clean up the live Powabase project**

Run this once against the real project. It deletes the orchestration and its three agents, which nothing will recreate now that the bootstrap is gone.

```bash
cd backend && .venv/bin/python -c "
import httpx
from dotenv import dotenv_values
c = dotenv_values('.env')
base = c['POWABASE_BASE_URL'].rstrip('/'); key = c['POWABASE_SERVICE_ROLE_KEY']
h = {'apikey': key, 'Authorization': 'Bearer ' + key}
for o in httpx.get(base + '/api/orchestrations', headers=h, timeout=30).json().get('orchestrations', []):
    if o['name'] == 'deep-research-pipeline':
        print('deleting orchestration', o['id'], httpx.delete(base + '/api/orchestrations/' + o['id'], headers=h, timeout=30).status_code)
for a in httpx.get(base + '/api/agents', headers=h, timeout=30).json().get('agents', []):
    if a['name'].startswith('research-'):
        print('deleting agent', a['name'], httpx.delete(base + '/api/agents/' + a['id'], headers=h, timeout=30).status_code)
"
```

---

### Task 2: Powabase client — agent update, agent rows, KB source removal

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PowabaseClient.update_agent(agent_id: str, fields: dict) -> dict`
  - `PowabaseClient.remove_source_from_kb(kb_id: str, source_id: str) -> None`
  - `PowabaseClient.insert_agent_row(row: dict) -> dict`
  - `PowabaseClient.list_agent_rows(owner_id: str) -> list`
  - `PowabaseClient.get_agent_row(agent_id: str) -> dict | None`
  - `PowabaseClient.update_agent_row(agent_id: str, fields: dict) -> None`
  - `PowabaseClient.delete_agent_row(agent_id: str) -> None`
  - `PowabaseClient.list_sessions_for_agent(agent_id: str) -> list`

The `_row` suffix is deliberate: `list_agents()` already means *Powabase* agents. Row methods talk to PostgREST at `/rest/v1/agents`; non-row methods talk to the agent API at `/api/agents`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_powabase_client.py`:

```python
@respx.mock
def test_update_agent_patches_fields():
    route = respx.patch(f"{BASE_URL}/api/agents/a-1").mock(
        return_value=httpx.Response(200, json={"id": "a-1", "model": "gpt-4o-mini"})
    )
    result = PowabaseClient(BASE_URL, "k").update_agent("a-1", {"model": "gpt-4o-mini"})
    assert result["model"] == "gpt-4o-mini"
    assert json.loads(route.calls[0].request.content) == {"model": "gpt-4o-mini"}


@respx.mock
def test_remove_source_from_kb_deletes_the_link_not_the_source():
    # upload_source reuses duplicates on 409, so one source can live in several
    # KBs. Untraining must unlink, never delete the source itself.
    route = respx.delete(f"{BASE_URL}/api/knowledge-bases/kb-1/sources/src-1").mock(
        return_value=httpx.Response(204)
    )
    PowabaseClient(BASE_URL, "k").remove_source_from_kb("kb-1", "src-1")
    assert route.called


@respx.mock
def test_insert_agent_row_returns_the_created_row():
    respx.post(f"{BASE_URL}/rest/v1/agents").mock(
        return_value=httpx.Response(201, json=[{"id": "ag-1", "name": "Tutor"}])
    )
    row = PowabaseClient(BASE_URL, "k").insert_agent_row({"name": "Tutor"})
    assert row["id"] == "ag-1"


@respx.mock
def test_list_agent_rows_filters_by_owner():
    route = respx.get(f"{BASE_URL}/rest/v1/agents").mock(
        return_value=httpx.Response(200, json=[{"id": "ag-1"}])
    )
    rows = PowabaseClient(BASE_URL, "k").list_agent_rows("o1")
    assert rows == [{"id": "ag-1"}]
    assert "owner_id=eq.o1" in str(route.calls[0].request.url)


@respx.mock
def test_get_agent_row_returns_none_when_absent():
    respx.get(f"{BASE_URL}/rest/v1/agents").mock(return_value=httpx.Response(200, json=[]))
    assert PowabaseClient(BASE_URL, "k").get_agent_row("nope") is None


@respx.mock
def test_list_sessions_for_agent_filters_by_agent_id():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s-1"}])
    )
    rows = PowabaseClient(BASE_URL, "k").list_sessions_for_agent("ag-1")
    assert rows == [{"id": "s-1"}]
    assert "agent_id=eq.ag-1" in str(route.calls[0].request.url)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q`
Expected: FAIL — `AttributeError: 'PowabaseClient' object has no attribute 'update_agent'`

- [ ] **Step 3: Implement the methods**

Add after `delete_agent` in `app/clients/powabase_client.py`:

```python
    def update_agent(self, agent_id: str, fields: dict) -> dict:
        """Patch an agent in place. Verified live: PATCH /api/agents/{id} is
        supported; PUT/POST are not. Editing in place (rather than recreating)
        keeps the agent id stable, so existing chat threads stay bound to it."""
        response = self._client.patch(f"/api/agents/{agent_id}", json=fields)
        self._raise_for_status(response)
        return response.json()

    def remove_source_from_kb(self, kb_id: str, source_id: str) -> None:
        """Unlink a source from a KB. Never deletes the Source itself —
        upload_source reuses duplicates on 409, so one source can belong to
        several KBs and deleting it would break the others."""
        response = self._client.delete(f"/api/knowledge-bases/{kb_id}/sources/{source_id}")
        self._raise_for_status(response)
```

Add an `# Agent rows -----` section alongside the existing session-row methods:

```python
    def insert_agent_row(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/agents", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        return response.json()[0]

    def list_agent_rows(self, owner_id: str) -> list:
        response = self._client.get(
            "/rest/v1/agents",
            params={"owner_id": f"eq.{owner_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def get_agent_row(self, agent_id: str):
        response = self._client.get("/rest/v1/agents", params={"id": f"eq.{agent_id}"})
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_agent_row(self, agent_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/agents", params={"id": f"eq.{agent_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_agent_row(self, agent_id: str) -> None:
        response = self._client.delete("/rest/v1/agents", params={"id": f"eq.{agent_id}"})
        self._raise_for_status(response)

    def list_sessions_for_agent(self, agent_id: str) -> list:
        response = self._client.get("/rest/v1/sessions", params={"agent_id": f"eq.{agent_id}"})
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/clients/powabase_client.py tests/unit/test_powabase_client.py
git commit -m "feat: client methods for agent update, agent rows, KB source removal"
```

---

### Task 3: Migration — agents table, sessions reshaped

**Files:**
- Create: `backend/migrations/004_user_owned_agents.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: `public.agents`; `public.sessions` with `agent_id uuid not null`, no `kb_full_id`, no `user_slug`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/004_user_owned_agents.sql`:

```sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- DESTRUCTIVE. Drops every existing session. Approved in the design spec:
-- existing data is test material and is wiped rather than migrated.

create table if not exists public.agents (
  id                uuid primary key default gen_random_uuid(),
  owner_id          uuid not null,
  name              text not null,
  instructions      text not null default '',
  model             text not null,
  grounding         text not null default 'strict',
  use_general_kb    boolean not null default false,
  powabase_agent_id text not null,
  kb_id             text,
  kb_full_id        text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists agents_owner_updated_idx
  on public.agents (owner_id, updated_at desc);
alter table public.agents enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

-- sessions is dropped and recreated: agent_id changes meaning from a Powabase
-- agent id to a foreign key into public.agents, kb_full_id is removed (scratch
-- is chunk-only), and user_slug is vestigial now that owner_id exists.
drop table if exists public.sessions;

create table public.sessions (
  id                  uuid primary key default gen_random_uuid(),
  owner_id            uuid not null,
  agent_id            uuid not null references public.agents (id),
  name                text not null,
  kb_id               text,               -- chat scratch KB, created lazily
  powabase_session_id text,               -- the conversation thread
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index if not exists sessions_owner_updated_idx
  on public.sessions (owner_id, updated_at desc);
create index if not exists sessions_agent_idx
  on public.sessions (agent_id);
alter table public.sessions enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
```

- [ ] **Step 2: Apply it to the live project**

Paste the file into the Powabase Studio SQL Editor and run it. There is no automated test for this step; Task 11 verifies it end to end.

- [ ] **Step 3: Commit**

```bash
git add migrations/004_user_owned_agents.sql
git commit -m "feat: migration for user-owned agents"
```

---

### Task 4: Prompt composition

**Files:**
- Create: `backend/app/services/prompts.py`
- Test: `backend/tests/unit/test_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `compose_system_prompt(instructions: str, grounding: str) -> str`, `STRICT_CLAUSE`, `OPEN_CLAUSE`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_prompts.py`:

```python
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE, compose_system_prompt


def test_strict_appends_the_strict_clause():
    prompt = compose_system_prompt("You are a chemistry tutor.", "strict")
    assert prompt.startswith("You are a chemistry tutor.")
    assert STRICT_CLAUSE in prompt


def test_open_appends_the_open_clause():
    prompt = compose_system_prompt("You are a chemistry tutor.", "open")
    assert OPEN_CLAUSE in prompt
    assert STRICT_CLAUSE not in prompt


def test_user_instructions_are_preserved_verbatim():
    # Whatever the user typed must survive intact — this is their agent's voice.
    instructions = "Speak like a pirate.\n\n  - Always show working\n"
    assert instructions.strip() in compose_system_prompt(instructions, "open")


def test_empty_instructions_yield_the_clause_alone():
    assert compose_system_prompt("", "strict") == STRICT_CLAUSE
    assert compose_system_prompt("   ", "strict") == STRICT_CLAUSE


def test_strict_clause_permits_normal_replies_to_small_talk():
    # Without this, the gate correctly skipping retrieval on "hi" would make a
    # strict agent answer "that isn't in my documents".
    assert "greetings" in STRICT_CLAUSE.lower()


def test_unknown_grounding_falls_back_to_strict():
    assert STRICT_CLAUSE in compose_system_prompt("x", "nonsense")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_prompts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.prompts'`

- [ ] **Step 3: Implement**

Create `backend/app/services/prompts.py`:

```python
from __future__ import annotations

STRICT_CLAUSE = (
    "When context from the knowledge base is provided with a question, base your "
    "answer only on that context and cite your sources. If the provided context "
    "does not contain the answer, say so plainly rather than guessing. Respond "
    "normally to greetings and small talk."
)

OPEN_CLAUSE = (
    "When context from the knowledge base is provided with a question, use it and "
    "cite your sources. When no context is provided, or it does not cover the "
    "question, answer normally and helpfully."
)


def compose_system_prompt(instructions: str, grounding: str) -> str:
    """The user's instructions plus a grounding clause.

    Unknown grounding values fall back to strict: the safer default for a RAG
    agent is to refuse rather than to invent.
    """
    clause = OPEN_CLAUSE if grounding == "open" else STRICT_CLAUSE
    base = (instructions or "").strip()
    return f"{base}\n\n{clause}" if base else clause
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_prompts.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/prompts.py tests/unit/test_prompts.py
git commit -m "feat: grounding-aware system prompt composition"
```

---

### Task 5: Retrieval scope

**Files:**
- Create: `backend/app/services/retrieval_scope.py`
- Test: `backend/tests/unit/test_retrieval_scope.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `kb_ids_for(agent_row, session_row, general_kb_id) -> list` — `session_row` and `general_kb_id` may be `None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_retrieval_scope.py`:

```python
from app.services.retrieval_scope import kb_ids_for

AGENT = {"kb_id": "ag-chunk", "kb_full_id": "ag-full", "use_general_kb": False}


def test_agent_permanent_kbs_are_always_in_scope():
    assert kb_ids_for(AGENT, None, "gen") == ["ag-chunk", "ag-full"]


def test_session_scratch_kb_is_added_when_present():
    assert kb_ids_for(AGENT, {"kb_id": "sc"}, "gen") == ["ag-chunk", "ag-full", "sc"]


def test_general_kb_only_when_opted_in():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, None, "gen") == ["ag-chunk", "ag-full", "gen"]


def test_general_kb_omitted_when_opted_in_but_unavailable():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, None, None) == ["ag-chunk", "ag-full"]


def test_untrained_agent_with_no_uploads_has_empty_scope():
    # Correct behavior, not a failure state: the agent answers from the model.
    bare = {"kb_id": None, "kb_full_id": None, "use_general_kb": False}
    assert kb_ids_for(bare, {"kb_id": None}, "gen") == []


def test_order_is_agent_then_scratch_then_general():
    opted_in = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(opted_in, {"kb_id": "sc"}, "gen") == [
        "ag-chunk", "ag-full", "sc", "gen",
    ]


def test_no_duplicates_when_ids_repeat():
    same = {"kb_id": "x", "kb_full_id": "x", "use_general_kb": True}
    assert kb_ids_for(same, {"kb_id": "x"}, "x") == ["x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.retrieval_scope'`

- [ ] **Step 3: Implement**

Create `backend/app/services/retrieval_scope.py`:

```python
from __future__ import annotations


def kb_ids_for(agent_row: dict, session_row, general_kb_id) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    Agent permanent KBs first (the curated tier), then this chat's scratch KB,
    then the shared general KB if the agent opted in. Falsy ids are dropped, so
    an untrained agent with no uploads yields [] — it answers from the model,
    which is correct rather than a failure.
    """
    ids = [agent_row.get("kb_id"), agent_row.get("kb_full_id")]
    if session_row:
        ids.append(session_row.get("kb_id"))
    if agent_row.get("use_general_kb"):
        ids.append(general_kb_id)

    out: list = []
    for kb_id in ids:
        if kb_id and kb_id not in out:
            out.append(kb_id)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/retrieval_scope.py tests/unit/test_retrieval_scope.py
git commit -m "feat: retrieval scope composition from agent + chat + general"
```

---

### Task 6: AgentService

**Files:**
- Create: `backend/app/services/agent_service.py`
- Test: `backend/tests/unit/test_agent_service.py`
- Modify: `backend/app/core/config.py`, `backend/app/main.py`

**Interfaces:**
- Consumes: `compose_system_prompt` (Task 4), client methods (Task 2).
- Produces:
  - `AgentService(client, reranker_config=None)` with `create(owner_id, name, instructions, model, grounding, use_general_kb) -> dict`, `list(owner_id) -> list`, `get_owned(agent_id, owner_id) -> dict | None`, `update(row, fields) -> dict`, `delete(agent_id) -> bool`, `ensure_kb(row, full_document=False) -> str`
  - `get_agent_service(request) -> AgentService`
  - `Settings.default_agent_model`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_agent_service.py`:

```python
import pytest

from app.clients.powabase_client import PowabaseAPIError
from app.services.agent_service import AgentService
from app.services.prompts import OPEN_CLAUSE, STRICT_CLAUSE


class FakeClient:
    def __init__(self):
        self.created_agents = []
        self.updated_agents = []
        self.rows = {}
        self.kbs = []
        self.deleted_agents = []
        self.deleted_kbs = []
        self.sessions_for_agent = []
        self.deleted_sessions = []
        self._n = 0

    # Powabase agent API
    def create_agent(self, name, model, system_prompt, settings=None):
        self.created_agents.append((name, model, system_prompt))
        return {"id": f"pa-{len(self.created_agents)}"}

    def update_agent(self, agent_id, fields):
        self.updated_agents.append((agent_id, fields))
        return {"id": agent_id}

    def delete_agent(self, agent_id):
        self.deleted_agents.append(agent_id)

    def create_knowledge_base(self, name, description=None, indexing_config=None, retrieval_config=None):
        self._n += 1
        self.kbs.append((name, indexing_config, retrieval_config))
        return {"id": f"kb-{self._n}"}

    def delete_knowledge_base(self, kb_id):
        self.deleted_kbs.append(kb_id)

    # Rows
    def insert_agent_row(self, row):
        row = dict(row, id="ag-1")
        self.rows[row["id"]] = row
        return row

    def list_agent_rows(self, owner_id):
        return [r for r in self.rows.values() if r.get("owner_id") == owner_id]

    def get_agent_row(self, agent_id):
        return self.rows.get(agent_id)

    def update_agent_row(self, agent_id, fields):
        self.rows[agent_id].update(fields)

    def delete_agent_row(self, agent_id):
        self.rows.pop(agent_id, None)

    def list_sessions_for_agent(self, agent_id):
        return self.sessions_for_agent

    def delete_session_row(self, session_id):
        self.deleted_sessions.append(session_id)


def test_create_makes_a_powabase_agent_with_the_composed_prompt():
    c = FakeClient()
    row = AgentService(c).create("o1", "Tutor", "Be terse.", "gpt-4o-mini", "strict", False)

    name, model, prompt = c.created_agents[0]
    assert model == "gpt-4o-mini"
    assert "Be terse." in prompt and STRICT_CLAUSE in prompt
    assert row["powabase_agent_id"] == "pa-1"
    assert row["owner_id"] == "o1"


def test_create_does_not_provision_knowledge_bases_upfront():
    # KBs are lazy: an agent nobody has trained costs no KB.
    c = FakeClient()
    row = AgentService(c).create("o1", "T", "", "m", "strict", False)
    assert c.kbs == []
    assert row["kb_id"] is None and row["kb_full_id"] is None


def test_ensure_kb_creates_chunk_kb_once_and_persists_it():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    first = svc.ensure_kb(row, full_document=False)
    second = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=False)

    assert first == second
    assert len(c.kbs) == 1
    assert c.rows["ag-1"]["kb_id"] == first


def test_ensure_kb_creates_a_separate_full_document_kb():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    chunk = svc.ensure_kb(row, full_document=False)
    full = svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)

    assert chunk != full
    assert c.kbs[1][1] == {"strategy": "full_document"}


def test_ensure_kb_passes_the_reranker_config():
    c = FakeClient()
    svc = AgentService(c, reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row)
    assert c.kbs[0][2] == {"reranker": {"model": "m", "candidate_count": 20}}


def test_get_owned_returns_none_for_another_users_agent():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("o1", "T", "", "m", "strict", False)
    assert svc.get_owned("ag-1", "someone-else") is None
    assert svc.get_owned("ag-1", "o1") is not None


def test_update_patches_the_remote_agent_when_prompt_inputs_change():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "Old.", "m", "strict", False)

    svc.update(row, {"instructions": "New.", "grounding": "open"})

    agent_id, fields = c.updated_agents[0]
    assert agent_id == "pa-1"
    assert "New." in fields["system_prompt"] and OPEN_CLAUSE in fields["system_prompt"]


def test_update_patches_the_remote_agent_when_model_changes():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m1", "strict", False)

    svc.update(row, {"model": "m2"})

    assert c.updated_agents[0][1]["model"] == "m2"


def test_update_skips_the_remote_call_for_local_only_fields():
    # Renaming or toggling general knowledge changes nothing Powabase knows about.
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)

    svc.update(row, {"name": "Renamed", "use_general_kb": True})

    assert c.updated_agents == []
    assert c.rows["ag-1"]["name"] == "Renamed"
    assert c.rows["ag-1"]["use_general_kb"] is True


def test_delete_cascades_to_kbs_chats_and_the_remote_agent():
    c = FakeClient()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row, full_document=False)
    svc.ensure_kb(c.get_agent_row("ag-1"), full_document=True)
    c.sessions_for_agent = [{"id": "s-1"}, {"id": "s-2"}]

    assert svc.delete("ag-1") is True

    assert set(c.deleted_kbs) == {"kb-1", "kb-2"}
    assert c.deleted_agents == ["pa-1"]
    assert c.deleted_sessions == ["s-1", "s-2"]
    assert c.get_agent_row("ag-1") is None


def test_delete_returns_false_for_unknown_agent():
    assert AgentService(FakeClient()).delete("nope") is False


def test_delete_survives_a_failing_remote_cleanup():
    # The row delete is authoritative; a stale remote resource must not block it.
    class Failing(FakeClient):
        def delete_knowledge_base(self, kb_id):
            raise PowabaseAPIError("gone", status_code=404)

    c = Failing()
    svc = AgentService(c)
    row = svc.create("o1", "T", "", "m", "strict", False)
    svc.ensure_kb(row)

    assert svc.delete("ag-1") is True
    assert c.get_agent_row("ag-1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_agent_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agent_service'`

- [ ] **Step 3: Check the PowabaseAPIError constructor**

Run: `grep -n "class PowabaseAPIError" -A 6 app/clients/powabase_client.py`
If its signature is not `(message, status_code=None)`, adjust the `Failing` fake in the test to match. Do not change the exception.

- [ ] **Step 4: Implement**

Create `backend/app/services/agent_service.py`:

```python
from __future__ import annotations

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError
from app.services.prompts import compose_system_prompt

# Changing any of these requires patching the remote agent; everything else on
# an agent row is local-only.
REMOTE_FIELDS = ("instructions", "grounding", "model")


class AgentService:
    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def create(
        self,
        owner_id: str,
        name: str,
        instructions: str,
        model: str,
        grounding: str,
        use_general_kb: bool,
    ) -> dict:
        prompt = compose_system_prompt(instructions, grounding)
        agent = self.client.create_agent(f"user-agent-{name}", model=model, system_prompt=prompt)
        return self.client.insert_agent_row({
            "owner_id": owner_id,
            "name": name,
            "instructions": instructions,
            "model": model,
            "grounding": grounding,
            "use_general_kb": use_general_kb,
            "powabase_agent_id": agent["id"],
            "kb_id": None,
            "kb_full_id": None,
        })

    def list(self, owner_id: str) -> list:
        return self.client.list_agent_rows(owner_id)

    def get_owned(self, agent_id: str, owner_id: str):
        row = self.client.get_agent_row(agent_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row

    def update(self, row: dict, fields: dict) -> dict:
        """Apply an edit. Patches the remote agent only when the fields that
        feed its model or system prompt actually changed."""
        merged = dict(row, **fields)
        if any(field in fields for field in REMOTE_FIELDS):
            self.client.update_agent(row["powabase_agent_id"], {
                "model": merged["model"],
                "system_prompt": compose_system_prompt(
                    merged["instructions"], merged["grounding"]
                ),
            })
        self.client.update_agent_row(row["id"], fields)
        return merged

    def ensure_kb(self, row: dict, full_document: bool = False) -> str:
        """Return the agent's permanent KB id for this document class, creating
        it lazily. An agent nobody has trained costs no knowledge base."""
        column = "kb_full_id" if full_document else "kb_id"
        existing = row.get(column)
        if existing:
            return existing
        agent_id = row["id"]
        if full_document:
            name = f"agent-{agent_id}-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"agent-{agent_id}-kb"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Permanent knowledge for agent {agent_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
        self.client.update_agent_row(agent_id, {column: kb["id"]})
        return kb["id"]

    def delete(self, agent_id: str) -> bool:
        """Delete an agent and everything it owns: its chats, its permanent KBs
        and its Powabase agent. Remote cleanup is best-effort so a stale resource
        never blocks the authoritative row delete."""
        row = self.client.get_agent_row(agent_id)
        if row is None:
            return False

        for session in self.client.list_sessions_for_agent(agent_id):
            try:
                self.client.delete_session_row(session["id"])
            except PowabaseAPIError:
                pass

        for resource_id, delete_fn in (
            (row.get("kb_id"), self.client.delete_knowledge_base),
            (row.get("kb_full_id"), self.client.delete_knowledge_base),
            (row.get("powabase_agent_id"), self.client.delete_agent),
        ):
            if resource_id:
                try:
                    delete_fn(resource_id)
                except PowabaseAPIError:
                    pass

        self.client.delete_agent_row(agent_id)
        return True


def get_agent_service(request: Request) -> "AgentService":
    """FastAPI dependency returning the shared AgentService created at startup."""
    return request.app.state.agent_service
```

- [ ] **Step 5: Add the default model setting**

In `app/core/config.py`, add below `router_agent_model`:

```python
    default_agent_model: str = "gpt-4o-mini"
```

- [ ] **Step 6: Wire AgentService into the lifespan**

In `app/main.py`, add the import and register the service after `session_service`:

```python
from app.services.agent_service import AgentService
```

```python
        app.state.agent_service = AgentService(client, reranker_config)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_agent_service.py -q`
Expected: PASS (12 tests)

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/services/agent_service.py app/core/config.py app/main.py tests/unit/test_agent_service.py
git commit -m "feat: AgentService — CRUD, lazy permanent KBs, cascade delete"
```

---

### Task 7: Agent routes — CRUD

**Files:**
- Modify: `backend/app/models/schemas.py`
- Create: `backend/app/api/routes/agents.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_routes_agents.py`

**Interfaces:**
- Consumes: `AgentService` (Task 6).
- Produces: `POST/GET/PATCH/DELETE /agents`; schemas `AgentCreateRequest`, `AgentUpdateRequest`, `AgentResponse`, `AgentSummary`.

- [ ] **Step 1: Add the schemas**

In `app/models/schemas.py`:

```python
class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=8000)
    model: Optional[str] = Field(default=None)
    grounding: str = Field(default="strict")
    use_general_kb: bool = Field(default=False)

    @field_validator("grounding")
    @classmethod
    def _known_grounding(cls, v: str) -> str:
        if v not in ("strict", "open"):
            raise ValueError("grounding must be 'strict' or 'open'")
        return v


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    instructions: Optional[str] = Field(default=None, max_length=8000)
    model: Optional[str] = None
    grounding: Optional[str] = None
    use_general_kb: Optional[bool] = None

    @field_validator("grounding")
    @classmethod
    def _known_grounding(cls, v):
        if v is not None and v not in ("strict", "open"):
            raise ValueError("grounding must be 'strict' or 'open'")
        return v


class AgentResponse(BaseModel):
    id: str
    name: str
    instructions: str
    model: str
    grounding: str
    use_general_kb: bool
    trained: bool


class AgentSummary(BaseModel):
    id: str
    name: str
    model: str
    trained: bool
    updated_at: Optional[str] = None
```

`trained` is derived, not stored: `bool(row.get("kb_id") or row.get("kb_full_id"))`. It tells the UI whether to show "not trained yet" without a second round trip.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/unit/test_routes_agents.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import agents as agents_route
from app.core.config import get_settings
from app.services.agent_service import get_agent_service
from types import SimpleNamespace


class FakeAgentService:
    def __init__(self):
        self.rows = {}
        self.deleted = []
        self._n = 0

    def create(self, owner_id, name, instructions, model, grounding, use_general_kb):
        self._n += 1
        row = {
            "id": f"ag-{self._n}", "owner_id": owner_id, "name": name,
            "instructions": instructions, "model": model, "grounding": grounding,
            "use_general_kb": use_general_kb, "powabase_agent_id": "pa-1",
            "kb_id": None, "kb_full_id": None, "updated_at": "2026-08-06T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def list(self, owner_id):
        return [r for r in self.rows.values() if r["owner_id"] == owner_id]

    def get_owned(self, agent_id, owner_id):
        row = self.rows.get(agent_id)
        return row if row and row["owner_id"] == owner_id else None

    def update(self, row, fields):
        row.update(fields)
        return row

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        return self.rows.pop(agent_id, None) is not None


def build_app(service=None):
    app = FastAPI()
    app.include_router(agents_route.router)
    svc = service or FakeAgentService()
    app.dependency_overrides[get_agent_service] = lambda: svc
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        default_agent_model="gpt-4o-mini"
    )
    app.state.svc = svc
    return app


def test_create_agent_returns_the_configured_agent():
    app = build_app()
    r = TestClient(app).post("/agents", json={
        "name": "Tutor", "instructions": "Be terse.",
        "grounding": "open", "use_general_kb": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Tutor"
    assert body["grounding"] == "open"
    assert body["use_general_kb"] is True
    assert body["trained"] is False


def test_create_agent_falls_back_to_the_default_model():
    app = build_app()
    r = TestClient(app).post("/agents", json={"name": "T"})
    assert r.json()["model"] == "gpt-4o-mini"


def test_create_agent_rejects_unknown_grounding():
    app = build_app()
    r = TestClient(app).post("/agents", json={"name": "T", "grounding": "sideways"})
    assert r.status_code == 422


def test_list_agents_returns_only_mine():
    svc = FakeAgentService()
    svc.create("o1", "Mine", "", "m", "strict", False)
    svc.create("other", "Theirs", "", "m", "strict", False)
    app = build_app(svc)

    body = TestClient(app).get("/agents").json()

    assert [a["name"] for a in body] == ["Mine"]


def test_trained_flag_is_true_once_a_kb_exists():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "m", "strict", False)
    row["kb_id"] = "kb-1"
    app = build_app(svc)

    assert TestClient(app).get("/agents").json()[0]["trained"] is True


def test_patch_updates_fields():
    svc = FakeAgentService()
    svc.create("o1", "Old", "", "m", "strict", False)
    app = build_app(svc)

    r = TestClient(app).patch("/agents/ag-1", json={"name": "New", "grounding": "open"})

    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert r.json()["grounding"] == "open"


def test_patch_ignores_unset_fields():
    svc = FakeAgentService()
    svc.create("o1", "Keep", "Keep me.", "m", "strict", False)
    app = build_app(svc)

    TestClient(app).patch("/agents/ag-1", json={"name": "Renamed"})

    assert svc.rows["ag-1"]["instructions"] == "Keep me."


def test_patch_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "Theirs", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).patch("/agents/ag-1", json={"name": "x"}).status_code == 404


def test_delete_agent_204_and_cascades():
    svc = FakeAgentService()
    svc.create("o1", "T", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).delete("/agents/ag-1").status_code == 204
    assert svc.deleted == ["ag-1"]


def test_delete_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "m", "strict", False)
    app = build_app(svc)

    assert TestClient(app).delete("/agents/ag-1").status_code == 404
    assert svc.deleted == []


def test_get_agent_404_for_unknown_id():
    app = build_app()
    assert TestClient(app).get("/agents/nope").status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_agents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.routes.agents'`

- [ ] **Step 4: Implement the routes**

Create `backend/app/api/routes/agents.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError
from app.core.config import get_settings
from app.models.schemas import (
    AgentCreateRequest,
    AgentResponse,
    AgentSummary,
    AgentUpdateRequest,
)
from app.services.agent_service import AgentService, get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


def _trained(row: dict) -> bool:
    return bool(row.get("kb_id") or row.get("kb_full_id"))


def _to_response(row: dict) -> AgentResponse:
    return AgentResponse(
        id=row["id"], name=row["name"], instructions=row.get("instructions", ""),
        model=row["model"], grounding=row.get("grounding", "strict"),
        use_general_kb=bool(row.get("use_general_kb")), trained=_trained(row),
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    settings=Depends(get_settings),
):
    try:
        row = await run_in_threadpool(
            agents.create, user["id"], req.name, req.instructions,
            req.model or settings.default_agent_model, req.grounding, req.use_general_kb,
        )
    except PowabaseAPIError as e:
        # A bad model id is rejected here by the provider, not by a local list.
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(row)


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    rows = await run_in_threadpool(agents.list, user["id"])
    return [
        AgentSummary(id=r["id"], name=r["name"], model=r["model"],
                     trained=_trained(r), updated_at=r.get("updated_at"))
        for r in rows
    ]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _to_response(row)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    req: AgentUpdateRequest,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    fields = req.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        return _to_response(row)
    try:
        merged = await run_in_threadpool(agents.update, row, fields)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(merged)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        await run_in_threadpool(agents.delete, agent_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return None
```

- [ ] **Step 5: Register the router**

In `app/main.py`, add the import and `app.include_router(agents_router)` **before** the `StaticFiles` mount (the mount at `/` swallows anything registered after it):

```python
from app.api.routes.agents import router as agents_router
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_agents.py -q`
Expected: PASS (11 tests)

- [ ] **Step 7: Commit**

```bash
git add app/api/routes/agents.py app/models/schemas.py app/main.py tests/unit/test_routes_agents.py
git commit -m "feat: /agents CRUD routes"
```

---

### Task 8: Training routes

**Files:**
- Modify: `backend/app/api/routes/agents.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_routes_agents.py`

**Interfaces:**
- Consumes: `AgentService.ensure_kb` (Task 6), `IngestService` (existing), `remove_source_from_kb` (Task 2).
- Produces: `POST /agents/{id}/train`, `GET /agents/{id}/documents`, `DELETE /agents/{id}/documents/{source_id}`; schema `AgentDocument`.

- [ ] **Step 1: Add the schema**

In `app/models/schemas.py`:

```python
class AgentDocument(BaseModel):
    source_id: str
    filename: Optional[str] = None
    status: Optional[str] = None
```

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/unit/test_routes_agents.py`:

```python
class FakeIngestClient:
    def __init__(self):
        self.kb_sources = {}
        self.removed = []

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_sources.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, source_id):
        self.removed.append((kb_id, source_id))


def build_train_app(svc=None, client=None):
    from app.clients.powabase_client import get_powabase_client
    app = build_app(svc)
    app.dependency_overrides[get_powabase_client] = lambda: (client or FakeIngestClient())
    return app


def test_train_404_for_another_users_agent():
    svc = FakeAgentService()
    svc.create("someone-else", "T", "", "m", "strict", False)
    app = build_train_app(svc)

    r = TestClient(app).post(
        "/agents/ag-1/train", files={"file": ("d.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert r.status_code == 404


def test_documents_lists_both_permanent_kbs():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "m", "strict", False)
    row["kb_id"], row["kb_full_id"] = "kb-c", "kb-f"
    client = FakeIngestClient()
    client.kb_sources = {
        "kb-c": [{"source_id": "s1", "filename": "big.pdf", "status": "indexed"}],
        "kb-f": [{"source_id": "s2", "filename": "small.pdf", "status": "indexed"}],
    }
    app = build_train_app(svc, client)

    body = TestClient(app).get("/agents/ag-1/documents").json()

    assert {d["source_id"] for d in body} == {"s1", "s2"}


def test_documents_empty_for_untrained_agent():
    svc = FakeAgentService()
    svc.create("o1", "T", "", "m", "strict", False)
    app = build_train_app(svc)

    assert TestClient(app).get("/agents/ag-1/documents").json() == []


def test_untrain_unlinks_from_the_kb_that_holds_it():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "m", "strict", False)
    row["kb_id"], row["kb_full_id"] = "kb-c", "kb-f"
    client = FakeIngestClient()
    client.kb_sources = {"kb-f": [{"source_id": "s2"}]}
    app = build_train_app(svc, client)

    r = TestClient(app).delete("/agents/ag-1/documents/s2")

    assert r.status_code == 204
    assert client.removed == [("kb-f", "s2")]


def test_untrain_404_when_the_agent_does_not_hold_that_document():
    svc = FakeAgentService()
    row = svc.create("o1", "T", "", "m", "strict", False)
    row["kb_id"] = "kb-c"
    app = build_train_app(svc, FakeIngestClient())

    assert TestClient(app).delete("/agents/ag-1/documents/nope").status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_agents.py -q`
Expected: FAIL — 404/405 on the new paths

- [ ] **Step 4: Implement**

Append to `app/api/routes/agents.py` (add the imports at the top of the file):

```python
from fastapi import File, UploadFile
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.models.schemas import AgentDocument, IngestResponse
from app.services.ingest_service import (
    AttentionRequiredError,
    ExtractionFailedError,
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)
```

```python
def _permanent_kb_ids(row: dict) -> list:
    return [kb for kb in (row.get("kb_id"), row.get("kb_full_id")) if kb]


@router.post("/{agent_id}/train", response_model=IngestResponse)
async def train_agent(
    agent_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    content = await file.read()
    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_max_wait_seconds,
    )

    def _run() -> dict:
        source_id = service.start(file.filename, content)
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= settings.full_document_max_chars
        kb_id = agents.ensure_kb(row, full_document)
        status = service.index_into(kb_id, source_id)
        return {"source_id": source_id, "status": status}

    try:
        result = await run_in_threadpool(_run)
    except AttentionRequiredError as e:
        raise HTTPException(status_code=422, detail=f"Could not read {file.filename}; it may need OCR.")
    except (ExtractionFailedError, IndexingFailedError) as e:
        raise HTTPException(status_code=422, detail=e.message)
    except IngestTimeoutError as e:
        raise HTTPException(status_code=504, detail=f"Still {e.status} after the maximum wait.")
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestResponse(**result)


@router.get("/{agent_id}/documents", response_model=list[AgentDocument])
async def list_agent_documents(
    agent_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    def _collect() -> list:
        out = []
        for kb_id in _permanent_kb_ids(row):
            for item in client.list_kb_sources(kb_id).get("items", []):
                out.append(AgentDocument(
                    source_id=item.get("source_id"),
                    filename=item.get("filename"),
                    status=item.get("status"),
                ))
        return out

    try:
        return await run_in_threadpool(_collect)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/{agent_id}/documents/{source_id}", status_code=204)
async def untrain_agent_document(
    agent_id: str,
    source_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    row = await run_in_threadpool(agents.get_owned, agent_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    def _unlink() -> bool:
        # Unlink from whichever permanent KB holds it. Never delete the Source:
        # upload_source reuses duplicates, so it may belong to other agents too.
        for kb_id in _permanent_kb_ids(row):
            items = client.list_kb_sources(kb_id).get("items", [])
            if any(item.get("source_id") == source_id for item in items):
                client.remove_source_from_kb(kb_id, source_id)
                return True
        return False

    try:
        found = await run_in_threadpool(_unlink)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found on this agent")
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_agents.py -q`
Expected: PASS (16 tests)

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/agents.py app/models/schemas.py tests/unit/test_routes_agents.py
git commit -m "feat: agent training — upload, list documents, untrain"
```

---

### Task 9: Bind sessions to agents

**Files:**
- Modify: `backend/app/services/session_service.py`, `backend/app/api/routes/sessions.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_session_service.py`, `backend/tests/unit/test_routes_sessions.py`

**Interfaces:**
- Consumes: `AgentService.get_owned` (Task 6).
- Produces: `SessionService.create_session(owner_id, agent_id, name=None) -> dict`; `SessionService.ensure_kb(row) -> str` (no `full_document` parameter); `SessionCreateRequest.agent_id`.

- [ ] **Step 1: Update the schema**

In `app/models/schemas.py`, `SessionCreateRequest` becomes:

```python
class SessionCreateRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    name: Optional[str] = None
```

- [ ] **Step 2: Write the failing tests**

In `backend/tests/unit/test_session_service.py`, replace tests that assert agent creation with:

```python
def test_create_session_binds_to_the_agent_and_creates_no_agent():
    # The agent is durable and user-owned now; a chat must never mint one.
    c = FakeClient()
    row = SessionService(c).create_session("o1", "ag-1", "My chat")

    assert row["agent_id"] == "ag-1"
    assert row["owner_id"] == "o1"
    assert c.created_agents == []


def test_ensure_kb_creates_one_chunk_scratch_kb():
    c = FakeClient()
    svc = SessionService(c)
    row = svc.create_session("o1", "ag-1", None)

    kb_id = svc.ensure_kb(row)

    assert c.kbs[0][1] is None  # chunk_embed: no indexing_config
    assert c.rows[row["id"]]["kb_id"] == kb_id


def test_ensure_kb_is_idempotent():
    c = FakeClient()
    svc = SessionService(c)
    row = svc.create_session("o1", "ag-1", None)
    first = svc.ensure_kb(row)
    assert svc.ensure_kb(c.get_session_row(row["id"])) == first
    assert len(c.kbs) == 1
```

In `backend/tests/unit/test_routes_sessions.py`, add:

```python
def test_create_session_requires_an_agent_id():
    app = build_app()
    assert TestClient(app).post("/sessions", json={}).status_code == 422


def test_create_session_404_for_another_users_agent():
    app = build_app(owned_agent=None)
    r = TestClient(app).post("/sessions", json={"agent_id": "ag-x"})
    assert r.status_code == 404
```

Adjust the existing `build_app` in that file to override `get_agent_service` with a fake whose `get_owned` returns a row (or `None` when `owned_agent=None`), and to pass `agent_id` in every existing create call.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_session_service.py tests/unit/test_routes_sessions.py -q`
Expected: FAIL

- [ ] **Step 4: Update SessionService**

In `app/services/session_service.py`:

- Delete `SYSTEM_PROMPT`, `slugify`, and the `model`/`general_kb_id` constructor parameters. The constructor becomes `__init__(self, client, reranker_config: dict | None = None)`.
- `create_session` becomes:

```python
    def create_session(self, owner_id: str, agent_id: str, name: str | None = None) -> dict:
        """Create a chat bound to an existing agent. Creates no Powabase agent:
        the agent is durable and user-owned, and one agent serves many chats."""
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "agent_id": agent_id,
            "name": name or DEFAULT_NAME,
        })
```

- `ensure_kb` loses its `full_document` parameter and always creates a chunk KB:

```python
    def ensure_kb(self, row: dict) -> str:
        """Return this chat's scratch KB id, creating it lazily on first upload.

        Chunk-embed only: scratch uploads are throwaway context for one
        conversation, so the chunk/full split is reserved for the agent's
        permanent tier.
        """
        existing = row.get("kb_id")
        if existing:
            return existing
        session_id = row["id"]
        kb = self.client.create_knowledge_base(
            f"chat-{session_id}-kb",
            description=f"Scratch documents for chat {session_id}",
            retrieval_config=self.reranker_config,
        )
        self.client.update_session(session_id, {"kb_id": kb["id"]})
        return kb["id"]
```

- In `delete`, drop the `kb_full_id` and `agent_id` entries from the cleanup tuple — the agent is no longer owned by the session and must survive:

```python
        for resource_id, delete_fn in (
            (row.get("kb_id"), self.client.delete_knowledge_base),
        ):
```

- [ ] **Step 5: Update the sessions route**

In `app/api/routes/sessions.py`, `create_session` validates agent ownership first:

```python
@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: SessionCreateRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
):
    agent = await run_in_threadpool(agents.get_owned, req.agent_id, user["id"])
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        row = await run_in_threadpool(
            sessions.create_session, user["id"], req.agent_id, req.name
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SessionResponse(id=row["id"], name=row["name"])
```

Add the imports for `AgentService` and `get_agent_service`.

- [ ] **Step 6: Update the lifespan**

In `app/main.py`, `SessionService` now takes only the client and reranker config:

```python
        app.state.session_service = SessionService(client, reranker_config)
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Fix any test still passing the old `create_session` signature.

- [ ] **Step 8: Commit**

```bash
git add app/services/session_service.py app/api/routes/sessions.py app/main.py app/models/schemas.py tests/
git commit -m "feat: chats bind to a user-owned agent instead of minting one"
```

---

### Task 10: Rewire chat and ingest

**Files:**
- Modify: `backend/app/api/routes/chat.py`, `backend/app/api/routes/ingest.py`
- Test: `backend/tests/unit/test_routes_chat.py`, `backend/tests/unit/test_routes_ingest.py`

**Interfaces:**
- Consumes: `kb_ids_for` (Task 5), `AgentService.get_owned` (Task 6), reshaped `SessionService` (Task 9).
- Produces: chat answering through the agent's Powabase agent with the composed KB scope.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_routes_chat.py`:

```python
def test_chat_uses_the_agents_powabase_agent_and_full_kb_scope():
    # The chat must run on the agent the chat is bound to, retrieving from the
    # agent's permanent KBs plus this chat's scratch KB.
    app, client = build_app_with(
        agent_row={
            "id": "ag-1", "owner_id": "o1", "powabase_agent_id": "pa-9",
            "kb_id": "ag-chunk", "kb_full_id": "ag-full",
            "use_general_kb": False, "model": "m",
        },
        session_row={"id": "s1", "owner_id": "o1", "agent_id": "ag-1", "kb_id": "sc"},
    )

    TestClient(app).post("/chat", json={"session_id": "s1", "query": "q"})

    assert client.ran_agent_id == "pa-9"
    assert [kb["id"] for kb in client.knowledge_bases] == ["ag-chunk", "ag-full", "sc"]


def test_chat_includes_general_kb_only_when_the_agent_opted_in():
    app, client = build_app_with(
        agent_row={
            "id": "ag-1", "owner_id": "o1", "powabase_agent_id": "pa-9",
            "kb_id": "ag-chunk", "kb_full_id": None,
            "use_general_kb": True, "model": "m",
        },
        session_row={"id": "s1", "owner_id": "o1", "agent_id": "ag-1", "kb_id": None},
    )

    TestClient(app).post("/chat", json={"session_id": "s1", "query": "q"})

    assert [kb["id"] for kb in client.knowledge_bases] == ["ag-chunk", "gkb-1"]
```

Write `build_app_with(agent_row, session_row)` in that file following the existing fixture style: override `get_session_service`, `get_agent_service`, `get_powabase_client`, `get_general_kb_id` (returning `"gkb-1"`), `get_router_agent_id`, `get_current_user`, and `get_settings`. The fake client records `ran_agent_id` in `run_agent` and `knowledge_bases` in `create_context_handler`, and its gate returns `needs_kb=True`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_routes_chat.py -q`
Expected: FAIL

- [ ] **Step 3: Update the chat route**

In `app/api/routes/chat.py`, after resolving the session row, load its agent and compose the scope:

```python
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    agent_row = agents.get_owned(row["agent_id"], user["id"])
    if agent_row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
```

Replace the `ChatService` construction with:

```python
    gate = GateService(client, router_agent_id)
    service = ChatService(
        client, agent_row["powabase_agent_id"], gate,
        kb_ids_for(agent_row, row, general_kb_id),
        settings.retrieval_top_k, settings.retrieval_max_context_tokens,
    )
```

Add `agents: AgentService = Depends(get_agent_service)` to the signature and import `kb_ids_for`, `AgentService`, `get_agent_service`.

- [ ] **Step 4: Update the ingest route**

In `app/api/routes/ingest.py`, the chat upload path targets the scratch KB with no content-aware branch. Replace the routing block:

```python
        kb_id = sessions.ensure_kb(row)
        service.index_into(kb_id, source_id)
```

Delete the `full_document = 0 < service.char_count(source_id) <= max_chars` line and the `max_chars` argument threaded into `_run_finish`. Content-aware routing now lives only on the agent training path (Task 8).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/chat.py app/api/routes/ingest.py tests/
git commit -m "feat: chat retrieves across agent permanent KBs plus chat scratch"
```

---

### Task 11: Frontend — agents UI

**Files:**
- Create: `frontend/agents.js`
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: the `/agents` routes (Tasks 7-8), `POST /sessions` with `agent_id` (Task 9).
- Produces: a two-level sidebar and an agent manage view.

- [ ] **Step 1: Add the markup**

In `frontend/index.html`, above the session list in the sidebar:

```html
<div class="agent-bar">
  <select id="agent-select" aria-label="Agent"></select>
  <button type="button" id="new-agent" title="New agent">+</button>
  <button type="button" id="manage-agent" title="Manage this agent">⚙</button>
</div>
```

And before the closing `</body>`, an agent dialog plus the new script tag:

```html
<div id="agent-modal" class="modal" hidden>
  <form id="agent-form" class="modal-card">
    <h2 id="agent-modal-title">New agent</h2>
    <label>Name <input id="agent-name" required maxlength="80"></label>
    <label>Instructions
      <textarea id="agent-instructions" rows="5"
        placeholder="You are a study tutor for AP Chemistry. Always show your working."></textarea>
    </label>
    <label>Model <input id="agent-model" placeholder="gpt-4o-mini"></label>
    <label>Grounding
      <select id="agent-grounding">
        <option value="strict">Only answer from my documents</option>
        <option value="open">Use my documents, but answer freely</option>
      </select>
    </label>
    <label class="checkbox">
      <input type="checkbox" id="agent-general-kb"> Also use shared general knowledge
    </label>
    <div id="agent-docs" hidden>
      <h3>Trained on</h3>
      <ul id="agent-doc-list"></ul>
      <label class="train-row">Add a document
        <input type="file" id="agent-train-file" accept="application/pdf">
      </label>
      <p id="agent-train-status" class="train-status"></p>
    </div>
    <p id="agent-error" class="auth-error"></p>
    <div class="modal-actions">
      <button type="button" id="agent-delete" class="danger" hidden>Delete agent</button>
      <button type="button" id="agent-cancel">Cancel</button>
      <button type="submit" id="agent-save">Save</button>
    </div>
  </form>
</div>
<script src="agents.js"></script>
```

`agents.js` must load **before** `app.js` if `app.js` calls into it at init; place the tag accordingly and verify in Step 6.

- [ ] **Step 2: Implement `frontend/agents.js`**

```javascript
// Agent management: list, create, edit, train, delete. Kept out of app.js,
// which already carries auth, sessions, chat, uploads and rendering.

let agents = [];
let currentAgentId = null;
let editingAgentId = null;

const agentSelect = document.getElementById("agent-select");
const agentModal = document.getElementById("agent-modal");
const agentForm = document.getElementById("agent-form");
const agentNameInput = document.getElementById("agent-name");
const agentInstructionsInput = document.getElementById("agent-instructions");
const agentModelInput = document.getElementById("agent-model");
const agentGroundingInput = document.getElementById("agent-grounding");
const agentGeneralKbInput = document.getElementById("agent-general-kb");
const agentDocsSection = document.getElementById("agent-docs");
const agentDocList = document.getElementById("agent-doc-list");
const agentTrainFile = document.getElementById("agent-train-file");
const agentTrainStatus = document.getElementById("agent-train-status");
const agentError = document.getElementById("agent-error");
const agentDeleteButton = document.getElementById("agent-delete");
const agentModalTitle = document.getElementById("agent-modal-title");

function wireAgents() {
  document.getElementById("new-agent").addEventListener("click", () => openAgentModal(null));
  document.getElementById("manage-agent").addEventListener("click", () => {
    if (currentAgentId) openAgentModal(currentAgentId);
  });
  document.getElementById("agent-cancel").addEventListener("click", closeAgentModal);
  agentForm.addEventListener("submit", saveAgent);
  agentDeleteButton.addEventListener("click", deleteAgent);
  agentTrainFile.addEventListener("change", trainAgent);
  agentSelect.addEventListener("change", () => {
    currentAgentId = agentSelect.value;
    onAgentChanged();
  });
}

async function loadAgents() {
  const res = await authFetch("/agents");
  if (!res.ok) return;
  agents = await res.json();
  renderAgentSelect();
  if (!currentAgentId && agents.length > 0) currentAgentId = agents[0].id;
  agentSelect.value = currentAgentId || "";
  onAgentChanged();
}

function renderAgentSelect() {
  agentSelect.innerHTML = "";
  if (agents.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No agents yet — click +";
    agentSelect.appendChild(opt);
    return;
  }
  agents.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.trained ? a.name : `${a.name} (untrained)`;
    agentSelect.appendChild(opt);
  });
}

function openAgentModal(agentId) {
  editingAgentId = agentId;
  agentError.textContent = "";
  agentTrainStatus.textContent = "";
  agentModalTitle.textContent = agentId ? "Manage agent" : "New agent";
  agentDeleteButton.hidden = !agentId;
  agentDocsSection.hidden = !agentId;
  if (agentId) {
    loadAgentDetail(agentId);
    loadAgentDocuments(agentId);
  } else {
    agentNameInput.value = "";
    agentInstructionsInput.value = "";
    agentModelInput.value = "";
    agentGroundingInput.value = "strict";
    agentGeneralKbInput.checked = false;
  }
  agentModal.hidden = false;
  agentNameInput.focus();
}

function closeAgentModal() {
  agentModal.hidden = true;
  editingAgentId = null;
}

async function loadAgentDetail(agentId) {
  const res = await authFetch(`/agents/${encodeURIComponent(agentId)}`);
  if (!res.ok) return;
  const a = await res.json();
  agentNameInput.value = a.name;
  agentInstructionsInput.value = a.instructions || "";
  agentModelInput.value = a.model || "";
  agentGroundingInput.value = a.grounding;
  agentGeneralKbInput.checked = a.use_general_kb;
}

async function saveAgent(event) {
  event.preventDefault();
  agentError.textContent = "";
  const payload = {
    name: agentNameInput.value.trim(),
    instructions: agentInstructionsInput.value,
    grounding: agentGroundingInput.value,
    use_general_kb: agentGeneralKbInput.checked,
  };
  const model = agentModelInput.value.trim();
  if (model) payload.model = model;

  const url = editingAgentId ? `/agents/${encodeURIComponent(editingAgentId)}` : "/agents";
  const method = editingAgentId ? "PATCH" : "POST";
  try {
    const res = await authFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) {
      agentError.textContent = errorText(body, res);
      return;
    }
    if (!editingAgentId) currentAgentId = body.id;
    closeAgentModal();
    await loadAgents();
  } catch (err) {
    agentError.textContent = err.message;
  }
}

async function deleteAgent() {
  if (!editingAgentId) return;
  const agent = agents.find((a) => a.id === editingAgentId);
  const label = agent ? agent.name : "this agent";
  if (!window.confirm(
    `Delete ${label}? Its training and every chat with it are removed. This cannot be undone.`
  )) return;
  const res = await authFetch(`/agents/${encodeURIComponent(editingAgentId)}`, { method: "DELETE" });
  if (!res.ok) {
    agentError.textContent = "Could not delete this agent.";
    return;
  }
  currentAgentId = null;
  closeAgentModal();
  await loadAgents();
}

async function loadAgentDocuments(agentId) {
  agentDocList.innerHTML = "";
  const res = await authFetch(`/agents/${encodeURIComponent(agentId)}/documents`);
  if (!res.ok) return;
  const docs = await res.json();
  if (docs.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nothing yet — add a document below.";
    agentDocList.appendChild(li);
    return;
  }
  docs.forEach((d) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = d.filename || d.source_id;
    li.appendChild(name);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "link-danger";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => untrainDocument(agentId, d.source_id));
    li.appendChild(remove);
    agentDocList.appendChild(li);
  });
}

async function untrainDocument(agentId, sourceId) {
  const res = await authFetch(
    `/agents/${encodeURIComponent(agentId)}/documents/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" }
  );
  if (res.ok) {
    await loadAgentDocuments(agentId);
    await loadAgents();
  }
}

async function trainAgent() {
  const file = agentTrainFile.files[0];
  if (!file || !editingAgentId) return;
  agentTrainStatus.textContent = `Training on ${file.name}…`;
  const data = new FormData();
  data.append("file", file);
  try {
    const res = await authFetch(`/agents/${encodeURIComponent(editingAgentId)}/train`, {
      method: "POST",
      body: data,
    });
    const body = await res.json();
    agentTrainStatus.textContent = res.ok
      ? `Trained on ${file.name}.`
      : errorText(body, res);
    if (res.ok) {
      await loadAgentDocuments(editingAgentId);
      await loadAgents();
    }
  } catch (err) {
    agentTrainStatus.textContent = err.message;
  } finally {
    agentTrainFile.value = "";
  }
}
```

- [ ] **Step 3: Hook `app.js` into the agent selection**

In `frontend/app.js`:

- Call `wireAgents()` inside `init()`, next to `wireAuthForm()`.
- In `enterApp()`, call `await loadAgents()` before `loadSessions()`.
- Add the callback `agents.js` expects:

```javascript
// Called by agents.js whenever the selected agent changes: chats belong to one
// agent, so switching agents clears the thread and reloads that agent's chats.
function onAgentChanged() {
  currentSessionId = null;
  clearThread("Type a message to start a new chat with this agent.");
  activeTitle.textContent = "RAG Chat";
  loadSessions();
}
```

- `ensureSession()` sends the agent id and refuses without one:

```javascript
async function ensureSession() {
  if (currentSessionId) return currentSessionId;
  if (!currentAgentId) throw new Error("Create an agent first.");
  const response = await authFetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: currentAgentId }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(errorText(body, response));
  currentSessionId = body.id;
  activeTitle.textContent = body.name;
  await loadSessions();
  return currentSessionId;
}
```

- In `doLogout()`, reset agent state: `agents = []; currentAgentId = null;`

- [ ] **Step 4: Add the styles**

Append to `frontend/styles.css`:

```css
.agent-bar { display: flex; gap: 0.4rem; padding: 0.6rem; align-items: center; }
.agent-bar select { flex: 1; min-width: 0; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,0.45);
         display: grid; place-items: center; z-index: 20; }
.modal[hidden] { display: none; }
.modal-card { background: var(--surface, #fff); padding: 1.25rem; border-radius: 8px;
              width: min(34rem, 92vw); max-height: 88vh; overflow-y: auto;
              display: flex; flex-direction: column; gap: 0.7rem; }
.modal-card label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.9rem; }
.modal-card label.checkbox { flex-direction: row; align-items: center; gap: 0.4rem; }
.modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; }
.modal-actions .danger { margin-right: auto; }
#agent-doc-list { list-style: none; padding: 0; margin: 0;
                  display: flex; flex-direction: column; gap: 0.3rem; }
#agent-doc-list li { display: flex; justify-content: space-between; gap: 0.5rem;
                     align-items: center; font-size: 0.9rem; }
.link-danger { background: none; border: none; color: #b3261e; cursor: pointer; }
.train-status { font-size: 0.85rem; min-height: 1.2em; }
.muted { opacity: 0.7; }
```

- [ ] **Step 5: Verify both scripts parse**

Run: `node -c frontend/agents.js && node -c frontend/app.js`
Expected: no output (success).

- [ ] **Step 6: Verify script order**

Run: `grep -n "script src" frontend/index.html`
Expected: `agents.js` appears before `app.js`.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: agent picker, create/manage modal, training UI"
```

---

### Task 12: Live smoke verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything.
- Produces: a report appended to `.superpowers/sdd/progress.md`.

- [ ] **Step 1: Confirm the migration ran**

The `agents` table and the reshaped `sessions` table must exist (Task 3, Step 2). Verify:

```bash
cd backend && .venv/bin/python -c "
import httpx
from dotenv import dotenv_values
c = dotenv_values('.env')
base = c['POWABASE_BASE_URL'].rstrip('/'); key = c['POWABASE_SERVICE_ROLE_KEY']
h = {'apikey': key, 'Authorization': 'Bearer ' + key}
for t in ('agents', 'sessions'):
    r = httpx.get(f'{base}/rest/v1/{t}?limit=1', headers=h, timeout=20)
    print(t, '->', r.status_code)
"
```

Expected: both `200`.

- [ ] **Step 2: Start the server**

```bash
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: "Application startup complete" with no orchestration bootstrap in the log.

- [ ] **Step 3: Verify each acceptance criterion against the running app**

Register a user, then confirm:

- [ ] Creating an agent returns 201 and it appears in `GET /agents` with `trained: false`.
- [ ] `POST /sessions` with another user's `agent_id` returns **404**.
- [ ] Training a PDF into the agent returns 200; `GET /agents/{id}/documents` lists it; the agent now reports `trained: true`.
- [ ] A **new** chat with that agent answers from the trained document with a citation — this is the whole point of the feature: training persists across chats.
- [ ] A PDF uploaded *inside* a chat is answerable in that chat but **not** in a second chat with the same agent — scratch isolation survives.
- [ ] Editing the agent's instructions changes its next answer, and `GET /agents/{id}` reflects the edit. Confirm the `powabase_agent_id` is unchanged (in-place PATCH, not recreate).
- [ ] An agent with `use_general_kb: false` does **not** answer from general knowledge; flipping it to `true` makes the same question answerable.
- [ ] Untraining the document removes it from `GET /agents/{id}/documents`.
- [ ] Deleting the agent removes its chats, and `GET /agents` no longer lists it.

- [ ] **Step 4: Record the results**

Append a Task 12 section to `.superpowers/sdd/progress.md` stating which criteria passed, any wire-format surprises, and anything deferred. Note that the file is gitignored — it is a local ledger.

- [ ] **Step 5: Clean up smoke data**

Delete the agents and chats created during the smoke via the API so the project is left tidy.

---

## Verification

Full suite: `cd backend && .venv/bin/python -m pytest -q`
Frontend syntax: `node -c frontend/app.js && node -c frontend/agents.js`
No stale references: `grep -rin "research\|orchestration" backend/app frontend README.md` → no matches
