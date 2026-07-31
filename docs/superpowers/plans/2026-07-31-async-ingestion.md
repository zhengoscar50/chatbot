# Async Ingestion with Status Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload returns 202 immediately; the extract→add-to-KB→index pipeline finishes in a background task (fixing the orphan bug where slow extraction skipped `add_source_to_kb`); a status endpoint + frontend polling reflect progress. Chatting stays allowed.

**Architecture:** Split `IngestService` into `start` (upload) + `finish` (the slow pipeline). `POST /ingest/file` runs `start`, schedules `finish` via FastAPI `BackgroundTasks`, returns 202. New `GET /ingest/status/{source_id}` reports `processing|indexed|failed`. Frontend polls.

**Tech Stack:** FastAPI (`BackgroundTasks`), httpx, pytest, vanilla JS.

## Global Constraints

- **Python 3.9.6**; no new module-level `X | None` without `from __future__ import annotations`.
- **Never commit secrets** (`.env` gitignored).
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 171 passing).
- Status vocabulary exposed to the UI: `processing | indexed | failed` (+ optional `detail`).
- Ownership: `/ingest/status` is owner-gated exactly like `/ingest/file` and `/chat` (404 for a non-owner or unknown session).
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/core/config.py` — `ingest_background_max_wait_seconds`.
- Modify `backend/app/models/schemas.py` — `IngestStatusResponse`.
- Modify `backend/app/services/ingest_service.py` — `start`/`finish` split; `source_status()` helper.
- Modify `backend/app/api/routes/ingest.py` — 202 + background task; status endpoint.
- Modify `frontend/app.js` — poll after 202.

---

### Task 1: Config + status schema

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Update the failing config test** — in `test_config.py`'s retrieval/gating-defaults test, add:

```python
    assert s.ingest_background_max_wait_seconds == 600
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_config.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `config.py`, add after `ingest_max_wait_seconds`:

```python
    ingest_background_max_wait_seconds: int = 600
```

In `schemas.py`, add (note `Optional` is already imported):

```python
class IngestStatusResponse(BaseModel):
    source_id: str
    status: str
    detail: Optional[str] = None
```

- [ ] **Step 4: Run** — full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: config + schema for async ingest status"`

---

### Task 2: IngestService start/finish split + source_status

**Files:**
- Modify: `backend/app/services/ingest_service.py`
- Test: `backend/tests/unit/test_ingest_service.py`

**Interfaces:**
- Produces: `IngestService.start(filename, content) -> str`; `IngestService.finish(source_id) -> str`; module fn `source_status(client, source_id, kb_ids) -> tuple[str, str | None]`. `ingest_pdf` stays (now `start`+`finish`) so existing tests pass.

- [ ] **Step 1: Write failing tests** — append to `test_ingest_service.py` (reuse the file's `FakeClient`; check its `upload_source`/`get_source`/`add_source_to_kb`/`list_kb_sources` shapes and match them):

```python
def test_start_uploads_and_returns_source_id():
    client = FakeClient()  # its upload_source returns a source with an id
    service = IngestService(client, "kb-1", poll_interval=0, max_wait=1)
    assert service.start("doc.pdf", b"bytes")  # returns the source id


def test_finish_runs_extract_add_index():
    client = FakeClient()
    service = IngestService(client, "kb-1", poll_interval=0, max_wait=1)
    sid = service.start("doc.pdf", b"bytes")
    assert service.finish(sid) == "indexed"


def test_source_status_processing_indexed_failed():
    from app.services.ingest_service import source_status
    # extraction still going
    class C1:
        def get_source(self, s): return {"extraction_status": "extracting"}
    assert source_status(C1(), "s", ["kb-1"]) == ("processing", None)
    # needs OCR
    class C2:
        def get_source(self, s): return {"extraction_status": "attention_required"}
    assert source_status(C2(), "s", ["kb-1"])[0] == "failed"
    # extracted + indexed in the KB
    class C3:
        def get_source(self, s): return {"extraction_status": "extracted"}
        def list_kb_sources(self, kb): return {"items": [{"source_id": "s", "index_status": "indexed"}]}
    assert source_status(C3(), "s", ["kb-1"]) == ("indexed", None)
    # extracted but not yet added to any KB
    class C4:
        def get_source(self, s): return {"extraction_status": "extracted"}
        def list_kb_sources(self, kb): return {"items": []}
    assert source_status(C4(), "s", ["kb-1", ""]) == ("processing", None)
    # indexing failed
    class C5:
        def get_source(self, s): return {"extraction_status": "extracted"}
        def list_kb_sources(self, kb): return {"items": [{"source_id": "s", "index_status": "failed"}]}
    assert source_status(C5(), "s", ["kb-1"])[0] == "failed"
```

(Adjust `FakeClient` usage to whatever the file's existing fake returns for a successful path — the existing `test_ingest_pdf_success_path` shows the happy shape.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_ingest_service.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `ingest_service.py`, replace `ingest_pdf` with the split and keep a thin `ingest_pdf`:

```python
    def start(self, filename: str, content: bytes) -> str:
        return self.client.upload_source(filename, content)["id"]

    def finish(self, source_id: str) -> str:
        self._wait_for_extraction(source_id)
        self.client.add_source_to_kb(self.kb_id, source_id)
        return self._wait_for_indexing(source_id)

    def ingest_pdf(self, filename: str, content: bytes) -> dict:
        source_id = self.start(filename, content)
        return {"source_id": source_id, "status": self.finish(source_id)}
```

Add a module-level function (after the `IngestService` class):

```python
def source_status(client, source_id: str, kb_ids) -> tuple:
    """Coarse ingest status for a source: processing | indexed | failed."""
    ext = client.get_source(source_id).get("extraction_status")
    if ext == "attention_required":
        return "failed", "Needs OCR re-extraction (low-quality/scanned PDF)."
    if ext in ("failed", "cancelled"):
        return "failed", "Extraction failed."
    if ext != "extracted":
        return "processing", None  # pending / extracting / unknown
    for kb_id in kb_ids:
        if not kb_id:
            continue
        items = client.list_kb_sources(kb_id).get("items", [])
        entry = next((i for i in items if i.get("source_id") == source_id), None)
        if entry is None:
            continue
        idx = entry.get("index_status")
        if idx == "indexed":
            return "indexed", None
        if idx in ("failed", "cancelled"):
            return "failed", "Indexing failed."
        return "processing", None  # pending / indexing
    return "processing", None  # extracted but not added to a KB yet
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/unit/test_ingest_service.py -q` → PASS (existing `ingest_pdf` tests still pass), then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: IngestService start/finish split + source_status helper"`

---

### Task 3: Async ingest route + status endpoint

**Files:**
- Modify: `backend/app/api/routes/ingest.py`
- Test: `backend/tests/unit/test_routes_ingest.py`

**Interfaces:** `POST /ingest/file` → 202 `{source_id, status:"processing"}` + a background `finish`; new `GET /ingest/status/{source_id}?session_id=…` → `IngestStatusResponse`.

- [ ] **Step 1: Rewrite `test_routes_ingest.py`** — the upload no longer returns 200/indexed or maps sync errors; it returns **202 processing** and enqueues one background task. Replace the fake ingest service and the outcome tests:

```python
from types import SimpleNamespace

class FakeIngestService:
    def __init__(self, client, kb_id, poll_interval, max_wait):
        pass
    def start(self, filename, content):
        return "src-1"
    def finish(self, source_id):  # runs in the background task (TestClient executes it)
        return "indexed"
```

Update the happy-path test to expect 202 + `{"source_id":"src-1","status":"processing"}`. KEEP: `test_ingest_requires_session_id` (422), `test_ingest_404_for_missing_session`, `test_ingest_404_for_non_owned_session`, and the small-vs-large routing test (now asserting the routing flag via `ensure_kb`, upload still returns 202). REMOVE the now-obsolete sync-error tests (`...attention_required` 422, `...202_on_timeout`, extraction/indexing-failed 500) — those paths moved to the background/status endpoint. ADD status-endpoint tests:

```python
def test_status_reports_indexed(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr(ingest_route, "source_status", lambda client, sid, kb_ids: ("indexed", None))
    class SS:
        def get_owned_session(self, session_id, owner_id):
            return {"id": session_id, "kb_id": "kb-1", "kb_full_id": ""}
    app = FastAPI(); app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: SS()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    r = TestClient(app).get("/ingest/status/src-1?session_id=s1")
    assert r.status_code == 200 and r.json()["status"] == "indexed"

def test_status_404_for_non_owner(monkeypatch):
    set_env(monkeypatch)
    class SS:
        def get_owned_session(self, session_id, owner_id):
            return None
    app = FastAPI(); app.include_router(ingest_route.router)
    app.dependency_overrides[get_powabase_client] = lambda: object()
    app.dependency_overrides[get_session_service] = lambda: SS()
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    assert TestClient(app).get("/ingest/status/src-1?session_id=s1").status_code == 404
```

(Ensure `get_current_user`, `get_session_service`, `get_powabase_client`, `FastAPI`, `TestClient` are imported — they already are.)

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_routes_ingest.py -q` → FAIL.

- [ ] **Step 3: Implement `ingest.py`** — new imports + rewritten upload + status route:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import IngestResponse, IngestStatusResponse
from app.services.ingest_service import (
    AttentionRequiredError, ExtractionFailedError, IndexingFailedError,
    IngestService, IngestTimeoutError, source_status,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _run_finish(service: IngestService, source_id: str) -> None:
    # Background completion; failures are observable via GET /ingest/status.
    try:
        service.finish(source_id)
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

    full_document = len(content) <= settings.full_document_max_bytes
    kb_id = await run_in_threadpool(sessions.ensure_kb, row, full_document)
    service = IngestService(
        client, kb_id,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(_run_finish, service, source_id)
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/status/{source_id}", response_model=IngestStatusResponse)
async def ingest_status(
    source_id: str,
    session_id: str,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    kb_ids = [row.get("kb_id"), row.get("kb_full_id")]
    try:
        status, detail = await run_in_threadpool(source_status, client, source_id, kb_ids)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)
```

- [ ] **Step 4: Run full suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: async ingest (202 + background finish) and status endpoint"`

---

### Task 4: Frontend — poll status after upload

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Replace the upload result handling** in the `fileInput` change handler. After a successful/202 upload, show "Indexing…" and start polling instead of showing the raw status:

```js
    if (response.ok || response.status === 202) {
      showAttachment(file.name, "Indexing…", null);
      pollIngestStatus(sessionId, body.source_id, file.name);
    } else {
      showAttachment(file.name, errorText(body, response), "error");
    }
```

- [ ] **Step 2: Add the poller** near the other upload helpers. It polls every 3s, stops on `indexed`/`failed`, gives up after ~10 min, and cancels if a newer upload starts (token guard):

```js
let uploadPollToken = 0;

async function pollIngestStatus(sessionId, sourceId, fileName) {
  const myToken = ++uploadPollToken;
  const started = Date.now();
  while (myToken === uploadPollToken) {
    if (Date.now() - started > 10 * 60 * 1000) {
      showAttachment(fileName, "Still processing — check back shortly.", null);
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
    if (myToken !== uploadPollToken) return;
    try {
      const res = await authFetch(`/ingest/status/${sourceId}?session_id=${encodeURIComponent(sessionId)}`);
      if (!res.ok) continue; // transient — keep polling
      const body = await res.json();
      if (body.status === "indexed") {
        showAttachment(fileName, "indexed", "ok");
        return;
      }
      if (body.status === "failed") {
        showAttachment(fileName, body.detail || "Indexing failed.", "error");
        return;
      }
      // processing -> keep polling
    } catch (err) {
      // transient network error -> keep polling
    }
  }
}
```

Also set `uploadPollToken++` at the START of a new upload (in the `fileInput` handler, right after reading the file) so a second upload cancels the first poll. Confirm the composer is NOT disabled during polling (it isn't today).

- [ ] **Step 2b: Verify** — `node -c frontend/app.js`; confirm `pollIngestStatus`'s `getElementById` deps (via `showAttachment`) exist; grep that the status call goes through `authFetch` (carries the token) and hits `/ingest/status/`.

- [ ] **Step 3: Commit** — `git add -A && git commit -m "feat: poll ingest status and update the attachment chip"`

---

### Task 5: Live smoke verification

**Files:** none. Requires `.env`, network, running server.

- [ ] **Step 1** Restart the server; `/health` 200.
- [ ] **Step 2** Register a user; create a session.
- [ ] **Step 3 (the fix)** Upload a **large** PDF (>128 KB). Confirm the response is **202 `processing`** immediately (no multi-minute hang). Then poll `GET /ingest/status/{source_id}?session_id=…` until it returns **`indexed`** — proving the pipeline now completes (the orphan bug is gone). Confirm the source is actually in the chunk KB's sources with `index_status=indexed`.
- [ ] **Step 4** Ask a question answerable only from that large doc → cited answer (it's retrievable now).
- [ ] **Step 5** Upload a small valid PDF → 202, poll → indexed quickly; ask → cited answer.
- [ ] **Step 6** Non-owner / unknown session `GET /ingest/status/...` → 404.
- [ ] **Step 7** Delete the session; clean up the smoke user. Record observations. No commit.

---

## Self-Review

- **Spec coverage:** start/finish split (Task 2), 202 + background finish fixing the orphan bug (Task 3), status endpoint + mapping (Tasks 2–3), owner-gating (Task 3), config (Task 1), frontend poll + allow-chatting (Task 4), live proof the large doc completes (Task 5). Covered.
- **Placeholder scan:** complete code in every backend step; the frontend poller is fully written with the token-guard and timeout.
- **Type/name consistency:** `IngestService.start`/`finish`, `source_status(client, source_id, kb_ids)`, `IngestStatusResponse`, `ingest_background_max_wait_seconds`, `_run_finish`, route `GET /ingest/status/{source_id}` used identically across tasks.
- **Green ordering:** Tasks 1–2 additive (`ingest_pdf` preserved). Task 3 rewrites the route + its tests together (removing the now-invalid sync-error tests). Task 4 is frontend-only.
