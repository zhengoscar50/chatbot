# Content-Aware Indexing Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide `full_document` vs `chunk_embed` by the extracted text's `char_count` (post-extraction, in the async background step) instead of file bytes, so text-small documents return whole. Bump the context budget; backfill the existing constitution.

**Architecture:** The upload route no longer chooses a KB — it uploads and schedules a background task that, after extraction, reads `char_count`, picks the KB, ensures it, and indexes. `IngestService` gains phase methods; admin's synchronous `ingest_pdf` is preserved.

**Tech Stack:** FastAPI (`BackgroundTasks`), httpx, Powabase, pytest.

## Global Constraints

- **Python 3.9.6**; module-level `X | None` needs `from __future__ import annotations`.
- **Never commit secrets.**
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 180 passing).
- Routing rule: `full_document` iff `0 < char_count <= full_document_max_chars` (unknown/zero → `chunk_embed`).
- Exact values: `full_document_max_chars = 120000`; `retrieval_max_context_tokens = 32000`.
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/core/config.py` — swap the byte threshold for a char threshold; bump the context budget.
- Modify `backend/app/services/ingest_service.py` — phase methods.
- Modify `backend/app/api/routes/ingest.py` — content-based routing in the background.

---

### Task 1: Config — char threshold + bigger budget

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Update the failing test** — in the retrieval/gating-defaults test, change/add:

```python
    assert s.retrieval_max_context_tokens == 32000
    assert s.full_document_max_chars == 120000
```

Keep the existing `full_document_max_bytes == 131072` assertion for now (removed in Task 3).

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `config.py`: change `retrieval_max_context_tokens` default to `32000`; add `full_document_max_chars: int = 120000` (leave `full_document_max_bytes` in place — removed in Task 3).

- [ ] **Step 4: Run full suite** — all pass (route tests override `get_settings`; only `test_config` cares about defaults).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: char-based full_document threshold + larger context budget"`

---

### Task 2: IngestService phase methods

**Files:**
- Modify: `backend/app/services/ingest_service.py`
- Test: `backend/tests/unit/test_ingest_service.py`

**Interfaces:**
- Produces: `await_extraction(source_id) -> None`; `char_count(source_id) -> int`; `index_into(kb_id, source_id) -> str`. `finish`/`ingest_pdf` unchanged in behavior (refactored to use the new methods). `IngestService(client, kb_id=None, ...)` — `kb_id` becomes optional (default None) so the route can construct a service without choosing a KB.

- [ ] **Step 1: Write failing tests** — append to `test_ingest_service.py` (reuse the file's `FakeClient(source_statuses, index_statuses)`; note its `get_source` returns `{"extraction_status": ..., "error_message": "boom"}` — extend it to also return `auto_metadata` if needed, or add a dedicated fake):

```python
def test_char_count_reads_auto_metadata():
    class C:
        def get_source(self, s): return {"auto_metadata": {"char_count": 4200}}
    assert IngestService(C(), None, poll_interval=0, max_wait=1).char_count("s") == 4200

def test_char_count_zero_when_missing():
    class C:
        def get_source(self, s): return {}
    assert IngestService(C(), None, poll_interval=0, max_wait=1).char_count("s") == 0

def test_index_into_adds_then_waits():
    client = FakeClient(["extracted"], ["indexed"])
    svc = IngestService(client, None, poll_interval=0, max_wait=1)
    assert svc.index_into("kb-9", "src-1") == "indexed"
    assert client.added_to_kb == [("kb-9", "src-1")]

def test_await_extraction_ok_and_finish_still_works():
    client = FakeClient(["extracted"], ["indexed"])
    svc = IngestService(client, "kb-1", poll_interval=0, max_wait=1)
    svc.await_extraction("src-1")  # no raise
    assert svc.finish("src-1") == "indexed"  # admin path intact
```

(Confirm the file's `FakeClient.add_source_to_kb` records `added_to_kb`; it does.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_ingest_service.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `ingest_service.py`:

Make `kb_id` optional in `__init__` (default `None`); keep all else. Add methods and refactor `finish`:

```python
    def await_extraction(self, source_id: str) -> None:
        self._wait_for_extraction(source_id)

    def char_count(self, source_id: str) -> int:
        return self.client.get_source(source_id).get("auto_metadata", {}).get("char_count") or 0

    def index_into(self, kb_id: str, source_id: str) -> str:
        self.client.add_source_to_kb(kb_id, source_id)
        return self._wait_for_indexing(source_id)

    def finish(self, source_id: str) -> str:
        self.await_extraction(source_id)
        return self.index_into(self.kb_id, source_id)
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_ingest_service.py -q` → PASS (existing `ingest_pdf`/`finish`/`source_status` tests still green), then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: IngestService phase methods (await_extraction/char_count/index_into)"`

---

### Task 3: Route — decide the KB post-extraction by char_count

**Files:**
- Modify: `backend/app/api/routes/ingest.py`, `backend/app/core/config.py` (remove `full_document_max_bytes`)
- Test: `backend/tests/unit/test_routes_ingest.py`, `backend/tests/unit/test_config.py`

**Interfaces:** consumes `IngestService.start`/`await_extraction`/`char_count`/`index_into`, `SessionService.ensure_kb`, `settings.full_document_max_chars`.

- [ ] **Step 1: Update tests.**
  `test_config.py` — remove the `full_document_max_bytes` assertion.
  `test_routes_ingest.py` — the upload no longer routes at request time; routing happens in the background task (which TestClient runs after the response) via `char_count`. Rework the fakes so the route + background task work and assert the routed flag:

```python
class FakeIngestService:
    def __init__(self, client, kb_id=None, poll_interval=0, max_wait=0):
        pass
    def start(self, filename, content):
        return "src-1"
    def await_extraction(self, source_id):
        pass
    char_count_value = 100  # class attr the test tweaks for small/large
    def char_count(self, source_id):
        return type(self).char_count_value
    def index_into(self, kb_id, source_id):
        return "indexed"

class RoutingSessionService:
    def __init__(self):
        self.calls = []
    def get_owned_session(self, session_id, owner_id):
        return {"id": session_id, "kb_id": "", "kb_full_id": ""}
    def ensure_kb(self, row, full_document=False):
        self.calls.append(full_document)
        return "kb-routed"
```

  Tests: a small `char_count` (e.g. 100 ≤ 120000) → after the request, `svc.calls == [True]` (full_document); a large `char_count` (e.g. 200000) → `[False]`; the upload always returns 202 `{source_id:"src-1", status:"processing"}`. Keep the 422 (missing session_id), 404 (missing/non-owned session), and the status-endpoint tests. Remove any request-time byte-routing assertions.

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_ingest.py tests/unit/test_config.py -q` → FAIL.

- [ ] **Step 3: Implement `config.py`** — remove the `full_document_max_bytes` field.

- [ ] **Step 4: Implement `ingest.py`** — rewrite the upload handler and the background wrapper:

```python
def _run_finish(service, sessions, row, source_id, max_chars):
    # Runs post-response: decide the KB by extracted size, then index.
    try:
        service.await_extraction(source_id)
        full_document = 0 < service.char_count(source_id) <= max_chars
        kb_id = sessions.ensure_kb(row, full_document)
        service.index_into(kb_id, source_id)
    except (AttentionRequiredError, ExtractionFailedError, IndexingFailedError,
            IngestTimeoutError, PowabaseAPIError):
        pass


@router.post("/file", response_model=IngestResponse)
async def ingest_file(
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    content = await file.read()
    settings = get_settings()
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = IngestService(
        client,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(
        _run_finish, service, sessions, row, source_id, settings.full_document_max_chars
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )
```

(The `_run_finish` signature changed — its old form is fully replaced. `content` is no longer used for routing but is still read for the upload. The status endpoint is unchanged.)

- [ ] **Step 5: Run full suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: route documents by extracted char_count post-extraction"`

---

### Task 4: Backfill constitution + live smoke

**Files:** none. Requires `.env`, network, running server.

- [ ] **Step 1** Restart the server; `/health` 200.
- [ ] **Step 2 (backfill the reported doc)** For the constitution source (`1feec3dd-9f1e-43ff-916b-37d546d84786`, session `db685c19-…`), which is text-small (`char_count` 52900): ensure that session's `full_document` KB exists (create `session-<id>-full` with `indexing_config={"strategy":"full_document"}` + the reranker `retrieval_config`, persist `kb_full_id` on the row via PostgREST), `add_source_to_kb(full_kb, source)`, wait indexed. Best-effort remove the source from the old chunk KB. Then retrieve "Summarize the main articles of the Constitution" over the session's KBs → confirm the whole text (Preamble + Articles I/II/III + Bill of Rights) is returned.
- [ ] **Step 3 (new small doc → full_document)** Register a throwaway user, upload a small text PDF; poll `/ingest/status` to `indexed`; confirm the session row's `kb_full_id` is set (routed to full_document) and its KB strategy is `full_document`; ask a question → cited answer.
- [ ] **Step 4 (new large-text doc → chunk_embed)** Upload a PDF whose extracted text exceeds 120000 chars (e.g. generate ~150k chars of text → PDF); poll to `indexed`; confirm the session row's `kb_id` (chunk) is set and its KB strategy is `chunk_embed`.
- [ ] **Step 5** Clean up throwaway sessions/user. Record observations. No commit.

---

## Self-Review

- **Spec coverage:** char-based routing (Tasks 2,3), post-extraction decision in the background (Task 3), char threshold + budget (Task 1), `IngestService` phases (Task 2), admin `ingest_pdf` preserved (Task 2), byte field removed (Task 3), constitution backfill + new-doc routing proof (Task 4). Covered.
- **Placeholder scan:** every backend step has complete code; the large-text-doc test says how to build a >120k-char upload.
- **Type/name consistency:** `await_extraction`/`char_count`/`index_into`, `IngestService(client, kb_id=None, …)`, `_run_finish(service, sessions, row, source_id, max_chars)`, `full_document_max_chars`, `retrieval_max_context_tokens=32000` used identically across tasks.
- **Green ordering:** Task 1 additive (keeps the byte field). Task 2 additive (finish/ingest_pdf preserved). Task 3 atomically swaps the route to char-routing, removes the byte field, and updates both test files. Task 4 is ops/verification.
