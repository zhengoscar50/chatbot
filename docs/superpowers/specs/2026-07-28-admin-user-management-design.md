# Admin User-Management Page — Design Spec

**Date:** 2026-07-28
**Status:** Approved (design), pending implementation plan

## Goal

Give the app owner an admin page to manage user accounts: list all users with
their session counts, view/read any user's sessions and conversations, reset a
user's password, rename a user, and delete a user (cascading their data). Also
make the username validation error human-readable.

## Decisions (locked during brainstorming)

1. **Admin auth: the existing shared `ADMIN_PASSWORD` gate** (not an admin
   role). New endpoints reuse `_require_admin`. Because several are `GET`s, the
   password is supplied via an **`X-Admin-Password` header** (a
   `require_admin_header` dependency wrapping `_require_admin`). The admin
   frontend already caches the password in memory and will send this header.
2. **Full capability set** (all confirmed): list users + counts; view a user's
   session list (metadata); **read any session's chat contents**; reset
   password; rename username; delete user (cascade).
3. **Passwords are never viewable** — reset only (argon2id hash stored; the new
   password is never echoed back).

## Security notes (conscious tradeoffs)

- **Read-chats** gives the admin full read access to every user's private
  conversations. Accepted by the owner.
- The **shared password** is now the single secret guarding destructive actions
  (delete user, reset password). Accepted; an admin-role upgrade (per-identity)
  remains the stronger future option.
- Reset-password sets a new argon2id hash; it never returns or logs the value.
  Rename validates + lowercases + checks uniqueness (same rules as register).

## API (all require `X-Admin-Password`; 403 if `ADMIN_PASSWORD` unset, 401 on mismatch)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/admin/users` | `[{id, username, created_at, session_count}]` |
| GET | `/admin/users/{user_id}/sessions` | `[{id, name, created_at, updated_at}]` for that user |
| GET | `/admin/sessions/{session_id}/messages` | `{messages:[{role,text,citations}]}` — read chat contents of ANY session |
| POST | `/admin/users/{user_id}/reset-password` | body `{password}` (≥8) → set new hash; 204 |
| PATCH | `/admin/users/{user_id}` | body `{username}` → rename; 200 `{id, username}` |
| DELETE | `/admin/users/{user_id}` | delete user + cascade all their sessions; 204 |

- 404 when `user_id`/`session_id` doesn't exist. Rename to an existing username → 409. Rename/reset validation failures → 422.

## Backend components

### Client (`powabase_client.py`) — new PostgREST methods
- `list_users() -> list` (GET `/rest/v1/users`, ordered by `created_at`).
- `list_all_sessions() -> list` (GET `/rest/v1/sessions`, for counting).
- `update_user(user_id, fields) -> None` (PATCH `?id=eq.<id>`).
- `delete_user(user_id) -> None` (DELETE `?id=eq.<id>`).
- (Existing: `get_user`, `get_user_by_username`, `get_session_row`,
  `list_sessions(owner_id)`, `get_session_messages`.)

### Admin-users service (`services/admin_users.py`)
- `list_users_with_counts(client) -> list` — join `list_users()` with per-owner
  counts from `list_all_sessions()`.
- `delete_user(client, session_service, user_id) -> bool` — look up the user
  (False if missing); for each of the user's sessions (`list_sessions(user_id)`)
  call `session_service.delete(session_id)` (cascades KB+agent+row, best-effort);
  then `client.delete_user(user_id)`; True.
- `reset_password(client, user_id, new_password) -> bool` — `get_user` (False if
  missing) → `update_user(user_id, {"password_hash": hash_password(new)})`.
- `rename_user(client, user_id, new_username) -> dict` — normalize/validate;
  ensure target not taken (`get_user_by_username`) → 409 via a typed error;
  `update_user(user_id, {"username": uname})`; return the updated identity.

### Admin auth dependency
- `require_admin_header(x_admin_password: str = Header(None))` calls
  `_require_admin(x_admin_password)` (reused from `admin.py`). Applied to all new
  endpoints.

### Routes (`api/routes/admin.py`, extended)
- The six endpoints above, each `Depends(require_admin_header)`; reuse
  `session_service`, `client`, and `_format_messages` (from sessions route) for
  the messages shape.

### Schemas
- `AdminResetPasswordRequest{password: str Field(min_length=8)}`
- `AdminRenameRequest{username: str}` (shares the username validator, below).

### Username validation (folded-in fix)
- Extract a shared `validate_username(value) -> str` (regex
  `^[A-Za-z0-9_.-]{3,32}$`) that raises a **friendly** message: "Username can
  only contain letters, numbers, dots, dashes, and underscores (3–32
  characters)." Use it via a pydantic `field_validator` on `RegisterRequest`
  (replacing the raw `pattern=`) and on `AdminRenameRequest`.

## Frontend (`frontend/admin.html`, `admin.js`, `admin.css`/`styles`)

- Below the existing general-knowledge trainer (shown after unlock), add a
  **Users** panel:
  - Fetch `GET /admin/users` (with `X-Admin-Password`), render a table:
    username · created · # sessions · actions.
  - Row actions: **Sessions** (expand → `GET /admin/users/{id}/sessions`, list
    names+dates; each session row has **Read** → `GET
    /admin/sessions/{id}/messages`, show the messages inline/modal); **Reset
    password** (prompt for a new password → POST); **Rename** (prompt → PATCH);
    **Delete** (confirm dialog → DELETE, then refresh the table).
  - All admin calls send the `X-Admin-Password` header from the cached password;
    a 401 clears it and returns to the gate. Errors rendered readably (reuse the
    `errorText`-style handling).

## Testing

- **Unit:** client `list_users`/`list_all_sessions`/`update_user`/`delete_user`
  (respx); `admin_users` service (counts join; cascade delete calls
  session_service.delete per session then delete_user; reset hashes; rename
  uniqueness→409); each admin route (missing/incorrect password → 403/401;
  happy paths; 404 for unknown ids; rename dup → 409; reset <8 → 422); the shared
  username validator (valid, space→friendly message, symbol-only→rejected).
- **Live smoke:** with `ADMIN_PASSWORD`, register two users each with a session;
  `GET /admin/users` shows both with counts; read one user's session messages;
  rename a user; reset a user's password and log in with the new one; delete a
  user and confirm their sessions/rows are gone and they can't log in.

## Non-goals

- No admin role / per-admin identity (shared password, by decision).
- No audit log of admin actions.
- No pagination (small scale); no user search/filter.
- No change to the RAG pipeline, session ownership model, or the Service-Role
  Powabase model.
