# LLM-Gated, Detached KB Retrieval — Design Spec

**Date:** 2026-07-28
**Status:** Approved (design), pending implementation plan

## Goal

Add an LLM "gate" that decides, per chat message, whether the knowledge base
is needed at all — so the app stops spending retrieval credits and chunk
tokens digging through the KB for questions that don't need it (greetings,
chit-chat, general-knowledge). In the same change, **detach the KB from the
agent**: retrieval becomes an explicit, gated step instead of a permanently
linked `knowledge_search` tool.

## Motivation

Today each session's agent has its KB (and the shared general KB)
**permanently linked**, so it carries a `knowledge_search` tool in every
ReAct run. The model self-decides whether to search, but: the tool schema is
always loaded, models over-call it, and every actual search costs retrieval
credits. The user wants a deterministic pre-flight decision that skips
retrieval when it clearly isn't needed.

## Decisions (locked during brainstorming)

1. **Architecture: detached + gated.** The agent has no permanently-linked
   KB. A cheap LLM gate decides per message whether to retrieve; retrieval,
   when needed, is an explicit Powabase context-handler call whose chunks are
   injected into the run.
2. **Agent topology: keep per-session agents.** Sessions still create their
   own KB and agent, but we **stop linking the KB to the agent**. (We did not
   collapse to a single shared responder agent — smaller footprint.)
3. **Uncertainty bias: retrieve when unsure.** The gate skips retrieval only
   on clearly KB-independent messages; borderline/ambiguous messages retrieve,
   favoring grounded correctness over maximum token savings.

## Powabase primitives used

- **Sync run** `POST /api/agents/{id}/run` — single LLM call, no tools, no
  ReAct loop. Used for the gate classification (and could serve the plain
  no-KB answer path).
- **Streaming run** `POST /api/agents/{id}/run/stream` — used for the actual
  answer. Accepts a `context_handler_id` as its (single) context source.
- **Context handler** `POST /api/context-handlers` with
  `{ query, knowledge_bases: [{ id, top_k? }], max_context_tokens? }` —
  standalone retrieval that returns chunks + a handler id for injection.
- Per-run **context sources are mutually exclusive**: at most one of
  `knowledge_bases` / `context_handler_id` / `context_override` /
  `context_items`. Since KBs are no longer linked, passing
  `context_handler_id` is unambiguous.

## Request flow (per chat message)

```
user message
  │
  ├─► GATE  (router agent, sync /run, gpt-4o-mini, temp 0, JSON response_format)
  │         input: current message + last 1–2 conversation turns
  │         output: { "needs_kb": true | false }   (unsure → true)
  │
  ├─ needs_kb = true ─► POST /api/context-handlers
  │                       query = user message
  │                       knowledge_bases = [session KB, general KB]
  │                     → run session agent /run/stream with context_handler_id
  │                       (citations enabled) → answer + citations
  │
  └─ needs_kb = false ─► run session agent (no context) → plain answer
```

Both paths pass the session's `powabase_session_id`, so conversation memory,
resume, and history are unchanged. Citations appear only on the retrieve path.

## Components

### Gate / router (new)
- A **single shared router agent**, find-or-created at startup (same pattern
  as the general KB): KB-less, model `gpt-4o-mini`, `settings.temperature = 0`,
  a strict classifier system prompt, and a JSON `response_format` of
  `{ "needs_kb": boolean }`.
- New service `gate_service.py` (or equivalent): `needs_kb(query, history) ->
  bool`. Builds the classifier input from the current message plus the last
  1–2 turns, calls the router agent via sync `/run`, parses the JSON.
- **Fail-safe:** if the gate call errors or returns unparseable output,
  default to `true` (retrieve), matching the "retrieve when unsure" bias.

### Retrieval + injection (new)
- New client method `create_context_handler(query, kb_ids, top_k,
  max_context_tokens)` → `POST /api/context-handlers`, returns the handler id
  (and chunks).
- Extend `run_agent` to accept an optional `context_handler_id` and pass it in
  the run payload.

### Chat orchestration (modified)
- `chat_service.py` orchestrates: call the gate; on `true`, create a context
  handler over `[session_kb_id, general_kb_id]` and run the agent with that
  handler id; on `false`, run the agent with no context. Existing error
  mapping (ModelBusy / InsufficientCredits / ProviderKey / RuntimeError) is
  preserved on both the gate call and the answer call.

### Session provisioning (modified)
- `session_service.create_session` still creates a KB and an agent, but **no
  longer calls `link_kb_to_agent`** (neither the session KB nor the general
  KB is linked). The stored `kb_id` / `agent_id` are retained; the general KB
  id remains available to the chat layer for retrieval.
- Startup (`main.py`) additionally ensures the shared router agent and stores
  its id in `app.state` for the gate service.

### Config (modified)
- New settings: router model (default `gpt-4o-mini`), retrieval `top_k`,
  `max_context_tokens`, and the number of history turns the gate sees
  (default 2). Existing `powabase_agent_model` stays the responder model.

## Error handling

- Gate call failure → treat as `needs_kb = true` (fail safe). If the retrieval
  call then fails, surface the existing `PowabaseAPIError` → 502 path.
- Insufficient credits / provider-key / throttle errors keep their current
  mappings on both the gate and answer runs.

## Testing

- **Unit (mocked client):**
  - gate → `true` routes through `create_context_handler` + run-with-handler.
  - gate → `false` routes to a plain run with no context source.
  - router JSON parsing (valid, malformed → fail-safe to retrieve).
  - `create_context_handler` sends the correct payload (both KB ids, top_k).
  - gate-call exception → falls back to retrieve.
- **Live smoke:** a greeting (expect skip: no citations) and a question about
  an uploaded document (expect retrieve: answer + ≥1 citation), against the
  real project.

## Known limitations / non-goals

- The gate decides from the **query** (and short history), not KB contents. A
  general-sounding question whose answer the admin actually trained into the
  general KB may be skipped. "Retrieve when unsure" softens this; contents-aware
  routing is out of scope.
- Per-session agents are kept (not collapsed to one shared responder). The
  existing per-session agents' now-unused linked KBs (on sessions created
  before this change) are left as-is.
- No change to the sessions table schema, the admin/general-knowledge flow, or
  the frontend.
