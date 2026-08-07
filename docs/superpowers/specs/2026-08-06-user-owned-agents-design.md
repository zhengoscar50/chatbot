# User-Owned Agents — Design Spec

**Date:** 2026-08-06
**Status:** Approved for planning
**Replaces:** the session-owned-agent model and the Deep Research feature

## Goal

Let a user create and configure their own agents, train each on a permanent
knowledge base, and chat with them across many conversations. Training persists:
a document uploaded to an agent is available in every future chat with that
agent. Deep Research is removed.

## Context

Today a Powabase agent is created per **session** and destroyed with it. Sessions
own their documents, so nothing a user teaches the bot survives the conversation.
That made sense when the session was the unit of work; it does not survive the
shift to user-authored agents.

The change inverts ownership: the **agent** becomes durable and user-configured,
and a session becomes a conversation *with* an agent.

## Decisions

Each of these was settled during brainstorming; the rationale matters more than
the choice, because it is what a future reader will need.

1. **Two document tiers.** An agent has a permanent KB the user deliberately
   trains; a chat has an optional scratch KB private to that conversation. Both
   are in scope when answering. This preserves per-conversation isolation — the
   property the previous model was built to demonstrate — while adding
   persistence.

2. **Agents are private to their creator.** No sharing, no directory. Keeps the
   existing ownership model untouched: not yours means 404, never 403.

3. **Config surface:** name, instructions, grounding mode, model, and a
   general-knowledge toggle.

4. **The shared general KB survives, per-agent opt-in.** The admin surface stays;
   an agent only retrieves from it when its creator ticks the box.

5. **Existing data is wiped.** Three users and eleven sessions of test material
   are dropped rather than migrated. No backfill script, no dual-model period.

6. **One Powabase agent per user-created agent** — not per session. Powabase
   already separates the agent from the conversation thread
   (`run_agent(agent_id, message, session_id=…)`), so a single agent serves many
   chats with independent histories. Remote resources stop scaling with chat
   count, and editing an agent's instructions applies everywhere immediately.
   *Given up:* no way to pin an old chat to the instructions it was created
   under.

7. **Content-aware chunk/full routing on the permanent tier only.** Scratch
   uploads are throwaway context for one conversation, so they all go to a single
   chunk-embed KB. Caps retrieval at four KBs and removes a column.

## Object model

```
User ──owns──▶ Agent (persistent, configured, trained)
                 │
                 └──has many──▶ Chat (thread + optional scratch docs)
```

## Schema

`agents.id` is our row; `agents.powabase_agent_id` is the remote object. Keeping
these distinct matters because `sessions.agent_id` currently holds a *Powabase*
id and now becomes a foreign key to our own table.

```sql
create table if not exists public.agents (
  id                uuid primary key default gen_random_uuid(),
  owner_id          uuid not null,
  name              text not null,
  instructions      text not null default '',
  model             text not null,
  grounding         text not null default 'strict',   -- 'strict' | 'open'
  use_general_kb    boolean not null default false,
  powabase_agent_id text not null,
  kb_id             text,               -- permanent chunk KB, created lazily
  kb_full_id        text,               -- permanent full-doc KB, created lazily
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists agents_owner_updated_idx
  on public.agents (owner_id, updated_at desc);
alter table public.agents enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
```

`sessions` is dropped and recreated (the wipe makes this clean):

- `agent_id uuid not null` — FK to `agents.id`
- `kb_id text` — chat scratch KB, created lazily on first upload
- `kb_full_id` — **removed**
- `powabase_session_id text` — the conversation thread, unchanged
- `owner_id`, `name`, `created_at`, `updated_at` — unchanged

Written as a new numbered migration that drops and recreates, so the file history
records what changed rather than hiding it.

## Retrieval composition

Assembled per question from the agent's config; falsy ids are filtered out:

| Source | Column | When |
|---|---|---|
| Agent permanent, chunked | `agents.kb_id` | always |
| Agent permanent, full-doc | `agents.kb_full_id` | always |
| Chat scratch | `sessions.kb_id` | if this chat has uploads |
| Shared general | `app.state.general_kb_id` | only if `use_general_kb` |

An untrained agent with no uploads retrieves from nothing and answers from the
model. That is correct, not a failure state.

The retrieval gate is unchanged and still decides *whether* to retrieve.

## Prompt composition

The system prompt is the user's instructions plus a grounding clause. Strict mode
is scoped to the case where context was actually provided — otherwise the gate's
(correct) decision to skip retrieval on "hi" would produce "that isn't in my
documents."

- **strict:** "When context is provided, base your answer only on it and say
  plainly when it doesn't contain the answer. Respond normally to greetings and
  small talk."
- **open:** today's softer wording — use context when given, answer normally
  otherwise.

The user's instructions are preserved verbatim; the clause is appended.

## API surface

```
POST   /agents                       create
GET    /agents                       list mine
PATCH  /agents/{id}                  edit any config field
DELETE /agents/{id}                  cascade: Powabase agent, its KBs, its chats
POST   /agents/{id}/train            upload a document into the permanent KB
GET    /agents/{id}/documents        what this agent has been trained on
DELETE /agents/{id}/documents/{sid}  untrain one document
```

`/train` reuses `IngestService` and content-aware routing unchanged — the same
flow as `/admin/train`, targeting the agent's KB instead of the general one.

`POST /sessions` now requires `agent_id`. Chat's `/ingest/file` targets the
chat's scratch KB.

**Document list and delete are in v1 deliberately.** A permanent knowledge base
that cannot be inspected or pruned is a trap: without them, one bad PDF means
deleting and rebuilding the agent.

**Model validation** is delegated to Powabase at agent-creation time. A bad model
id surfaces as a 400 carrying the provider's message, rather than being checked
against a hardcoded list that would rot.

**`PATCH` requires a new client method.** `PowabaseClient` has
`create_agent`/`get_agent`/`delete_agent` but no update. Powabase does support
one — verified live 2026-08-06: `PATCH /api/agents/{id}` on an absent id returns
404 (route exists), while `PUT` and `POST` return 405 carrying
`Allow: PATCH, DELETE, HEAD, OPTIONS, GET`. So editing an agent is an in-place
`update_agent(agent_id, fields)` — **not** delete-and-recreate. This matters:
recreating would mint a new `powabase_agent_id` and orphan every existing chat
thread bound to the old one.

## UI

Two-level sidebar: the user's agents, and under the selected one, its chats. A
"New agent" form covers the five config fields. A manage view handles editing,
training uploads, and the trained-document list.

Agent management goes in a new `frontend/agents.js` rather than growing
`app.js`, which is already ~800 lines covering auth, sessions, chat, uploads, and
rendering. A second `<script>` tag, no bundler — consistent with `/admin` being a
separate page today.

## Removal: Deep Research

Nothing outside the feature depends on it.

- `app/api/routes/research.py`, `app/services/research_service.py`,
  `app/services/research_pipeline.py`
- `tests/unit/test_research_{pipeline,service}.py`, `test_routes_research.py`
- the seven `research_*` settings in `config.py`; the pipeline bootstrap and
  `research_jobs` store in `main.py`
- frontend button, poll loop, card renderer, styles; README section 8
- the orchestration client methods (`create_orchestration`,
  `add_orchestration_entity`, `list_orchestrations`,
  `run_orchestration_stream`) — dead once the feature goes
- live cleanup in Powabase: the `deep-research-pipeline` orchestration and its
  three agents

## Testing

Two tests carry the design:

- **Retrieval composition** — given an agent config, exactly which KB ids end up
  in scope, including the `use_general_kb` branch and the untrained-agent case. A
  regression here is silent and expensive; this is the same gap the deep-research
  review caught late.
- **Prompt composition** — strict vs open produce the intended system prompt with
  the user's instructions preserved verbatim.

Plus CRUD and ownership coverage on every new route (foreign agent → 404 both
ways), and cascade-on-delete.

The data wipe is a manual SQL step, not code.

## Out of scope

- Sharing agents, or any public directory
- Pinning a chat to the instructions it was created under
- Per-agent retrieval tuning (top_k, token budget) — global settings still
- Migrating existing sessions

## Risks

- **Cascade delete is the one destructive path.** Deleting an agent removes its
  Powabase agent, both permanent KBs, and every chat with it. Best-effort remote
  cleanup as `SessionService.delete` already does, so a stale remote resource
  never blocks the row delete — but the UI must confirm before calling it.
- **`sessions.agent_id` changes meaning** from a Powabase id to a local FK.
  Harmless under the wipe, actively confusing to anyone reading old code or
  migrations. The column comment and this spec are the mitigation.
- **Agents accumulate.** They no longer scale with chats, but nothing prunes an
  abandoned agent, and each one holds up to two KBs. Acceptable at this scale;
  a retention policy is a later product decision.
