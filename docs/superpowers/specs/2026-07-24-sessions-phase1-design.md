# Saved Sessions with Per-Session Isolation — Phase 1 Design

Date: 2026-07-24

## Purpose

Replace the current per-profile model with a **session-centric** one: each user
has multiple **saved, resumable sessions**, and each session has its **own
isolated uploaded documents** and its **own message history**. Sessions are
listed by name in a left sidebar; the user picks which one to use.

This is **Phase 1** of a two-phase effort. **Phase 2** (separate spec) adds an
admin-only, shared "general knowledge" base that every session can also draw on.
Phase 1 deliberately leaves room for it: session agents are created so Phase 2
can additionally link the general KB into them.

## Terminology (to avoid "session" ambiguity)

- **User** — a profile: a typed name, no password. Groups that person's
  sessions. (Evolves the current "profile" concept.)
- **Session** — a saved, resumable conversation belonging to a user. Has a name,
  its own uploaded documents, and its own message history. This is the unit shown
  in the sidebar.
- **`powabase_session_id`** — Powabase's internal agent-session id (the message
  thread). Stored on our session row; never called "session" in the UI.

## How this supersedes the current profile model

Today: `ProfileService` provisions one KB + one agent **per profile**; `/chat`
and `/ingest/file` take a `profile`; the frontend has a profile bar.

After Phase 1:

- A **user** is just a slug used to group sessions — it no longer owns any
  Powabase resources.
- KB + agent move **down to the session level**: each session gets its own KB and
  agent, so a session's uploads are physically isolated from every other session
  (and every other user).
- `/chat` and `/ingest/file` take a `session_id` (which resolves to that
  session's own kb_id/agent_id); the frontend gains a sidebar of sessions.
- The old per-profile KBs/agents (`profile-<slug>-kb/agent`) and the `/profile`
  route become vestigial. They are left in place (harmless) but unused; not
  deleted.

## Data model — new `public.sessions` table

The per-user table. Created once via SQL (human step, below).

| column | type | notes |
| --- | --- | --- |
| `id` | uuid pk | `default gen_random_uuid()` |
| `user_slug` | text not null | which user owns it (for listing) |
| `name` | text not null | shown in the sidebar |
| `kb_id` | text not null | this session's Knowledge Base |
| `agent_id` | text not null | this session's agent |
| `powabase_session_id` | text | the message thread; null until the first message |
| `created_at` | timestamptz | `default now()` |
| `updated_at` | timestamptz | `default now()`; bumped on each message (sidebar ordering) |

Index: `(user_slug, updated_at desc)` for the sidebar list.

**RLS:** enabled with **no policies**, so the table is reachable only through our
backend using the Service Role key. The browser never touches it directly.

### One-time SQL migration (human step)

Provided to the user to paste into the Studio **SQL Editor** (or run via the
Database URL):

```sql
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_slug text not null,
  name text not null,
  kb_id text not null,
  agent_id text not null,
  powabase_session_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists sessions_user_updated_idx
  on public.sessions (user_slug, updated_at desc);
alter table public.sessions enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.
```

## Powabase resources per session

Provisioned when a session is created:

- KB named `session-<id>-kb`, agent named `session-<id>-agent`, agent linked to
  that session KB. (Phase 2 will additionally link the shared general KB into
  each session's agent so answers use general + session docs.)
- `<id>` is the session row's uuid.

**Isolation guarantee:** a session's agent is linked only to that session's KB,
so its `knowledge_search` can physically only retrieve that session's documents.
Uploads in a session go only to its KB. Therefore one session's (or user's)
documents can never surface in another.

> **Resource tradeoff (recorded):** this creates one KB + one agent per session.
> At demo scale this is fine and gives airtight isolation with the pattern we've
> already proven. If resource count later matters, the alternative is a single
> shared agent with per-run KB scoping (`knowledge_bases` on the run body) — but
> that needs live verification of Powabase's per-run retrieval-scoping behavior,
> so it is out of scope here.

## Backend

New/changed modules:

- `SessionStore` — CRUD over `public.sessions` via PostgREST (`/rest/v1/sessions`,
  two-header Service Role auth): `create(row) -> row`, `list_by_user(user_slug) ->
  [rows]`, `get(id) -> row | None`, `update(id, fields)`.
- `SessionService` — orchestration: `create_session(user, name) -> session`
  (slugify user, provision KB+agent, insert row, return it); `list(user) ->
  [{id, name, updated_at}]`; `get(id) -> row`; plus helpers the routes need.
  Created once and shared on `app.state`, like the current services.

Routes:

- `POST /sessions` — body `{user, name?}`. Provisions the session's KB+agent,
  inserts the row (name defaults to "New session" if omitted), returns
  `{id, name}`. Never returns kb_id/agent_id to the client.
- `GET /sessions?user=<name>` — the sidebar list: `[{id, name, updated_at}]`,
  newest first.
- `GET /sessions/{id}/messages` — resume: load the session's messages from its
  Powabase agent-session and return `[{role, text, citations}]` (empty if the
  session has no messages yet).
- `POST /chat` — body `{session_id, query}`. Loads the session row, runs its
  agent, threading `powabase_session_id` (captured from the first run and saved
  back), bumps `updated_at`, and — if the session still has its default name —
  renames it to the (truncated) first user message so the sidebar is meaningful.
  Returns `{answer, citations}`.
- `POST /ingest/file` — form `{session_id, file}`. Ingests the PDF into that
  session's KB.

Removed/repurposed: the `profile` field on `/chat` and `/ingest/file` becomes
`session_id`; `ProfileService`/`/profile` are superseded by `SessionService`
(the profile code is replaced, not extended).

Config: no new settings for Phase 1 — the sessions table uses the existing
`POWABASE_BASE_URL` + `POWABASE_SERVICE_ROLE_KEY` over PostgREST. (Phase 2 adds
`ADMIN_PASSWORD` and a general-KB id.)

## Message persistence & resume

Messages continue to live in Powabase's agent-sessions; resume reads them via
`GET /api/sessions/{powabase_session_id}/messages` and formats them for the UI.

> **Caveat to verify live:** whether reopened (historical) messages carry their
> original citations depends on exactly what Powabase's session-messages endpoint
> returns. Verified during implementation; worst case, historical messages render
> as text without citation pills (newly sent messages still get them).

## Frontend

Layout becomes **sidebar + chat area**:

- **Left sidebar:** a user field at the top (type a name → loads that user's
  sessions), a **"New session"** button, and a scrollable list of the user's
  sessions by name (newest first), the active one highlighted.
- **Main area:** the existing chat thread + composer, the thinking indicator,
  citations — all retained.
- **Interactions:**
  - Type/switch user → load that user's session list; clear the chat area; no
    active session until one is picked or created.
  - Click a session → load its messages into the thread, mark it active; uploads
    and chat now target that session.
  - "New session" → `POST /sessions`, prepend it to the list, open it (empty
    thread).
  - Paperclip upload and chat send both include the current `session_id`; both
    are disabled until a session is active.
- **Responsive:** the sidebar collapses / is toggleable on narrow screens; the
  page body never scrolls horizontally.

## Testing / verification

Unit tests (faked PostgREST + Powabase, no network):

- `SessionService.create_session` slugifies the user, provisions a KB+agent
  (agent linked to the session KB), inserts a row, returns `{id, name, ...}`.
- `list` returns a user's sessions newest-first; a different user's sessions are
  not included.
- `/chat` routes to the session's `agent_id`, threads `powabase_session_id`
  (captures it on the first run, reuses it after), and auto-names a
  default-named session from the first message.
- `/ingest/file` routes the upload to the session's `kb_id`.
- `GET /sessions/{id}/messages` returns the formatted messages; empty when the
  session has none.
- `/sessions` and `/sessions/{id}/messages` never leak kb_id/agent_id.

Manual isolation proof (live Powabase):

1. User `alice`, create session "Taxes", upload doc A, ask about A → cited answer.
2. Same user, "New session" "Recipes", upload doc B, ask about A's content →
   not found (session B can't see session A's upload).
3. Reopen "Taxes" from the sidebar → its messages are still there; asking about A
   works again.
4. Switch user to `bob` → alice's sessions are not listed; bob starts empty.

## Out of scope (Phase 2, separate spec)

- Admin password + admin panel.
- The shared general-knowledge KB and linking it into every session's agent.
- Deleting/renaming sessions from the UI (Phase 1 auto-names; manual
  rename/delete can come later).
