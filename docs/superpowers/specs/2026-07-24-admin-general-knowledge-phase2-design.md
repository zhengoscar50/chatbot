# Admin General Knowledge — Phase 2 Design

Date: 2026-07-24

## Purpose

Add an **admin-curated, shared "general knowledge" base** that every session can
draw on, alongside that session's own uploaded documents. Only the admin (via a
password) can add documents to general knowledge; regular users just benefit
from it.

This is **Phase 2**, building on Phase 1 (saved sessions with per-session
isolation). Phase 1 left the hook: session agents are created per session, so
Phase 2 additionally links a shared general KB into each new session's agent.

## Confirmed scope decisions

- **Retrieval model:** in a session, the chatbot answers from **general
  knowledge + that session's own uploads**. (A session agent links both KBs.)
- **Admin gating:** a password in `.env` (`ADMIN_PASSWORD`). Only adding to
  general knowledge is gated; nothing else changes.
- **Admin UI:** a **separate `/admin` page** (its own URL) — password gate +
  general-knowledge uploader.
- **Scope of general KB:** **new sessions only.** Every session created from now
  on links the general KB; sessions created before Phase 2 keep only their own
  documents (no back-fill).

## The general Knowledge Base

- A single shared KB named `general-knowledge-kb`, **get-or-create by name**,
  provisioned once at startup. Its id is stored on `app.state.general_kb_id`.
- It **always exists** (empty until the admin trains it) and is **always linked**
  into new session agents — so general retrieval "just works" once documents are
  added, with no per-session change.
- Uses the same `chunk_embed`/`hybrid` defaults as session KBs.

## Admin authentication

- `ADMIN_PASSWORD` is an **optional** setting. If unset, the admin feature is
  **disabled**: admin endpoints return `403 admin not configured`, and the app
  still starts normally (existing setups are unaffected).
- When set, admin actions must include the password. There is no session/token —
  the password is sent with each admin request (a demo-grade gate, checked
  server-side; not hardened auth). The comparison uses a constant-time check
  (`hmac.compare_digest`).

## Backend

Config (`app/core/config.py`): add `admin_password: Optional[str] = None`.

Startup (`app/main.py` lifespan): after the connectivity check, get-or-create
the general KB, store `app.state.general_kb_id`, and construct `SessionService`
with that id so it links the general KB into new session agents.

General KB provisioning: a small helper `ensure_general_kb(client) -> kb_id`
(find-or-create `general-knowledge-kb` by name, using the existing
`list_knowledge_bases`/`create_knowledge_base` client methods). Reuses the same
list-key correctness (`knowledge_bases`) fixed in Phase 1.

`SessionService`: gains an optional `general_kb_id`. In `create_session`, after
creating the session KB + agent and linking the session KB, **also** link the
general KB to the agent when `general_kb_id` is set. So a new session's agent
searches [session KB, general KB].

New routes (`app/api/routes/admin.py`):

- `POST /admin/verify` — body `{password}`. Returns `200 {"ok": true}` if the
  password matches; `401` if wrong; `403` if `ADMIN_PASSWORD` is unset. Lets the
  admin page unlock its uploader.
- `POST /admin/train` — multipart `{password, file}`. Verifies the password
  (same 401/403), then ingests the PDF into the **general** KB via the existing
  `IngestService` (constructed with `app.state.general_kb_id`). Returns the same
  `IngestResponse{source_id, status}` as `/ingest/file`, with the same
  attention-required/timeout/failure handling.
- `GET /admin` — serves the admin page (`FileResponse` of `frontend/admin.html`).
  Registered before the static mount so the path resolves to the route.

A shared password-check helper raises the right HTTP error:
`_require_admin(password, settings)` → 403 if unset, 401 if mismatch, else pass.

## Frontend

New files: `frontend/admin.html`, `frontend/admin.js` (reusing `styles.css`).

- The admin page shows a **password field**; on submit it calls `POST
  /admin/verify`. On success it reveals a **general-knowledge uploader** (a file
  input + upload button + status), and keeps the password in memory for
  subsequent `/admin/train` calls. On 401 it shows "Incorrect password"; on 403,
  "Admin is not configured (set ADMIN_PASSWORD)".
- Uploading a PDF posts `{password, file}` to `/admin/train` and shows
  indexed/failed status, matching the chat app's attachment UX.
- A back-to-chat link.
- The main app's sidebar gains a small **"Admin"** link (an anchor to `/admin`).

## Data flow (admin training)

```
Admin opens /admin → enters password → POST /admin/verify
  200 → uploader unlocked
Admin uploads PDF → POST /admin/train {password, file}
  → verify password → IngestService(general_kb_id).ingest_pdf → indexed
From then on, every NEW session's chat retrieves general KB + its own uploads.
```

## Testing / verification

Unit tests (faked client/services, no network):

- `ensure_general_kb`: creates when absent (by name), reuses when present.
- `SessionService.create_session` links BOTH the session KB and the general KB
  to the new agent when `general_kb_id` is set; links only the session KB when
  it is `None` (Phase-1 behavior preserved).
- `POST /admin/verify`: 200 on match, 401 on mismatch, 403 when unset.
- `POST /admin/train`: 401/403 gating; on success constructs `IngestService`
  with the general KB id and returns the ingest result; attention/timeout/502
  paths behave like `/ingest/file`.
- `GET /admin` returns the admin page.

Manual proof (live Powabase, `ADMIN_PASSWORD` set):

1. Open `/admin`, enter the password, upload a general-knowledge PDF (e.g. a doc
   stating a distinctive fact) → indexed.
2. Create a **new** session (fresh user or new session), ask about that fact
   *without* uploading anything to the session → answered from general knowledge.
3. In the same session, upload a session-specific PDF and confirm the chat can
   use both (its own doc + general knowledge).
4. Confirm the wrong admin password is rejected (401), and that a session created
   before Phase 2 does not answer from general knowledge (new-sessions-only).

## Out of scope

- Back-filling general knowledge into pre-Phase-2 sessions.
- Managing/deleting individual general-knowledge documents from the UI.
- Real admin authentication (accounts, tokens, rate limiting).
