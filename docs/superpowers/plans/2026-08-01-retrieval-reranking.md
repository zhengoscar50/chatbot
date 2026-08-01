# Retrieval Reranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a `cohere/rerank-english-v3.0` reranker to every KB the app creates (session chunk + full-document KBs, the general KB), demoting empty header chunks below real content. Backfill existing KBs. Query-time, no re-index. Proven live to fix the "only headers/page numbers" symptom.

**Architecture:** A reranker `retrieval_config` built from settings at startup, passed through `ensure_general_kb` and `SessionService.ensure_kb` to `create_knowledge_base`. A one-time backfill PATCHes existing KBs.

**Tech Stack:** FastAPI, httpx, PostgREST/Powabase, pytest + respx.

## Global Constraints

- **Python 3.9.6**; new modules with module-level `X | None` need `from __future__ import annotations`.
- **Never commit secrets.**
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 174 passing).
- Reranker is **optional/graceful**: an empty `reranker_model` setting → pass no reranker (plain hybrid). Powabase also fails-open if a configured reranker errors.
- Exact reranker config shape: `{"reranker": {"model": <model>, "candidate_count": <n>}}` (merged over KB retrieval defaults at create).
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/core/config.py` — reranker settings.
- Create `backend/app/services/retrieval.py` — `reranker_retrieval_config` helper.
- Modify `backend/app/clients/powabase_client.py` — `create_knowledge_base(retrieval_config=…)`, `update_knowledge_base`.
- Modify `backend/app/services/session_service.py` — thread `reranker_config` into `ensure_kb`.
- Modify `backend/app/services/general_kb.py` — `ensure_general_kb(client, reranker_config=None)`.
- Modify `backend/app/main.py` — build + wire the reranker config.

---

### Task 1: Config, helper, client methods

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/clients/powabase_client.py`
- Create: `backend/app/services/retrieval.py`
- Test: `backend/tests/unit/test_config.py`, `backend/tests/unit/test_powabase_client.py`, `backend/tests/unit/test_retrieval.py` (new)

**Interfaces:**
- Produces: `Settings.reranker_model` (default `"cohere/rerank-english-v3.0"`), `Settings.reranker_candidate_count` (default 20); `reranker_retrieval_config(model, candidate_count) -> dict | None`; `create_knowledge_base(..., retrieval_config=None)`; `update_knowledge_base(kb_id, fields) -> dict`.

- [ ] **Step 1: Write failing tests.**
  `test_config.py` — add to the defaults test: `assert s.reranker_model == "cohere/rerank-english-v3.0"` and `assert s.reranker_candidate_count == 20`.
  `test_retrieval.py` (new):

```python
from app.services.retrieval import reranker_retrieval_config

def test_returns_config_when_model_set():
    assert reranker_retrieval_config("cohere/rerank-english-v3.0", 20) == {
        "reranker": {"model": "cohere/rerank-english-v3.0", "candidate_count": 20}
    }

def test_returns_none_when_model_empty():
    assert reranker_retrieval_config("", 20) is None
```

  `test_powabase_client.py` (respx):

```python
@respx.mock
def test_create_kb_includes_retrieval_config_when_set():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    PowabaseClient(BASE_URL, "k").create_knowledge_base(
        "n", retrieval_config={"reranker": {"model": "m", "candidate_count": 20}}
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["retrieval_config"] == {"reranker": {"model": "m", "candidate_count": 20}}

@respx.mock
def test_update_knowledge_base_patches():
    route = respx.patch(f"{BASE_URL}/api/knowledge-bases/kb-1").mock(
        return_value=httpx.Response(200, json={"id": "kb-1"})
    )
    PowabaseClient(BASE_URL, "k").update_knowledge_base("kb-1", {"retrieval_config": {"x": 1}})
    assert json.loads(route.calls[0].request.content) == {"retrieval_config": {"x": 1}}
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_retrieval.py tests/unit/test_powabase_client.py -q` → FAIL.

- [ ] **Step 3: Implement.**
  `config.py` — add after `gate_history_turns`:

```python
    reranker_model: str = "cohere/rerank-english-v3.0"
    reranker_candidate_count: int = 20
```

  `app/services/retrieval.py`:

```python
from __future__ import annotations


def reranker_retrieval_config(model: str, candidate_count: int) -> dict | None:
    """Build a KB retrieval_config with a reranker, or None if disabled."""
    if not model:
        return None
    return {"reranker": {"model": model, "candidate_count": candidate_count}}
```

  `powabase_client.py` — extend `create_knowledge_base` and add `update_knowledge_base`:

```python
    def create_knowledge_base(
        self, name: str, description: str = "",
        indexing_config: dict | None = None, retrieval_config: dict | None = None,
    ) -> dict:
        body: dict = {"name": name, "description": description}
        if indexing_config is not None:
            body["indexing_config"] = indexing_config
        if retrieval_config is not None:
            body["retrieval_config"] = retrieval_config
        response = self._client.post("/api/knowledge-bases", json=body)
        self._raise_for_status(response)
        return response.json()

    def update_knowledge_base(self, kb_id: str, fields: dict) -> dict:
        response = self._client.patch(f"/api/knowledge-bases/{kb_id}", json=fields)
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Run** — the three test files pass, then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: reranker config helper + client retrieval_config/update_kb"`

---

### Task 2: Wire the reranker into KB creation

**Files:**
- Modify: `backend/app/services/session_service.py`, `backend/app/services/general_kb.py`, `backend/app/main.py`
- Test: `backend/tests/unit/test_session_service.py`, `backend/tests/unit/test_general_kb.py`, `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: `reranker_retrieval_config` (Task 1), `create_knowledge_base(retrieval_config=…)`.
- Changes: `SessionService(client, model, general_kb_id=None, reranker_config=None)`; `ensure_general_kb(client, reranker_config=None)`; both pass `retrieval_config` on KB create.

- [ ] **Step 1: Update tests.**
  `test_session_service.py` — the `FakeClient.create_knowledge_base` must accept `retrieval_config=None` and record it:

```python
    def create_knowledge_base(self, name, description="", indexing_config=None, retrieval_config=None):
        kb = {"id": f"kb-{name}", "name": name, "indexing_config": indexing_config, "retrieval_config": retrieval_config}
        self.created_kbs.append(kb)
        return kb
```

  Add a test that `ensure_kb` passes the service's reranker config:

```python
def test_ensure_kb_passes_reranker_config():
    client = FakeClient()
    svc = SessionService(client, model="m", reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    svc.ensure_kb({"id": "s1", "kb_id": ""})
    assert client.created_kbs[0]["retrieval_config"] == {"reranker": {"model": "m", "candidate_count": 20}}
```

  `test_general_kb.py` — its `FakeClient.create_knowledge_base` must accept `retrieval_config=None`; add:

```python
def test_ensure_general_kb_passes_reranker_config():
    client = FakeClient()
    ensure_general_kb(client, reranker_config={"reranker": {"model": "m", "candidate_count": 20}})
    assert client.created and client.created[0].get("retrieval_config") == {"reranker": {"model": "m", "candidate_count": 20}}
```

  (Update that file's fake `create_knowledge_base` to store `retrieval_config`.)

  `test_main_lifespan.py` — the `ensure_general_kb` monkeypatch now takes the extra arg: change it to `lambda client, reranker_config=None: "gkb-1"`.

- [ ] **Step 2: Run, expect fail** — those three test files → FAIL.

- [ ] **Step 3: Implement.**
  `session_service.py` — `__init__` gains `reranker_config`:

```python
    def __init__(self, client, model: str, general_kb_id: str | None = None, reranker_config: dict | None = None):
        self.client = client
        self.model = model
        self.general_kb_id = general_kb_id
        self.reranker_config = reranker_config
```

  In `ensure_kb`, pass it on the create:

```python
        kb = self.client.create_knowledge_base(
            name,
            description=f"Documents for session {session_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
```

  `general_kb.py` — `ensure_general_kb(client, reranker_config=None)`; on create pass `retrieval_config=reranker_config`.

  `main.py` — add `from app.services.retrieval import reranker_retrieval_config`; in lifespan, before `ensure_general_kb`:

```python
            reranker_config = reranker_retrieval_config(
                settings.reranker_model, settings.reranker_candidate_count
            )
            general_kb_id = ensure_general_kb(client, reranker_config)
```

  and pass `reranker_config` to the `SessionService(...)` construction.

- [ ] **Step 4: Run full suite** — all pass.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: attach reranker retrieval_config to every KB the app creates"`

---

### Task 3: Backfill existing KBs + live smoke

**Files:** none (operation + verification). Requires `.env`, network, running server.

- [ ] **Step 1: Restart the server;** `/health` 200.
- [ ] **Step 2: Backfill** — run a one-time script that lists all KBs, and for each whose name starts with `session-` OR equals `general-knowledge-kb`, reads its current `retrieval_config` (via `GET /api/knowledge-bases/{id}`), adds `"reranker": {"model": "cohere/rerank-english-v3.0", "candidate_count": 20}` to it (read-modify-write, preserving `method`/`context_mode`/`ts_language`), and PATCHes it back. Print each KB patched.
- [ ] **Step 3: Verify the fix** — retrieve over the existing `constitution.pdf` KB with "Summarize the main articles of the Constitution"; confirm the top results are real article **body text** (Article V/VI/VII bodies), not the `# Article. V.` + `CONSTITUTION OF THE UNITED STATES` header chunks.
- [ ] **Step 4: New-KB path** — register a throwaway user, create a session, upload a small doc, and confirm the newly-created KB's `retrieval_config` contains the reranker (via `GET /api/knowledge-bases/{id}`). Ask a question → cited answer. Clean up.
- [ ] **Step 5: Record** observations in the task report. No commit.

---

## Self-Review

- **Spec coverage:** reranker settings (Task 1), helper (Task 1), client create/update (Task 1), wiring into session + general KBs at create (Task 2), startup build (Task 2), backfill existing KBs (Task 3), live proof on the constitution doc (Task 3). Covered.
- **Placeholder scan:** every code step has complete code; the backfill's read-modify-write is described precisely.
- **Type/name consistency:** `reranker_retrieval_config`, `create_knowledge_base(retrieval_config=…)`, `update_knowledge_base`, `SessionService(..., reranker_config=None)`, `ensure_general_kb(client, reranker_config=None)`, `Settings.reranker_model`/`reranker_candidate_count` used identically across tasks.
- **Green ordering:** Task 1 additive (defaults on params). Task 2 changes two signatures with defaulted new params + updates the three affected tests/fakes together. Task 3 is ops/verification.
