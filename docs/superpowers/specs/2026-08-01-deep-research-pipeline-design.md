# Deep Research Pipeline — Design Spec

**Date:** 2026-08-01
**Status:** Approved (design), pending implementation plan

## Goal

An opt-in **"Deep Research"** action that runs a multi-agent Powabase
**orchestration** (Researcher → Analyst → Writer) over a session's documents and
returns a structured, cited report. Heavier and slower than a normal chat turn,
so it's explicit and async.

## Feasibility (verified live)

- Powabase orchestration `/run/stream` **silently ignores injected context**
  (`context_handler_id` / `context_override` were dropped). So evidence must be
  put **in the `message`** — verified: a sequential orchestration answered
  correctly when the fact was in a `CONTEXT:` section of the message.
- Therefore a **single shared orchestration** works (no per-session agents); we
  build the input message from the session's retrieved evidence per request.

## Decisions

1. **Trigger:** an explicit **Deep Research button** in the composer. Normal
   messages use today's fast path, unchanged.
2. **Pipeline:** a **shared `sequential` orchestration** of 3 shared agents,
   find-or-created once at startup (like the router agent / general KB):
   - **Researcher** `gpt-4o-mini` — organize the retrieved evidence into key
     facts/claims, preserving citation markers.
   - **Analyst** `claude-sonnet-5` (higher reasoning) — synthesize: themes,
     comparisons, tensions, conclusions.
   - **Writer** `gpt-4o-mini` — a structured, cited markdown report.
3. **Evidence:** retrieve **broadly** (higher `top_k`, bigger budget than a
   normal turn) over the session's KBs + the general KB; **bypass the gate**
   (research always retrieves). Build `message = "CONTEXT:\n<evidence>\n\n
   RESEARCH QUESTION:\n<query>"`.
4. **Delivery: async job + poll** (a research run is ~30s–2min):
   - `POST /research {session_id, query}` → owner-gated → retrieve → create an
     in-memory job → schedule the background run → **202 `{job_id}`**.
   - Background: stream the orchestration; update the job's **stage** from
     `sequential_step` events (researching → analyzing → writing); on `complete`
     store the report; on error store failure.
   - `GET /research/status/{job_id}` (owner-gated) → `{status:
     "running"|"done"|"failed", stage?, report?, citations?, detail?}`.
5. **Citations** come from the retrieval step and are returned with the report.
6. **Job store:** an in-memory dict on `app.state` keyed by `job_id`, scoped to
   the owner. Lost on restart (a running job would be abandoned) — acceptable for
   this single-process demo; a durable queue is the production upgrade.

## Backend

### Client (`powabase_client.py`)
- `create_orchestration(name, strategy, orchestrator_config=None) -> dict`
  (`POST /api/orchestrations`).
- `add_orchestration_entity(orch_id, agent_id, role_description, position) ->
  dict` (`POST /api/orchestrations/{id}/entities`, `entity_type:"agent"`).
- `list_orchestrations() -> dict`; `list_orchestration_entities(orch_id) ->
  list` (for idempotent find-or-create).
- `run_orchestration_stream(orch_id, message, on_event)` — POST
  `/api/orchestrations/{id}/run/stream` with `httpx` streaming; parse SSE lines
  and call `on_event(event_name, data)` per event (the event name lives in each
  JSON body's `event` key, same as agent SSE). Used by the background task to
  track stage + capture the final `complete.content`.

### Research agents bootstrap (`services/research_pipeline.py`)
- Constants: agent names (`research-researcher`/`-analyst`/`-writer`), their
  system prompts, `ORCHESTRATION_NAME = "deep-research-pipeline"`, the stage
  labels, per-agent models.
- `ensure_research_pipeline(client, researcher_model, analyst_model,
  writer_model) -> str` — find-or-create the 3 agents (by name, via
  `list_agents`) and the sequential orchestration with them as ordered entities
  (idempotent); return the orchestration id. Stored on `app.state` at startup.
- `get_research_orchestration_id(request)` dependency.

### Research service (`services/research_service.py`)
- `ResearchService(client, orchestration_id, retrieval_kb_ids, top_k,
  max_context_tokens)`; `build_message(query, evidence) -> str`; and the
  background `run(job, query, evidence)` that streams the orchestration, maps
  `sequential_step`→stage, and fills the job's report/citations/status. (The
  in-memory job registry can be a small module-level/`app.state` structure.)

### Config
- `research_top_k` (default ~12), `research_max_context_tokens` (~24000),
  `research_researcher_model`/`research_analyst_model`/`research_writer_model`
  (defaults `gpt-4o-mini` / `claude-sonnet-5` / `gpt-4o-mini`).

### Routes (`api/routes/research.py`)
- `POST /research` — `get_current_user` → `get_owned_session` (404) →
  broad retrieval (context-handler over `[kb_id, kb_full_id, general_kb_id]`) →
  create job (owner = user id) → `background_tasks.add_task(...)` → 202 `{job_id,
  status:"running"}`.
- `GET /research/status/{job_id}` — owner-gated (job's owner must match the
  caller; unknown/foreign → 404) → the job's current state.

### main.py
- `ensure_research_pipeline(...)` at startup → `app.state.research_orchestration_id`;
  the in-memory job registry initialized on `app.state`.

## Frontend (`index.html`, `app.js`)

- A **"Deep Research"** button in the composer. On click (with a non-empty
  query): `POST /research` → show a **research card** in the thread with a
  live stage label (Researching… → Analyzing… → Writing…) → poll
  `GET /research/status/{job_id}` every ~3s → on `done`, render the report
  (markdown-ish) + citations; on `failed`, show the detail. Normal send is
  unchanged; the composer stays usable.

## Testing

- **Unit:** client orchestration methods (respx: create/add-entity/run-stream
  event parsing); `ensure_research_pipeline` find-or-create (fake client);
  `ResearchService.build_message` (evidence + question format), and `run`
  mapping `sequential_step`→stage + capturing the final report (fake streaming
  client); `POST /research` (owner 404, 202 + job created) and
  `GET /research/status` (running/done/failed, owner-gated 404 for a foreign
  job); the job registry.
- **Live smoke:** create a session, upload a multi-fact doc, POST /research a
  synthesis question → poll status through stages → a coherent report citing the
  doc. Non-owner status → 404.

## Non-goals

- No per-session orchestrations/agents (shared pipeline only).
- No durable job store / cross-restart recovery (in-memory).
- No autonomous KB-searching researcher (evidence is pre-retrieved and injected
  via the message — orchestrations can't search injected context anyway).
- No change to normal chat, the gate, ownership, or ingest.
