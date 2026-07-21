# Multi-Profile Data Isolation — Design

Date: 2026-07-20

## Purpose

Upgrade the RAG chatbot from a single shared knowledge base to **multiple
lightweight profiles, each with fully isolated training data**. Documents
uploaded under one profile must never be visible to, or retrievable by, another
profile's chats.

This is the multi-tenancy step that the original design
([2026-07-18-rag-chatbot-design.md](2026-07-18-rag-chatbot-design.md)) listed as
a v1 non-goal.

## Scope decision: local demo, lightweight profiles

Confirmed with the user: this is a **local demonstration of data isolation**, not
a hardened multi-user product.

- A "profile" is just a name the user types (e.g. `alice`). No passwords, no
  real authentication.
- The property that must hold is **data isolation**: a profile's uploaded
  documents are searchable only by that same profile.
- The property that is explicitly **out of scope** is **security against
  impersonation**: because there is no password, anyone using the app can select
  any profile name. This demonstrates isolation of data, not access control.

This scoping means we do **not** need Powabase GoTrue auth, JWT verification, or
per-user RLS. Isolation is enforced entirely by which Knowledge Base each
request is routed to.

## Non-goals (this iteration)

- Passwords / authentication / access control.
- Hosting or deployment hardening (still runs locally via `uvicorn`).
- Deleting profiles or their documents from the UI (profiles are create-and-use
  only for this iteration).
- Migrating the two documents already in the current shared KB — they are left
  as-is and simply become unused by the new per-profile flow.

## Isolation approach: one Knowledge Base per profile

Powabase's AI layer (`ai.*` schema, agent tools, KB search) is **project-wide and
RLS-bypassed** — it does not isolate data per user on its own. Two ways to build
isolation on top of it were considered:

- **A — Per-profile Knowledge Base (chosen).** Each profile gets its own KB and
  its own agent linked only to that KB. A profile's agent has no path to another
  profile's documents; isolation is physical and cannot be bypassed by a
  forgotten filter.
- **B — One shared KB, per-document metadata filter.** Fewer Powabase resources,
  but every retrieval must remember to filter by profile; a single missed filter
  leaks data across profiles. Rejected — weaker guarantee, more error-prone, and
  the resource savings are irrelevant at demo scale.

## How a profile maps to its resources

A profile name is normalized to a safe slug (lowercase, trimmed,
non-alphanumeric runs collapsed to `-`). Resources are named deterministically
from the slug:

- Knowledge Base: `profile-<slug>-kb`
- Agent: `profile-<slug>-agent`, linked to that KB, using the model from
  `POWABASE_AGENT_MODEL`.

Resolution is **find-or-create**, reusing the existing bootstrap pattern: given a
profile name, look up its KB and agent by name; create them if absent; return
their IDs. Powabase is the source of truth (resources are discoverable by their
deterministic names), with an in-memory cache mapping `slug -> {kb_id, agent_id}`
so a warm profile does not re-list on every request. A simple per-slug lock
prevents two concurrent first-touch requests from double-creating.

The display name the user typed is preserved for the UI; only the slug is used
for Powabase resource names and cache keys. Two names that slug identically
(e.g. `Alice` and `alice`) intentionally map to the same profile.

## Backend changes

New module:

- `app/services/profile_service.py` — `ProfileService(client, model)` with
  `resolve(name) -> {"slug": str, "kb_id": str, "agent_id": str}`. Slugifies,
  checks the cache, else finds-or-creates the KB and agent (link KB to agent),
  caches, and returns. Holds the per-slug lock and the cache.

Changed routes (all resolve the profile first, then run against that profile's
KB/agent — `IngestService`/`ChatService` already accept these as arguments):

- `POST /ingest/file` — gains a `profile` form field alongside `file`.
- `POST /chat` — gains a `profile` field alongside `query`/`session_id`.
- `POST /profile` — new. Body `{ "profile": <name> }`. Ensures the profile's
  resources exist and returns `{ "profile": <display name>, "slug": <slug> }`.
  Called by the frontend on switch so provisioning latency and any error surface
  at switch time rather than on the first message. Never returns KB/agent IDs to
  the client.

Changed startup:

- `app/main.py` lifespan no longer pings a fixed `POWABASE_KB_ID` /
  `POWABASE_AGENT_ID`. It instead confirms Powabase is reachable with a single
  cheap authenticated call (`GET /api/agents`). The shared `ProfileService`
  (and its cache) is created in `lifespan` and stored on `app.state`, injected
  into routes via a dependency, mirroring how the single `PowabaseClient` is
  shared today.

Config:

- `POWABASE_KB_ID` and `POWABASE_AGENT_ID` are no longer required and are
  dropped from `Settings` and `.env.example`. `POWABASE_AGENT_MODEL` stays (used
  as the model for every profile's agent). The OpenRouter provider key remains a
  project-level Powabase setting shared by all profiles' agents — unchanged.

## Frontend changes

- A **profile bar** at the top of the page: a labeled text field showing the
  current profile, persisted in `localStorage`. Typing a name and pressing Enter
  (or blurring) switches profile.
- On switch: clear the message thread, reset `sessionId` to `null`, clear the
  attachment chip, then `POST /profile` and show a brief "Setting up <name>…" →
  "Profile: <name>" state.
- Every `/ingest/file` and `/chat` request includes the current profile name.
- On first load with no stored profile, the app prompts for a name before chat/
  upload are usable (a disabled composer with a "Enter a profile name to start"
  hint until one is set).

## Health endpoint

`GET /health` stays global (no profile) and reports `status` + `model`. It no
longer reports `kb_id`/`agent_id` (those are now per-profile). This is a
response-shape change to an endpoint used only for diagnostics.

## Isolation guarantee (why this holds)

- Uploads: `POST /ingest/file` adds the source only to the resolved profile's KB.
- Retrieval: a profile's agent is linked only to that profile's KB, so its
  `knowledge_search` tool can physically only search that KB.
- Therefore a profile's chat can never retrieve another profile's documents.

Caveat recorded for honesty: at the Powabase *project* level all KBs still live
under one project, and the Service Role key can read all of them. The app never
routes one profile's agent to another's KB, so this is not reachable through the
application — which is exactly the isolation a demo needs to show.

## Testing / verification

Unit tests (faked Powabase client, no network):

- `ProfileService.resolve` creates KB + agent when absent, links them, and
  returns their IDs.
- `resolve` reuses existing resources (by name) instead of creating duplicates.
- `resolve` caches: a second call for the same slug does not re-list/re-create.
- Slugification: `Alice` and `alice` resolve to the same slug/resources.
- `/ingest/file` and `/chat` route the request to the resolved profile's IDs
  (assert the service is constructed with the profile's kb_id/agent_id).
- `/profile` returns the display name + slug and never leaks resource IDs.

Manual isolation proof (against live Powabase):

1. Set profile `alice`, upload a document, confirm a question about it is
   answered with a citation.
2. Switch to profile `bob`, ask the same question, confirm bob's chat reports it
   has no such document (bob's KB is empty).
3. Switch back to `alice`, confirm the document is still there and answerable.
