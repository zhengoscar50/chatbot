# Deep Research Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in "Deep Research" action: `POST /research` retrieves broadly over a session's docs, runs a shared 3-agent sequential orchestration (Researcher→Analyst→Writer) in the background, and the UI polls `GET /research/status/{job_id}` for live stage progress + the final cited report.

**Architecture:** A single shared orchestration + 3 agents, find-or-created at startup. Evidence is injected via the run **message** (orchestrations ignore context injection — verified). An in-memory job registry tracks each run.

**Tech Stack:** FastAPI (`BackgroundTasks`), httpx (streaming), Powabase orchestrations, pytest + respx, vanilla JS.

## Global Constraints

- **Python 3.9.6**; new modules with module-level `X | None` need `from __future__ import annotations`.
- **Never commit secrets.**
- **Keep the suite green after every task** (`cd backend && .venv/bin/python -m pytest -q`; currently 183 passing).
- Orchestration SSE: the event name lives inside each JSON body's `event` key (same as agent runs). `complete` carries the final `content`; `sequential_step` marks a pipeline stage advance; `error` marks failure.
- Some Powabase list/event shapes are confirmed in the live smoke (Task 6) — design robustly (fallbacks), don't hard-fail on a missing optional field.
- Commands assume CWD `backend/`, interpreter `.venv/bin/python`.

---

## File Structure

- Modify `backend/app/clients/powabase_client.py` — orchestration methods incl. a streaming runner.
- Modify `backend/app/core/config.py` — research settings.
- Create `backend/app/services/research_pipeline.py` — bootstrap the 3 agents + orchestration.
- Create `backend/app/services/research_service.py` — retrieve→message→run→job + the job registry.
- Modify `backend/app/models/schemas.py` — research request/response.
- Create `backend/app/api/routes/research.py` — `POST /research`, `GET /research/status/{id}`.
- Modify `backend/app/main.py` — bootstrap + job registry on `app.state`; register the router.
- Modify `frontend/index.html`, `frontend/app.js` — Deep Research button + poll + report.

---

### Task 1: Client orchestration methods (incl. streaming)

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- `create_orchestration(name, strategy, orchestrator_config=None) -> dict`
- `add_orchestration_entity(orch_id, agent_id, role_description, position=0) -> dict`
- `list_orchestrations() -> dict`
- `run_orchestration_stream(orch_id, message, on_event) -> None` — streams SSE, calling `on_event(name, data)` per event.

- [ ] **Step 1: Write failing tests** (respx). For the streaming test, return an SSE body and collect events:

```python
@respx.mock
def test_create_orchestration_and_entity():
    respx.post(f"{BASE_URL}/api/orchestrations").mock(return_value=httpx.Response(201, json={"id": "o-1"}))
    e = respx.post(f"{BASE_URL}/api/orchestrations/o-1/entities").mock(return_value=httpx.Response(201, json={"id": "e-1"}))
    c = PowabaseClient(BASE_URL, "k")
    assert c.create_orchestration("R", "sequential")["id"] == "o-1"
    c.add_orchestration_entity("o-1", "agent-1", "researcher", position=0)
    sent = json.loads(e.calls[0].request.content)
    assert sent == {"entity_type": "agent", "entity_ref_id": "agent-1", "role_description": "researcher", "position": 0}

@respx.mock
def test_run_orchestration_stream_emits_events():
    body = (
        'data: {"event": "sequential_step", "position": 0}\n\n'
        'data: {"event": "complete", "content": "Report."}\n\n'
    )
    respx.post(f"{BASE_URL}/api/orchestrations/o-1/run/stream").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    seen = []
    PowabaseClient(BASE_URL, "k").run_orchestration_stream("o-1", "hi", lambda n, d: seen.append((n, d)))
    assert ("sequential_step", {"event": "sequential_step", "position": 0}) in seen
    assert seen[-1][0] == "complete" and seen[-1][1]["content"] == "Report."
```

- [ ] **Step 2: Run, expect fail** — `.venv/bin/python -m pytest tests/unit/test_powabase_client.py -q` → FAIL.

- [ ] **Step 3: Implement** — add to `powabase_client.py` (a new `# Orchestrations` section). `run_orchestration_stream` uses `httpx` streaming and parses SSE incrementally (event name from the body's `event` key, mirroring `sse.parse_sse`):

```python
    def create_orchestration(self, name: str, strategy: str, orchestrator_config: dict | None = None) -> dict:
        body: dict = {"name": name, "strategy": strategy}
        if orchestrator_config is not None:
            body["orchestrator_config"] = orchestrator_config
        response = self._client.post("/api/orchestrations", json=body)
        self._raise_for_status(response)
        return response.json()

    def add_orchestration_entity(self, orch_id: str, agent_id: str, role_description: str, position: int = 0) -> dict:
        response = self._client.post(
            f"/api/orchestrations/{orch_id}/entities",
            json={"entity_type": "agent", "entity_ref_id": agent_id,
                  "role_description": role_description, "position": position},
        )
        self._raise_for_status(response)
        return response.json()

    def list_orchestrations(self) -> dict:
        response = self._client.get("/api/orchestrations")
        self._raise_for_status(response)
        return response.json()

    def run_orchestration_stream(self, orch_id: str, message: str, on_event) -> None:
        import json as _json
        with self._client.stream(
            "POST", f"/api/orchestrations/{orch_id}/run/stream",
            json={"message": message}, timeout=300.0,
        ) as response:
            if response.status_code >= 400:
                response.read()
                self._raise_for_status(response)
            buf: list = []
            def _flush():
                if not buf:
                    return
                payload = "\n".join(buf)
                buf.clear()
                try:
                    data = _json.loads(payload)
                except ValueError:
                    data = {"raw": payload}
                name = data.get("event") if isinstance(data, dict) else None
                on_event(name or "message", data)
            for line in response.iter_lines():
                if line == "":
                    _flush()
                elif line.startswith(":"):
                    continue
                elif line.startswith("data:"):
                    buf.append(line[len("data:"):].strip())
            _flush()
```

- [ ] **Step 4: Run** — target test file passes, then full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: Powabase orchestration client methods + streaming runner"`

---

### Task 2: Research settings + pipeline bootstrap

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/main.py`
- Create: `backend/app/services/research_pipeline.py`
- Test: `backend/tests/unit/test_config.py`, `backend/tests/unit/test_research_pipeline.py` (new), `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Config: `research_top_k=12`, `research_max_context_tokens=24000`, `research_researcher_model="gpt-4o-mini"`, `research_analyst_model="claude-sonnet-5"`, `research_writer_model="gpt-4o-mini"`.
- `ensure_research_pipeline(client, researcher_model, analyst_model, writer_model) -> str` (returns orchestration id); `get_research_orchestration_id(request)`.

- [ ] **Step 1: Config test** — add asserts for the five new settings; implement them in `config.py`.

- [ ] **Step 2: Write failing bootstrap test** — `test_research_pipeline.py` with a fake client (mirrors `test_router_agent.py`):

```python
from app.services.research_pipeline import ensure_research_pipeline, ORCHESTRATION_NAME, RESEARCHER_NAME

class FakeClient:
    def __init__(self):
        self.agents = []
        self.orchestrations = []
        self.entities = []
    def list_agents(self): return {"agents": self.agents}
    def create_agent(self, name, model, system_prompt, settings=None):
        a = {"id": f"a-{name}", "name": name}; self.agents.append(a); return a
    def list_orchestrations(self): return {"orchestrations": self.orchestrations}
    def create_orchestration(self, name, strategy, orchestrator_config=None):
        o = {"id": f"o-{name}", "name": name, "strategy": strategy}; self.orchestrations.append(o); return o
    def add_orchestration_entity(self, oid, agent_id, role_description, position=0):
        self.entities.append((oid, agent_id, role_description, position)); return {"id": "e"}

def test_creates_three_agents_sequential_orchestration_when_absent():
    c = FakeClient()
    oid = ensure_research_pipeline(c, "m1", "m2", "m3")
    assert oid == f"o-{ORCHESTRATION_NAME}"
    assert len(c.agents) == 3
    assert [e[3] for e in c.entities] == [0, 1, 2]  # ordered researcher/analyst/writer
    assert c.orchestrations[0]["strategy"] == "sequential"

def test_reuses_when_present():
    c = FakeClient()
    ensure_research_pipeline(c, "m1", "m2", "m3")
    n_before = (len(c.agents), len(c.orchestrations), len(c.entities))
    ensure_research_pipeline(c, "m1", "m2", "m3")  # idempotent
    assert (len(c.agents), len(c.orchestrations), len(c.entities)) == n_before
```

- [ ] **Step 3: Implement** — `research_pipeline.py`:

```python
from fastapi import Request

RESEARCHER_NAME = "research-researcher"
ANALYST_NAME = "research-analyst"
WRITER_NAME = "research-writer"
ORCHESTRATION_NAME = "deep-research-pipeline"

RESEARCHER_PROMPT = (
    "You are a research assistant. The user's message has a CONTEXT section "
    "(retrieved document excerpts, each with a [n] citation marker) and a "
    "RESEARCH QUESTION. Extract the key facts, claims, and figures from the "
    "CONTEXT that bear on the question, as a tight bulleted list, keeping each "
    "point's [n] citation marker. Do not add outside knowledge; if the context "
    "is thin, say so."
)
ANALYST_PROMPT = (
    "You are an analyst. Given the researcher's extracted facts (with [n] "
    "markers), synthesize an analysis of the research question: group themes, "
    "compare/contrast, note tensions or gaps, and draw supported conclusions. "
    "Keep the [n] markers on the claims they support. Reason carefully."
)
WRITER_PROMPT = (
    "You are a technical writer. Turn the analyst's synthesis into a clear, "
    "structured markdown report answering the research question: a short summary, "
    "then sections with headers, then a brief conclusion. Preserve the [n] "
    "citation markers inline. Do not invent facts beyond the analysis."
)


def _find_by_name(items, name):
    return next((i for i in items if i.get("name") == name), None)


def ensure_research_pipeline(client, researcher_model, analyst_model, writer_model) -> str:
    existing_agents = client.list_agents().get("agents", [])

    def ensure_agent(name, model, prompt):
        found = _find_by_name(existing_agents, name)
        if found:
            return found["id"]
        created = client.create_agent(name, model=model, system_prompt=prompt)
        existing_agents.append(created)
        return created["id"]

    r = ensure_agent(RESEARCHER_NAME, researcher_model, RESEARCHER_PROMPT)
    a = ensure_agent(ANALYST_NAME, analyst_model, ANALYST_PROMPT)
    w = ensure_agent(WRITER_NAME, writer_model, WRITER_PROMPT)

    orchestrations = client.list_orchestrations().get("orchestrations", [])
    orch = _find_by_name(orchestrations, ORCHESTRATION_NAME)
    if orch is not None:
        return orch["id"]
    orch = client.create_orchestration(ORCHESTRATION_NAME, "sequential")
    for position, agent_id in enumerate((r, a, w)):
        role = ("researcher", "analyst", "writer")[position]
        client.add_orchestration_entity(orch["id"], agent_id, role, position)
    return orch["id"]


def get_research_orchestration_id(request: Request) -> str:
    return request.app.state.research_orchestration_id
```

- [ ] **Step 4: Wire `main.py`** — build the orchestration id at startup (inside the existing try) and store it; also init the job registry:

```python
            research_orchestration_id = ensure_research_pipeline(
                client, settings.research_researcher_model,
                settings.research_analyst_model, settings.research_writer_model,
            )
        ...
        app.state.research_orchestration_id = research_orchestration_id
        app.state.research_jobs = {}
```

Update `test_main_lifespan.py`: monkeypatch `main_module.ensure_research_pipeline` to `lambda *a, **k: "orch-1"` and assert `app.state.research_orchestration_id == "orch-1"`.

- [ ] **Step 5: Run** full suite green.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: research settings + shared research pipeline bootstrap"`

---

### Task 3: Research service + job registry

**Files:**
- Create: `backend/app/services/research_service.py`
- Test: `backend/tests/unit/test_research_service.py` (new)

**Interfaces:**
- `build_message(query, evidence) -> str`
- A job dict shape: `{"status": "running"|"done"|"failed", "stage": str, "report": str|None, "citations": list, "detail": str|None, "owner": str}`.
- `run_research(client, orchestration_id, job, message)` — the background worker: streams the orchestration, advances `job["stage"]` on each `sequential_step`, sets `report`/`status="done"` on `complete`, `status="failed"`+`detail` on error/exception.

- [ ] **Step 1: Write failing tests** — drive a fake streaming client:

```python
from app.services.research_service import build_message, run_research, STAGES

def test_build_message_has_context_and_question():
    m = build_message("What changed?", "Excerpt A [1]\nExcerpt B [2]")
    assert "CONTEXT:" in m and "Excerpt A [1]" in m and "RESEARCH QUESTION:" in m and "What changed?" in m

class FakeStreamClient:
    def __init__(self, events): self.events = events
    def run_orchestration_stream(self, oid, message, on_event):
        for name, data in self.events: on_event(name, data)

def test_run_research_advances_stage_and_captures_report():
    events = [("sequential_step", {}), ("sequential_step", {}), ("sequential_step", {}),
              ("complete", {"content": "Final report."})]
    job = {"status": "running", "stage": STAGES[0], "report": None, "citations": [], "detail": None, "owner": "o1"}
    run_research(FakeStreamClient(events), "orch-1", job, "msg")
    assert job["status"] == "done" and job["report"] == "Final report."

def test_run_research_marks_failed_on_error_event():
    job = {"status": "running", "stage": STAGES[0], "report": None, "citations": [], "detail": None, "owner": "o1"}
    run_research(FakeStreamClient([("error", {"error": "boom"})]), "orch-1", job, "msg")
    assert job["status"] == "failed" and "boom" in (job["detail"] or "")
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement** — `research_service.py`:

```python
from __future__ import annotations

from app.clients.powabase_client import PowabaseAPIError

STAGES = ["Researching", "Analyzing", "Writing"]


def build_message(query: str, evidence: str) -> str:
    return (
        "CONTEXT:\n" + (evidence or "(no relevant excerpts found)") +
        "\n\nRESEARCH QUESTION:\n" + query
    )


def run_research(client, orchestration_id: str, job: dict, message: str) -> None:
    """Background worker: stream the orchestration into the job."""
    steps = {"n": 0}

    def on_event(name, data):
        if name == "sequential_step":
            steps["n"] += 1
            idx = min(steps["n"], len(STAGES) - 1)
            job["stage"] = STAGES[idx]
        elif name == "complete":
            if data.get("status") == "failed" or data.get("error"):
                job["status"] = "failed"; job["detail"] = data.get("error") or "Research run failed"
            else:
                job["report"] = data.get("content") or ""
                job["status"] = "done"
        elif name == "error":
            job["status"] = "failed"; job["detail"] = data.get("error") or data.get("message") or "Research run failed"

    try:
        client.run_orchestration_stream(orchestration_id, message, on_event)
    except PowabaseAPIError as e:
        job["status"] = "failed"; job["detail"] = str(e)
    if job["status"] == "running":  # stream ended without a terminal event
        job["status"] = "failed" if job["report"] is None else "done"
```

- [ ] **Step 4: Run** target tests pass, full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: research service (build message + streaming job worker)"`

---

### Task 4: Research routes + schemas

**Files:**
- Modify: `backend/app/models/schemas.py`, `backend/app/main.py`
- Create: `backend/app/api/routes/research.py`
- Test: `backend/tests/unit/test_routes_research.py` (new)

**Interfaces:**
- `ResearchRequest{session_id, query}`; `ResearchStartResponse{job_id, status}`; `ResearchStatusResponse{status, stage: Optional[str], report: Optional[str], citations: list, detail: Optional[str]}`.
- `POST /research` → 202 `{job_id, status:"running"}`; `GET /research/status/{job_id}` → the job (owner-gated).

- [ ] **Step 1: Add schemas** to `schemas.py` (mirror existing style; `Optional`/`Field` already imported).

- [ ] **Step 2: Write failing route tests** — mirror `test_routes_chat.py`: override `get_powabase_client`, `get_session_service`, `get_current_user`, `get_general_kb_id`, `get_research_orchestration_id`, `get_settings`. Cover:
  - `POST /research` unknown/non-owned session → 404; owned → 202 with a `job_id`, and a job registered in `app.state.research_jobs` with `owner == user id` and `status == "running"`. (Monkeypatch `research_route.run_research` to a no-op so the background task doesn't call Powabase.)
  - `GET /research/status/{job_id}` for the caller's job → 200 with its fields; for a **foreign** job (different owner) or unknown id → 404.

- [ ] **Step 3: Implement `research.py`:**

```python
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import ResearchRequest, ResearchStartResponse, ResearchStatusResponse
from app.services.general_kb import get_general_kb_id
from app.services.research_pipeline import get_research_orchestration_id
from app.services.research_service import STAGES, build_message, run_research
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/research", tags=["research"])


@router.post("", response_model=ResearchStartResponse, status_code=202)
def start_research(
    req: ResearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    general_kb_id: str = Depends(get_general_kb_id),
    orchestration_id: str = Depends(get_research_orchestration_id),
    settings=Depends(get_settings),
):
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    kbs = [
        {"id": kb, "top_k": settings.research_top_k}
        for kb in [row.get("kb_id"), row.get("kb_full_id"), general_kb_id] if kb
    ]
    try:
        handler = client.create_context_handler(req.query, kbs, settings.research_max_context_tokens)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    evidence = handler.get("formatted_context", "")
    citations = _citations_from(handler)

    job_id = str(uuid.uuid4())
    job = {"status": "running", "stage": STAGES[0], "report": None,
           "citations": citations, "detail": None, "owner": user["id"]}
    request.app.state.research_jobs[job_id] = job

    message = build_message(req.query, evidence)
    background_tasks.add_task(run_research, client, orchestration_id, job, message)
    return ResearchStartResponse(job_id=job_id, status="running")


@router.get("/status/{job_id}", response_model=ResearchStatusResponse)
def research_status(job_id: str, request: Request, user: dict = Depends(get_current_user)):
    job = request.app.state.research_jobs.get(job_id)
    if job is None or job.get("owner") != user["id"]:
        raise HTTPException(status_code=404, detail="Research job not found")
    return ResearchStatusResponse(
        status=job["status"], stage=job.get("stage"), report=job.get("report"),
        citations=job.get("citations", []), detail=job.get("detail"),
    )


def _citations_from(handler: dict) -> list:
    out = []
    for item in handler.get("retrieved_context", []):
        cid = item.get("source_name") or item.get("source_id")
        if cid and cid not in out:
            out.append(cid)
    return out
```

Register the router in `main.py` (`app.include_router(research_router)` before the StaticFiles mount).

- [ ] **Step 4: Run** — target tests pass, full suite green.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: /research start + status routes"`

---

### Task 5: Frontend — Deep Research button + polling

**Files:**
- Modify: `frontend/index.html`, `frontend/app.js`

- [ ] **Step 1: Add a "Deep Research" button** to the composer in `index.html` (a labeled button, e.g. `id="research-button"`, placed by the attach button). Keep it distinct from send.

- [ ] **Step 2: In `app.js`:**
  - On click (require a non-empty `chat-input` and an active/`ensureSession()` session): append the user query as a normal user bubble, clear the input, then `authFetch("/research", {method:"POST", body: JSON.stringify({session_id, query})})`.
  - Render a **research card** (an assistant-style row) showing a live stage label + a thinking indicator.
  - `pollResearch(jobId, cardEl)`: every ~3s `authFetch("/research/status/"+jobId)`:
    - `running` → update the card's stage label to `body.stage` (e.g. "Analyzing…"); keep polling; give up after ~5 min ("Still working — check back").
    - `done` → replace the card with the report (render `body.report`; a light markdown-to-HTML for headers/lists/bold is fine, or preserve line breaks) + a references list from `body.citations`.
    - `failed` → show `body.detail || "Research failed."` as an error.
  - Use the same token-guard pattern as the upload poller so a stale poll can't overwrite; the composer stays enabled throughout.

- [ ] **Step 3: Verify** — `node -c frontend/app.js`; confirm every new `getElementById` id exists; the research/status calls go through `authFetch`; report/query text rendered with `textContent`/safe DOM (no raw `innerHTML` with model/user text).

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: Deep Research button, stage polling, report rendering"`

---

### Task 6: Live smoke verification

**Files:** none. Requires `.env`, network, running server.

- [ ] **Step 1** Restart the server; `/health` 200. Confirm startup created `research-researcher`/`-analyst`/`-writer` agents and a `deep-research-pipeline` sequential orchestration (via `GET /api/agents` and `GET /api/orchestrations`) — and confirm the **list_orchestrations response key** and **sequential_step event shape** match what the bootstrap/`run_research` assume (adjust if the live shape differs).
- [ ] **Step 2** Register a user, create a session, upload a small multi-fact doc (a few distinct facts), wait indexed.
- [ ] **Step 3** `POST /research {session_id, query:"Summarize and analyze the key facts in my document."}` → 202 `{job_id}`. Poll `GET /research/status/{job_id}`: observe `stage` advancing (Researching→Analyzing→Writing), then `done` with a coherent report referencing the doc's facts + citations.
- [ ] **Step 4** Non-owner / unknown `GET /research/status/{id}` → 404.
- [ ] **Step 5** Clean up the session/user. (Leave the shared research agents/orchestration — reused.) Record observations, incl. the confirmed sequential_step/list shapes. No commit.

---

## Self-Review

- **Spec coverage:** orchestration client + streaming (Task 1), bootstrap + settings (Task 2), service + job + stage mapping (Task 3), routes + owner-gating + broad retrieval + citations (Task 4), button + poll + report (Task 5), live proof incl. shape confirmation (Task 6). Covered.
- **Placeholder scan:** backend steps carry complete code; the frontend task names ids, the poll behavior, the token-guard, and the no-`innerHTML` rule; Task 6 explicitly confirms the two uncertain live shapes.
- **Type/name consistency:** `create_orchestration`/`add_orchestration_entity`/`list_orchestrations`/`run_orchestration_stream`, `ensure_research_pipeline`/`get_research_orchestration_id`, `build_message`/`run_research`/`STAGES`, `research_jobs`, the `/research` routes and schemas used identically across tasks.
- **Green ordering:** Tasks 1–4 are additive (new methods/modules/routes; nothing existing changes behavior). Task 5 is frontend-only. Task 6 is verification.
