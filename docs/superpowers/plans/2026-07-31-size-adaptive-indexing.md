# Size-Adaptive Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each uploaded doc by size — small (≤128 KB) → a `full_document` KB (returned whole), large → a `chunk_embed` KB — with one hybrid retrieval spanning both plus the general KB, and a raised context budget so whole small docs actually land in the prompt.

**Architecture:** A session lazily grows two KBs: `kb_id` (chunk_embed) and a new `kb_full_id` (full_document). Upload picks the KB by `len(bytes)`; chat retrieves over both + general.

**Tech Stack:** FastAPI, httpx, PostgREST, pytest + respx.

## Global Constraints

- **Python 3.9.6**; modules with module-level `X | None` need `from __future__ import annotations` (powabase_client.py already has it).
- **Never commit secrets** (`.env` gitignored).
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 164 passing).
- **Migration 003 is a manual Studio step** run before the live smoke — the code must still create sessions on a not-yet-migrated DB (`create_session` never references `kb_full_id`).
- Exact values: threshold `full_document_max_bytes = 131072`; `retrieval_top_k = 8`; `retrieval_max_context_tokens = 16000`; full-doc KB name `session-<id>-full` with `indexing_config={"strategy": "full_document"}`; chunk KB name `session-<id>-kb` with `indexing_config=None`.
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.

---

## File Structure

- Create `backend/migrations/003_add_kb_full_id.sql`.
- Modify `backend/app/core/config.py` — new/changed settings.
- Modify `backend/app/clients/powabase_client.py` — `create_knowledge_base(indexing_config=None)`.
- Modify `backend/app/services/session_service.py` — `ensure_kb(row, full_document=False)`, delete also removes `kb_full_id`.
- Modify `backend/app/api/routes/ingest.py` — size → KB routing.
- Modify `backend/app/api/routes/chat.py` — 3-KB retrieval list.

---

### Task 1: Config + migration file

**Files:**
- Create: `backend/migrations/003_add_kb_full_id.sql`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Create the migration** — `003_add_kb_full_id.sql`:

```sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
alter table public.sessions add column if not exists kb_full_id text;
```

- [ ] **Step 2: Update the failing test** — in `test_config.py`, in the test that checks the gating/retrieval defaults, change the two asserts and add one:

```python
    assert s.retrieval_top_k == 8
    assert s.retrieval_max_context_tokens == 16000
    assert s.full_document_max_bytes == 131072
```

- [ ] **Step 3: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → FAIL.

- [ ] **Step 4: Implement** — in `config.py`, change the two defaults and add the new field:

```python
    retrieval_top_k: int = 8
    retrieval_max_context_tokens: int = 16000
    gate_history_turns: int = 2
    full_document_max_bytes: int = 131072
```

- [ ] **Step 5: Run full suite** — `.venv/bin/python -m pytest -q` → all pass. (Route tests override `get_settings` with explicit values, so the changed defaults only affect `test_config`.)

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: size-adaptive indexing config + kb_full_id migration"`

---

### Task 2: Client — indexing_config on create_knowledge_base

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Produces: `create_knowledge_base(name, description="", indexing_config=None)` — includes `indexing_config` in the POST body only when provided.

- [ ] **Step 1: Write failing tests** — append (respx):

```python
@respx.mock
def test_create_kb_includes_indexing_config_when_set():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    client = PowabaseClient(BASE_URL, "k")
    client.create_knowledge_base("n", description="d", indexing_config={"strategy": "full_document"})
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"name": "n", "description": "d", "indexing_config": {"strategy": "full_document"}}


@respx.mock
def test_create_kb_omits_indexing_config_when_none():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    client = PowabaseClient(BASE_URL, "k")
    client.create_knowledge_base("n")
    sent = json.loads(route.calls[0].request.content)
    assert "indexing_config" not in sent
```

(Add `import json` at the top of the file if it isn't already imported.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q` → FAIL.

- [ ] **Step 3: Implement** — replace `create_knowledge_base`:

```python
    def create_knowledge_base(
        self, name: str, description: str = "", indexing_config: dict | None = None
    ) -> dict:
        body: dict = {"name": name, "description": description}
        if indexing_config is not None:
            body["indexing_config"] = indexing_config
        response = self._client.post("/api/knowledge-bases", json=body)
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q` → PASS, then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: create_knowledge_base accepts indexing_config"`

---

### Task 3: session_service — two-KB ensure + delete cleanup

**Files:**
- Modify: `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_session_service.py`

**Interfaces:**
- Changes `ensure_kb(row)` → `ensure_kb(row, full_document: bool = False) -> str` (default False keeps the existing 1-arg ingest call working until Task 4). `delete` also best-effort-deletes `kb_full_id`.

- [ ] **Step 1: Update the tests** — in `test_session_service.py`:

Make the `FakeClient.create_knowledge_base` accept + record `indexing_config`:

```python
    def create_knowledge_base(self, name, description="", indexing_config=None):
        kb = {"id": f"kb-{name}", "name": name, "indexing_config": indexing_config}
        self.created_kbs.append(kb)
        return kb
```

Keep `test_ensure_kb_creates_and_persists_when_absent` (it calls `ensure_kb({"id": "s1", "kb_id": ""})` → default `full_document=False` → chunk KB `session-s1-kb`, column `kb_id`). Add:

```python
def test_ensure_kb_full_document_branch_creates_full_kb():
    client = FakeClient()
    kb_id = SessionService(client, model="m").ensure_kb({"id": "s1", "kb_full_id": ""}, full_document=True)
    assert kb_id == "kb-session-s1-full"
    assert client.created_kbs[0]["name"] == "session-s1-full"
    assert client.created_kbs[0]["indexing_config"] == {"strategy": "full_document"}
    assert client.updated == [("s1", {"kb_full_id": "kb-session-s1-full"})]


def test_ensure_kb_chunk_branch_passes_no_indexing_config():
    client = FakeClient()
    SessionService(client, model="m").ensure_kb({"id": "s1", "kb_id": ""})
    assert client.created_kbs[0]["indexing_config"] is None


def test_ensure_kb_full_returns_existing_without_creating():
    client = FakeClient()
    assert SessionService(client, model="m").ensure_kb(
        {"id": "s1", "kb_full_id": "kb-existing"}, full_document=True
    ) == "kb-existing"
    assert client.created_kbs == []
```

Update the delete test to also remove `kb_full_id`. In `test_delete_removes_resources_and_row`, set the row to include `kb_full_id` and assert both KB deletes:

```python
    client = FakeClient(rows=[{"id": "s1", "user_slug": "alice", "kb_id": "kb1", "kb_full_id": "kbf", "agent_id": "a1"}])
    ...
    assert set(client.deleted_kbs) == {"kb1", "kbf"}
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_session_service.py -q` → FAIL.

- [ ] **Step 3: Implement** — replace `ensure_kb`:

```python
    def ensure_kb(self, row: dict, full_document: bool = False) -> str:
        """Return the session KB id for this document class, creating it lazily.

        A session grows up to two KBs: a chunk_embed KB (``kb_id``) for large
        documents and a full_document KB (``kb_full_id``) for small ones. The
        first upload of each class creates that KB and persists its id.
        """
        column = "kb_full_id" if full_document else "kb_id"
        existing = row.get(column)
        if existing:
            return existing
        session_id = row["id"]
        if full_document:
            name = f"session-{session_id}-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"session-{session_id}-kb"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Documents for session {session_id}",
            indexing_config=indexing_config,
        )
        self.client.update_session(session_id, {column: kb["id"]})
        return kb["id"]
```

In `delete`, add the `kb_full_id` row to the cleanup tuple (before the agent row):

```python
        for resource_id, delete_fn in (
            (row.get("kb_id"), self.client.delete_knowledge_base),
            (row.get("kb_full_id"), self.client.delete_knowledge_base),
            (row.get("agent_id"), self.client.delete_agent),
        ):
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_session_service.py -q` → PASS, then full suite green (the ingest route still calls `ensure_kb(row)` → `full_document=False`, unchanged behavior).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: lazy two-KB provisioning (chunk + full_document) with delete cleanup"`

---

### Task 4: Route wiring — size routing on upload, 3-KB retrieval on chat

**Files:**
- Modify: `backend/app/api/routes/ingest.py`, `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_routes_ingest.py`

**Interfaces:** consumes `ensure_kb(row, full_document)` (Task 3), `settings.full_document_max_bytes` (Task 1).

- [ ] **Step 1: Update `test_routes_ingest.py`** — make the fake session service record the `full_document` flag it's called with, and add tests that a small upload routes `full_document=True` and a large one `False`:

```python
class RoutingSessionService:
    def __init__(self):
        self.calls = []
    def get_owned_session(self, session_id, owner_id):
        return {"id": session_id, "kb_id": "kb-1"}
    def ensure_kb(self, row, full_document=False):
        self.calls.append(full_document)
        return "kb-routed"

# small file (< 131072) -> full_document=True ; large file -> False.
# Build a >131072-byte upload for the large case, e.g. b"%PDF-1.4" + b"0" * 131073.
```

Update the existing `FakeSessionService.ensure_kb` signature to `ensure_kb(self, row, full_document=False)`. Add the two routing tests using `RoutingSessionService` + a `FakeIngestService` that accepts `kb_id == "kb-routed"`, asserting `svc.calls == [True]` (small) / `[False]` (large).

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_ingest.py -q` → FAIL.

- [ ] **Step 3: Implement `ingest.py`** — replace the `ensure_kb` call:

```python
    # Route by size: small docs get a full_document KB (returned whole on a
    # match); larger docs get the chunk_embed KB.
    full_document = len(content) <= settings.full_document_max_bytes
    kb_id = await run_in_threadpool(sessions.ensure_kb, row, full_document)
```

- [ ] **Step 4: Implement `chat.py`** — change the retrieval-KB list passed to `ChatService`:

```python
        [row["kb_id"], row.get("kb_full_id"), general_kb_id],
```

(ChatService already filters falsy ids, so a session with no full-doc KB is unaffected.)

- [ ] **Step 5: Run full suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: route uploads by size and retrieve over both session KBs"`

---

### Task 5: Live smoke verification

**Files:** none. Requires `.env`, network, and **migration 003 applied** in Studio first.

- [ ] **Step 1** Apply `003_add_kb_full_id.sql` in Powabase Studio; confirm `sessions.kb_full_id` exists. Restart the server; `/health` 200.
- [ ] **Step 2** Register a user; create a session.
- [ ] **Step 3 (small doc)** Upload a **small** PDF (< 128 KB) with a distinctive fact. Confirm a KB named `session-<id>-full` now exists and its strategy is `full_document` (via `GET /api/knowledge-bases/{id}`), and the session row's `kb_full_id` is set.
- [ ] **Step 4 (large doc)** Upload a **> 128 KB** PDF. Confirm a `session-<id>-kb` (chunk_embed) KB exists and `kb_id` is set.
- [ ] **Step 5 (retrieval spans both)** Ask a question answerable only from the small doc → complete, cited answer. Ask one answerable only from the large doc → cited answer. **This proves one hybrid retrieval spans full_document + chunk_embed KBs.** If the large-doc (or small-doc) question fails to retrieve, note it — that would mean a single call can't mix the strategies and we'd need two calls (escalate before merge).
- [ ] **Step 6** Delete the session → confirm both KBs and the row are gone.
- [ ] **Step 7** Clean up the smoke user. Record observations in the task report. No commit.

---

## Self-Review

- **Spec coverage:** two lazy KBs (Task 3), size routing (Task 4), full_document strategy via indexing_config (Tasks 2–3), raised budget/top_k (Task 1), 3-KB retrieval (Task 4), migration (Task 1), delete cleanup (Task 3), live proof incl. the mixed-strategy-retrieval risk (Task 5). Covered.
- **Placeholder scan:** every code step is complete; the large-file test says exactly how to build a >131072-byte upload.
- **Type/name consistency:** `create_knowledge_base(..., indexing_config=None)`, `ensure_kb(row, full_document=False)`, `full_document_max_bytes`, columns `kb_id`/`kb_full_id`, KB names `session-<id>-kb`/`session-<id>-full` are used identically across producer/consumer tasks.
- **Green ordering:** Tasks 1–2 additive; Task 3 changes `ensure_kb` with a defaulted param so the unchanged ingest call keeps working; Task 4 wires the flag + retrieval list. Suite green after each.
