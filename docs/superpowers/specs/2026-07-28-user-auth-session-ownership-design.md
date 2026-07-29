# User Accounts & Session Ownership — Design Spec

**Date:** 2026-07-28
**Status:** Approved (design), pending implementation plan

## Goal

Close the authentication/authorization gap: today "user" is just an
unauthenticated username typed in the browser, and every session-scoped route
(`/chat`, `/ingest/file`, session get/rename/delete/messages) takes only a
`session_id` with **no ownership check** — so anyone with a session UUID (or
who types another person's username) can read and use that session's data. This
feature adds real per-user accounts and enforces session ownership on every
session-scoped route.

## Decisions (locked during brainstorming)

1. **Credential model: username + password, self-contained** (not Powabase
   GoTrue, not passwordless keys). Passwords hashed with **argon2id**
   (`argon2-cffi`). Login yields a signed **JWT (HS256, 7-day expiry)** signed
   with a server secret `AUTH_JWT_SECRET` (required env, no default). Library:
   **PyJWT**.
2. **Token transport: `Authorization: Bearer <jwt>`**, JWT stored in the
   browser's `localStorage`. (Accepted tradeoff: XSS-exposed; an HttpOnly-cookie
   + CSRF design is the hardened alternative, out of scope.)
3. **Existing sessions: clean slate.** Current rows have no owner and become
   inaccessible under the new model (they are throwaway test data). No backfill.
4. **Open signup** — anyone may register.
5. **Ownership mismatch returns 404** (not 403), so a caller cannot probe
   whether another user's session id exists.
6. The **admin general-knowledge gate stays a separate password** (unchanged,
   not folded into user roles).

## Data model

- **New table `public.users`** (migration `002_create_users.sql`, run in Studio
  like `001`): `id uuid pk default gen_random_uuid()`, `username text not null
  unique` (case-insensitive uniqueness via `lower(username)` unique index),
  `password_hash text not null`, `created_at timestamptz default now()`. RLS
  enabled, no policies (Service Role only, as with `sessions`).
- **`public.sessions` gains `owner_id uuid`** (nullable for the pre-existing
  rows; new rows always set it). New index `sessions_owner_updated_idx
  (owner_id, updated_at desc)`. The old `user_slug` column stays (still written
  with the username slug for display/debug) but is no longer the access key.

## Backend components

### Auth core (new)
- `app/core/security.py` — `hash_password`, `verify_password` (argon2id);
  `create_access_token(user_id) -> str` and `decode_access_token(token) ->
  user_id` (PyJWT HS256, `AUTH_JWT_SECRET`, `exp`). Raises a typed error on
  invalid/expired.
- Config: `auth_jwt_secret: str` (required), `auth_token_ttl_hours: int = 168`.

### User store + service (new)
- `PowabaseClient` gains PostgREST user methods: `insert_user(row)`,
  `get_user_by_username(username)` (case-insensitive), `get_user(id)`.
- `app/services/auth_service.py` — `register(username, password) -> user`
  (uniqueness check → argon2 hash → insert; raises on duplicate),
  `authenticate(username, password) -> user` (lookup → verify hash; raises on
  bad creds).

### Auth dependency + routes (new)
- `app/api/routes/auth.py` — `POST /auth/register`, `POST /auth/login` (both
  return `{token, username}`), `GET /auth/me` (returns the current user).
- `get_current_user` dependency — reads the Bearer header, decodes the JWT,
  loads the user; **401** on missing/invalid/expired token or unknown user.

### Ownership enforcement (modified routes)
- `SessionService.create_session` takes `owner_id` (+ keeps username slug for
  `user_slug`); sets `owner_id` on the row. `SessionService.list(owner_id)`
  filters by `owner_id`. A helper `get_owned_session(session_id, owner_id)`
  returns the row only if it belongs to the caller, else `None`.
- Every session-scoped route depends on `get_current_user` and resolves the
  session via the ownership helper — **404** when the row is missing OR not
  owned by the caller:
  - `POST /chat`, `POST /ingest/file`, `GET/PATCH/DELETE /sessions/{id}`,
    `GET /sessions/{id}/messages`.
- `POST /sessions` uses `current_user` for the owner (drops the request `user`
  field). `GET /sessions` lists the current user's sessions (drops `?user=`).

### Schema changes
- `SessionCreateRequest` drops `user` (keeps optional `name`).
- New `RegisterRequest`/`LoginRequest` (`username`, `password` with min lengths),
  `AuthResponse` (`token`, `username`), `MeResponse` (`username`).

## Frontend

- A **login / register screen** gates the app (toggle between the two). On
  success, store `{token, username}` in `localStorage` and enter the app.
- All `fetch` calls attach `Authorization: Bearer <token>`. A **401 anywhere**
  clears the token and returns to the login screen.
- Sidebar shows the logged-in username and a **Logout** button (clears token,
  returns to login). The old free-text username box is removed.
- `POST /sessions` and `GET /sessions` no longer send a `user` value.

## Error handling

- Register with an existing username → **409**. Login/`register` with bad input
  → 422 (validation). Wrong password / unknown user on login → **401** (generic
  "invalid username or password", no user enumeration).
- Missing/invalid/expired token on any protected route → **401**.
- Session not owned or not found → **404** (uniform, no existence leak).

## Security posture

- **Closes:** session UUIDs are no longer sufficient to access data; every
  session-scoped action requires the authenticated owner.
- **Accepted (unchanged):** backend→Powabase still uses the Service Role key
  (tenancy enforced in the app layer); JWT in `localStorage` (XSS-exposed);
  open signup; admin gate remains a separate shared password.
- Password hashes never leave the backend; tokens are signed, expiring; login
  errors don't reveal whether a username exists.

## Testing

- **Unit:** `security` (hash/verify, token round-trip, expired/invalid token);
  `auth_service` (register happy, duplicate→409, authenticate happy, wrong
  password→raise); `get_current_user` (valid/missing/malformed/expired→401);
  auth routes (register/login/me); each session route (owner→200, non-owner→404,
  no token→401); `SessionService` owner filtering.
- **Live smoke:** register user A + user B; A creates a session and uploads a
  doc; **B addressing A's session id → 404**; A resumes fine; B's own session is
  isolated.

## Non-goals

- No email, password reset, email verification, or magic links.
- No roles/RBAC beyond the existing separate admin password.
- No refresh-token rotation (single 7-day access token; re-login on expiry).
- No change to the RAG/gating pipeline, the general-knowledge feature, or the
  Powabase Service-Role model.
- No HttpOnly-cookie/CSRF hardening (localStorage Bearer, by decision).
