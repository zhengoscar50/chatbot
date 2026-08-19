# Chatbots Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a `chatbots` layer between users and their agents, so one user can keep several separate assistants, each with its own agents and chats.

**Architecture:** A new `chatbots` table; `agents` and `sessions` gain a nullable `chatbot_id`; a SQL backfill puts everything that exists today into one auto-created "My chatbot" per user. Listing agents and chats becomes chatbot-scoped, guarded by an ownership check on the chatbot itself. Routing reads the chatbot from the chat row, never from the request.

**Tech Stack:** FastAPI, Powabase (PostgREST + agent API), pytest, vanilla JS frontend, Node's built-in test runner.

## Global Constraints

- Python 3.9 compatible: no PEP 604 unions in evaluated positions unless the module has `from __future__ import annotations`.
- Every new table gets `enable row level security` with no policies — only the Service Role key reads or writes.
- Ownership failures return **404**, never 403, so a foreign id is indistinguishable from a missing one.
- Migrations are applied by hand in Powabase Studio. New columns must be nullable so the running release keeps working.
- Tests live in `backend/tests/unit/`, run with `cd backend && .venv/bin/python -m pytest -q`.
- Frontend tests run with `node --test frontend/*.test.js`.
- Behaviour must be unchanged after the backfill. Any observable difference is a bug.

---

### Task 1: Migration and backfill

**Files:**
- Create: `backend/migrations/011_chatbots.sql`
- Create: `backend/scripts/verify_chatbot_backfill.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `public.chatbots(id, owner_id, name, description, created_at, updated_at)`; nullable `agents.chatbot_id` and `sessions.chatbot_id`.

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/011_chatbots.sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Adds a layer between a user and their agents: a user owns chatbots, a
-- chatbot owns agents and chats.
--
-- chatbot_id is NULLABLE and carries no foreign key on purpose. A NOT NULL FK
-- would fail on existing rows, and nullable means the currently deployed
-- release keeps working the moment this is applied. Tightening is a follow-up
-- once the backfill is verified.

create table if not exists public.chatbots (
  id          uuid primary key default gen_random_uuid(),
  owner_id    uuid not null,
  name        text not null,
  description text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists chatbots_owner_updated_idx
  on public.chatbots (owner_id, updated_at desc);
alter table public.chatbots enable row level security;
-- No policies: only the Service Role key (used server-side) can read/write.

alter table public.agents   add column if not exists chatbot_id uuid;
alter table public.sessions add column if not exists chatbot_id uuid;

-- One chatbot per user who already owns anything. Idempotent: re-running
-- cannot create a second chatbot for the same user.
insert into public.chatbots (owner_id, name, description)
select distinct owners.owner_id, 'My chatbot',
       'Everything that existed before chatbots were introduced.'
from (
  select owner_id from public.agents
  union
  select owner_id from public.sessions
) owners
where not exists (
  select 1 from public.chatbots c where c.owner_id = owners.owner_id
);

-- Stamp existing rows. Guarded by "is null" so a re-run never moves a row
-- that has since been reassigned.
update public.agents a
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = a.owner_id and a.chatbot_id is null;

update public.sessions s
   set chatbot_id = c.id
  from public.chatbots c
 where c.owner_id = s.owner_id and s.chatbot_id is null;
```

- [ ] **Step 2: Write the verification script**

```python
# backend/scripts/verify_chatbot_backfill.py
"""Prove the chatbot backfill lost nothing.

This is the first migration that rewrites existing rows rather than adding a
defaulted column, so the check is counts before and after — not "the page
still loads".

    python -m scripts.verify_chatbot_backfill
"""
import os
import sys

import httpx

BASE = os.environ["POWABASE_BASE_URL"].rstrip("/")
KEY = os.environ["POWABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def rows(path, **params):
    r = httpx.get(BASE + path, params=params, headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


users = {u["id"]: u["username"] for u in rows("/rest/v1/users", select="id,username")}
agents = rows("/rest/v1/agents", select="owner_id,chatbot_id")
sessions = rows("/rest/v1/sessions", select="owner_id,chatbot_id")
bots = rows("/rest/v1/chatbots", select="id,owner_id,name")

ok = True

orphan_agents = [a for a in agents if not a.get("chatbot_id")]
orphan_chats = [s for s in sessions if not s.get("chatbot_id")]
print("agents without a chatbot :", len(orphan_agents))
print("chats without a chatbot  :", len(orphan_chats))
ok &= not orphan_agents and not orphan_chats

owners_with_content = {a["owner_id"] for a in agents} | {s["owner_id"] for s in sessions}
per_owner = {}
for b in bots:
    per_owner.setdefault(b["owner_id"], []).append(b)
for owner in owners_with_content:
    n = len(per_owner.get(owner, []))
    print("%-14s chatbots: %d" % (users.get(owner, owner)[:14], n))
    ok &= n >= 1

print()
print("per-user counts (compare against the spec's table):")
for owner in owners_with_content:
    print("  %-14s %2d agents, %2d chats" % (
        users.get(owner, owner)[:14],
        sum(1 for a in agents if a["owner_id"] == owner),
        sum(1 for s in sessions if s["owner_id"] == owner)))

print("\nVERDICT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/011_chatbots.sql backend/scripts/verify_chatbot_backfill.py
git commit -m "feat: migration 011 — chatbots table and backfill"
```

---

### Task 2: Client queries for chatbots

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Test: `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Consumes: Task 1's schema.
- Produces:
  - `insert_chatbot_row(row: dict) -> dict`
  - `list_chatbot_rows(owner_id: str) -> list`
  - `get_chatbot_row(chatbot_id: str) -> dict | None`
  - `update_chatbot_row(chatbot_id: str, fields: dict) -> None`
  - `delete_chatbot_row(chatbot_id: str) -> None`
  - `list_agent_rows(chatbot_id: str) -> list` — **signature changes meaning**
  - `list_sessions(chatbot_id: str) -> list` — **signature changes meaning**

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/unit/test_powabase_client.py

@respx.mock
def test_get_chatbot_row_treats_a_malformed_id_as_not_found():
    """Same rule as agents and sessions: an id that cannot match anything is
    "not found", not a server error."""
    respx.get(f"{BASE_URL}/rest/v1/chatbots").mock(
        return_value=httpx.Response(400, json={"code": "22P02"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    assert client.get_chatbot_row("null") is None


@respx.mock
def test_list_agent_rows_is_scoped_by_chatbot():
    route = respx.get(f"{BASE_URL}/rest/v1/agents").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.list_agent_rows("cb-1")
    assert route.calls[0].request.url.params["chatbot_id"] == "eq.cb-1"


@respx.mock
def test_list_sessions_is_scoped_by_chatbot():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.list_sessions("cb-1")
    assert route.calls[0].request.url.params["chatbot_id"] == "eq.cb-1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_powabase_client.py -q -k "chatbot"`
Expected: FAIL — `AttributeError: 'PowabaseClient' object has no attribute 'get_chatbot_row'`, and the two scoping tests fail on `owner_id` being sent.

- [ ] **Step 3: Implement**

```python
    # add to PowabaseClient, beside the agent-row methods

    def insert_chatbot_row(self, row: dict) -> dict:
        response = self._client.post(
            "/rest/v1/chatbots", json=row, headers={"Prefer": "return=representation"}
        )
        self._raise_for_status(response)
        return response.json()[0]

    def list_chatbot_rows(self, owner_id: str) -> list:
        response = self._client.get(
            "/rest/v1/chatbots",
            params={"owner_id": f"eq.{owner_id}", "order": "created_at.asc"},
        )
        self._raise_for_status(response)
        return response.json()

    def get_chatbot_row(self, chatbot_id: str):
        response = self._client.get(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}
        )
        # A malformed id (not a valid uuid) -> PostgREST 400; it cannot match
        # any chatbot, so treat it as "not found" rather than a server error.
        if response.status_code == 400:
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None

    def update_chatbot_row(self, chatbot_id: str, fields: dict) -> None:
        response = self._client.patch(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}, json=fields
        )
        self._raise_for_status(response)

    def delete_chatbot_row(self, chatbot_id: str) -> None:
        response = self._client.delete(
            "/rest/v1/chatbots", params={"id": f"eq.{chatbot_id}"}
        )
        self._raise_for_status(response)
```

Then change the two existing methods to scope by chatbot:

```python
    def list_agent_rows(self, chatbot_id: str) -> list:
        response = self._client.get(
            "/rest/v1/agents",
            params={"chatbot_id": f"eq.{chatbot_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()

    def list_sessions(self, chatbot_id: str) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={"chatbot_id": f"eq.{chatbot_id}", "order": "updated_at.desc"},
        )
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_powabase_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/clients/powabase_client.py backend/tests/unit/test_powabase_client.py
git commit -m "feat: chatbot rows, and scope agent/session listing by chatbot"
```

---

### Task 3: ChatbotService

**Files:**
- Create: `backend/app/services/chatbot_service.py`
- Test: `backend/tests/unit/test_chatbot_service.py`

**Interfaces:**
- Consumes: Task 2's client methods.
- Produces: `ChatbotService(client)` with `create(owner_id, name, description="") -> dict`, `list(owner_id) -> list`, `get_owned(chatbot_id, owner_id) -> dict | None`, `rename(chatbot_id, name) -> None`, `delete(chatbot_id, owner_id, agents, sessions) -> bool`, and `get_chatbot_service(request)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_chatbot_service.py
from app.services.chatbot_service import ChatbotService, LastChatbotError


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updated = []
        self.deleted = []
        self._n = 0

    def insert_chatbot_row(self, row):
        self._n += 1
        row = dict(row, id="cb-%d" % self._n)
        self.rows.append(row)
        return row

    def list_chatbot_rows(self, owner_id):
        return [r for r in self.rows if r["owner_id"] == owner_id]

    def get_chatbot_row(self, chatbot_id):
        return next((r for r in self.rows if r["id"] == chatbot_id), None)

    def update_chatbot_row(self, chatbot_id, fields):
        self.updated.append((chatbot_id, fields))

    def delete_chatbot_row(self, chatbot_id):
        self.deleted.append(chatbot_id)
        self.rows = [r for r in self.rows if r["id"] != chatbot_id]


class FakeAgents:
    def __init__(self, ids=()):
        self.ids = list(ids)
        self.deleted = []

    def list(self, chatbot_id):
        return [{"id": i} for i in self.ids]

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        return True


class FakeSessions:
    def __init__(self, ids=()):
        self.ids = list(ids)
        self.deleted = []

    def list(self, chatbot_id):
        return [{"id": i} for i in self.ids]

    def delete(self, session_id):
        self.deleted.append(session_id)
        return True


def test_create_returns_the_new_chatbot():
    c = FakeClient()
    row = ChatbotService(c).create("o1", "Work", "work things")
    assert row["owner_id"] == "o1"
    assert row["name"] == "Work"
    assert row["description"] == "work things"


def test_get_owned_refuses_another_users_chatbot():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "someone-else", "name": "X"}])
    assert ChatbotService(c).get_owned("cb-1", "o1") is None


def test_get_owned_returns_your_own():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "X"}])
    assert ChatbotService(c).get_owned("cb-1", "o1")["id"] == "cb-1"


def test_delete_removes_its_agents_and_chats():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "A"},
                         {"id": "cb-2", "owner_id": "o1", "name": "B"}])
    agents, sessions = FakeAgents(["ag-1"]), FakeSessions(["s-1"])

    assert ChatbotService(c).delete("cb-1", "o1", agents, sessions) is True

    assert agents.deleted == ["ag-1"]
    assert sessions.deleted == ["s-1"]
    assert c.deleted == ["cb-1"]


def test_the_last_chatbot_cannot_be_deleted():
    """Otherwise agents have nowhere to live and the next login shows nothing."""
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "Only"}])
    try:
        ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions())
        assert False, "expected LastChatbotError"
    except LastChatbotError:
        pass
    assert c.deleted == []


def test_deleting_someone_elses_chatbot_reports_not_found():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "other", "name": "A"}])
    assert ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions()) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_chatbot_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.chatbot_service'`

- [ ] **Step 3: Implement**

```python
# backend/app/services/chatbot_service.py
from __future__ import annotations

from fastapi import Request

DEFAULT_CHATBOT_NAME = "My chatbot"


class LastChatbotError(Exception):
    """Deleting a user's only chatbot would leave their agents homeless."""


class ChatbotService:
    """A chatbot: a named group of agents and the chats that use them.

    The layer between a user and their agents. A user owns chatbots; a chatbot
    owns agents and chats. Routing considers only the agents inside the chat's
    chatbot.
    """

    def __init__(self, client):
        self.client = client

    def create(self, owner_id: str, name: str, description: str = "") -> dict:
        return self.client.insert_chatbot_row({
            "owner_id": owner_id,
            "name": name,
            "description": description,
        })

    def list(self, owner_id: str) -> list:
        return self.client.list_chatbot_rows(owner_id)

    def get_owned(self, chatbot_id: str, owner_id: str):
        row = self.client.get_chatbot_row(chatbot_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row

    def rename(self, chatbot_id: str, name: str) -> None:
        self.client.update_chatbot_row(chatbot_id, {"name": name})

    def delete(self, chatbot_id: str, owner_id: str, agents, sessions) -> bool:
        """Delete a chatbot and everything inside it.

        Returns False if it does not exist or is not yours — the caller turns
        that into a 404. Raises LastChatbotError rather than leaving a user
        with nowhere to put an agent.
        """
        row = self.get_owned(chatbot_id, owner_id)
        if row is None:
            return False
        if len(self.list(owner_id)) <= 1:
            raise LastChatbotError()
        for agent in agents.list(chatbot_id):
            agents.delete(agent["id"])
        for session in sessions.list(chatbot_id):
            sessions.delete(session["id"])
        self.client.delete_chatbot_row(chatbot_id)
        return True


def get_chatbot_service(request: Request) -> "ChatbotService":
    """FastAPI dependency returning the shared ChatbotService."""
    return request.app.state.chatbot_service
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_chatbot_service.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chatbot_service.py backend/tests/unit/test_chatbot_service.py
git commit -m "feat: ChatbotService"
```

---

### Task 4: Registration creates a chatbot

**Files:**
- Modify: `backend/app/services/auth_service.py`
- Modify: `backend/app/api/routes/auth.py`
- Test: `backend/tests/unit/test_auth_service.py`

**Interfaces:**
- Consumes: `ChatbotService.create` from Task 3.
- Produces: `AuthService(client, chatbots=None)`; `register` creates the default chatbot when a service is supplied.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/unit/test_auth_service.py

def test_registering_creates_a_default_chatbot():
    """The backfill only covers users who already own something, so without
    this a newly registered account has nowhere to put its first agent.

    Registration is also the only place this can happen exactly once — doing
    it lazily on first list would race two parallel requests into two
    chatbots.
    """
    class Chatbots:
        def __init__(self):
            self.created = []

        def create(self, owner_id, name, description=""):
            self.created.append((owner_id, name))
            return {"id": "cb-1"}

    client = FakeClient()          # already defined in this file
    bots = Chatbots()

    user = AuthService(client, chatbots=bots).register("alice", "pw-12345678")

    assert bots.created == [(user["id"], "My chatbot")]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_auth_service.py -q -k chatbot`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'chatbots'`

- [ ] **Step 3: Implement**

```python
# in backend/app/services/auth_service.py
from app.services.chatbot_service import DEFAULT_CHATBOT_NAME


class AuthService:
    def __init__(self, client, chatbots=None):
        self.client = client
        # Optional so existing call sites and tests that never register keep
        # working unchanged.
        self.chatbots = chatbots

    def register(self, username: str, password: str) -> dict:
        uname = username.strip().lower()
        if self.client.get_user_by_username(uname) is not None:
            raise DuplicateUsernameError(uname)
        try:
            user = self.client.insert_user(
                {"username": uname, "password_hash": hash_password(password)}
            )
        except PowabaseAPIError as e:
            # Unique-index race: two concurrent registers of the same name.
            if getattr(e, "status_code", None) == 409:
                raise DuplicateUsernameError(uname)
            raise
        if self.chatbots is not None:
            self.chatbots.create(user["id"], DEFAULT_CHATBOT_NAME)
        return user
```

In `backend/app/api/routes/auth.py`, pass the service through:

```python
from app.services.chatbot_service import ChatbotService, get_chatbot_service

@router.post("/register", response_model=AuthResponse)
def register(
    req: RegisterRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    ...
    user = AuthService(client, chatbots=chatbots).register(req.username, req.password)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_auth_service.py tests/unit/test_routes_auth.py -q`
Expected: PASS. If `test_routes_auth.py` errors on the missing dependency, add
`app.dependency_overrides[get_chatbot_service] = lambda: SimpleNamespace(create=lambda *a, **k: {"id": "cb-1"})`
to its app builder.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/auth_service.py backend/app/api/routes/auth.py backend/tests/unit/
git commit -m "feat: a new account starts with one chatbot"
```

---

### Task 5: Scope agents and chats to a chatbot

**Files:**
- Modify: `backend/app/services/agent_service.py`
- Modify: `backend/app/services/session_service.py`
- Test: `backend/tests/unit/test_agent_service.py`, `backend/tests/unit/test_session_service.py`

**Interfaces:**
- Consumes: Task 2's client methods.
- Produces: `AgentService.create(chatbot_id, owner_id, name, instructions, description, model, grounding, use_general_kb, max_context_tokens=None)`, `AgentService.list(chatbot_id)`, `SessionService.create_session(owner_id, chatbot_id, name=None)`, `SessionService.list(chatbot_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/unit/test_agent_service.py

def test_an_agent_records_the_chatbot_it_belongs_to():
    c = FakeClient()
    row = AgentService(c).create("cb-1", "o1", "T", "", "", "gpt-4o-mini",
                                 "strict", False)
    assert row["chatbot_id"] == "cb-1"
    assert row["owner_id"] == "o1"      # ownership is unchanged by the layer


def test_listing_is_scoped_to_one_chatbot():
    c = FakeClient()
    svc = AgentService(c)
    svc.create("cb-1", "o1", "A", "", "", "gpt-4o-mini", "strict", False)
    svc.create("cb-2", "o1", "B", "", "", "gpt-4o-mini", "strict", False)

    assert [a["name"] for a in svc.list("cb-1")] == ["A"]
```

```python
# append to backend/tests/unit/test_session_service.py

def test_a_chat_records_its_chatbot():
    c = FakeClient()
    row = SessionService(c).create_session("o1", "cb-1")
    assert row["chatbot_id"] == "cb-1"
    assert row["owner_id"] == "o1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_agent_service.py tests/unit/test_session_service.py -q -k "chatbot"`
Expected: FAIL — `create()` takes the wrong arguments.

- [ ] **Step 3: Implement**

In `agent_service.py`, add `chatbot_id` as the first parameter of `create` and store it:

```python
    def create(
        self,
        chatbot_id: str,
        owner_id: str,
        name: str,
        instructions: str,
        description: str,
        model: str,
        grounding: str,
        use_general_kb: bool,
        max_context_tokens=None,
    ) -> dict:
        self.probe_model(model)
        prompt = compose_system_prompt(instructions, grounding)
        agent = self.client.create_agent(
            f"user-agent-{name}", model=model, system_prompt=prompt
        )
        return self.client.insert_agent_row({
            "chatbot_id": chatbot_id,
            "owner_id": owner_id,
            "name": name,
            "instructions": instructions,
            "description": description,
            "model": model,
            "grounding": grounding,
            "use_general_kb": use_general_kb,
            "powabase_agent_id": agent["id"],
            "kb_id": None,
            "kb_full_id": None,
            "max_context_tokens": clamp_context_tokens(max_context_tokens, model),
        })

    def list(self, chatbot_id: str) -> list:
        """Agents in one chatbot. Ownership is checked on the CHATBOT by the
        caller — a chatbot narrows what you see, it does not replace who you
        are."""
        return self.client.list_agent_rows(chatbot_id)
```

In `session_service.py`:

```python
    def create_session(self, owner_id: str, chatbot_id: str, name: str | None = None) -> dict:
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "chatbot_id": chatbot_id,
            "name": name or DEFAULT_NAME,
        })

    def list(self, chatbot_id: str) -> list:
        rows = self.client.list_sessions(chatbot_id)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at"),
             "excluded_agent_ids": r.get("excluded_agent_ids") or []}
            for r in rows
        ]
```

Update the existing `FakeClient` in both test files so `insert_agent_row` /
`insert_session` echo `chatbot_id` back, and `list_agent_rows(chatbot_id)` /
`list_sessions(chatbot_id)` filter on it.

- [ ] **Step 4: Run the full suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: failures only in route tests that still call the old signatures — Task 6 and 7 fix those. Service-level tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_service.py backend/app/services/session_service.py backend/tests/unit/
git commit -m "feat: scope agents and chats to a chatbot"
```

---

### Task 6: Chatbot routes

**Files:**
- Create: `backend/app/api/routes/chatbots.py`
- Modify: `backend/app/models/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_routes_chatbots.py`

**Interfaces:**
- Consumes: `ChatbotService` (Task 3).
- Produces: `POST /chatbots`, `GET /chatbots`, `PATCH /chatbots/{id}`, `DELETE /chatbots/{id}`; schemas `ChatbotCreateRequest`, `ChatbotUpdateRequest`, `ChatbotResponse`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_routes_chatbots.py
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.chatbots import router
from app.services.agent_service import get_agent_service
from app.services.chatbot_service import ChatbotService, LastChatbotError, get_chatbot_service
from app.services.session_service import get_session_service


class FakeChatbots:
    def __init__(self, rows=None, raise_last=False):
        self.rows = list(rows or [])
        self.raise_last = raise_last
        self.renamed = []

    def create(self, owner_id, name, description=""):
        row = {"id": "cb-new", "owner_id": owner_id, "name": name,
               "description": description}
        self.rows.append(row)
        return row

    def list(self, owner_id):
        return [r for r in self.rows if r["owner_id"] == owner_id]

    def get_owned(self, chatbot_id, owner_id):
        return next((r for r in self.rows
                     if r["id"] == chatbot_id and r["owner_id"] == owner_id), None)

    def rename(self, chatbot_id, name):
        self.renamed.append((chatbot_id, name))

    def delete(self, chatbot_id, owner_id, agents, sessions):
        if self.raise_last:
            raise LastChatbotError()
        return self.get_owned(chatbot_id, owner_id) is not None


def build_app(bots):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_chatbot_service] = lambda: bots
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace()
    app.dependency_overrides[get_session_service] = lambda: SimpleNamespace()
    return app


def test_create_returns_201():
    bots = FakeChatbots()
    r = TestClient(build_app(bots)).post("/chatbots", json={"name": "Work"})
    assert r.status_code == 201
    assert r.json()["name"] == "Work"


def test_list_returns_only_your_own():
    bots = FakeChatbots(rows=[{"id": "cb-1", "owner_id": "o1", "name": "Mine",
                               "description": ""},
                              {"id": "cb-2", "owner_id": "other", "name": "Theirs",
                               "description": ""}])
    body = TestClient(build_app(bots)).get("/chatbots").json()
    assert [b["name"] for b in body] == ["Mine"]


def test_rename_404_for_another_users_chatbot():
    bots = FakeChatbots(rows=[{"id": "cb-1", "owner_id": "other", "name": "X",
                               "description": ""}])
    r = TestClient(build_app(bots)).patch("/chatbots/cb-1", json={"name": "new"})
    assert r.status_code == 404


def test_deleting_the_last_chatbot_is_a_400():
    bots = FakeChatbots(rows=[{"id": "cb-1", "owner_id": "o1", "name": "Only",
                               "description": ""}], raise_last=True)
    r = TestClient(build_app(bots)).delete("/chatbots/cb-1")
    assert r.status_code == 400
    assert "last" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_chatbots.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.routes.chatbots'`

- [ ] **Step 3: Add the schemas**

```python
# in backend/app/models/schemas.py

class ChatbotCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class ChatbotUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class ChatbotResponse(BaseModel):
    id: str
    name: str
    description: str = ""
```

- [ ] **Step 4: Implement the routes**

```python
# backend/app/api/routes/chatbots.py
"""Chatbots: a user's separate assistants, each owning its own agents and chats."""
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError
from app.models.schemas import (
    ChatbotCreateRequest,
    ChatbotResponse,
    ChatbotUpdateRequest,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.chatbot_service import (
    ChatbotService,
    LastChatbotError,
    get_chatbot_service,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/chatbots", tags=["chatbots"])


def _to_response(row: dict) -> ChatbotResponse:
    return ChatbotResponse(
        id=row["id"], name=row["name"], description=row.get("description") or ""
    )


@router.post("", response_model=ChatbotResponse, status_code=201)
async def create_chatbot(
    req: ChatbotCreateRequest,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        row = await run_in_threadpool(
            chatbots.create, user["id"], req.name, req.description
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _to_response(row)


@router.get("", response_model=list)
async def list_chatbots(
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        rows = await run_in_threadpool(chatbots.list, user["id"])
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [_to_response(r) for r in rows]


@router.patch("/{chatbot_id}", response_model=ChatbotResponse)
async def rename_chatbot(
    chatbot_id: str,
    req: ChatbotUpdateRequest,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        await run_in_threadpool(chatbots.rename, chatbot_id, req.name)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _to_response(dict(row, name=req.name))


@router.delete("/{chatbot_id}", status_code=204)
async def delete_chatbot(
    chatbot_id: str,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    agents: AgentService = Depends(get_agent_service),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        removed = await run_in_threadpool(
            chatbots.delete, chatbot_id, user["id"], agents, sessions
        )
    except LastChatbotError:
        raise HTTPException(
            status_code=400,
            detail="This is your last chatbot. Create another before deleting it.",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return None
```

In `main.py`, register the service and router:

```python
from app.api.routes.chatbots import router as chatbots_router
from app.services.chatbot_service import ChatbotService
...
        app.state.chatbot_service = ChatbotService(client)
...
    app.include_router(chatbots_router)
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_chatbots.py -q`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/chatbots.py backend/app/models/schemas.py backend/app/main.py backend/tests/unit/test_routes_chatbots.py
git commit -m "feat: chatbot routes"
```

---

### Task 7: Re-scope the existing routes

**Files:**
- Modify: `backend/app/api/routes/agents.py`, `sessions.py`, `chat.py`
- Test: `backend/tests/unit/test_routes_agents.py`, `test_routes_sessions.py`, `test_routes_chat.py`

**Interfaces:**
- Consumes: Tasks 3 and 5.
- Produces: `GET /agents?chatbot_id=…`, `POST /agents` with `chatbot_id` in the body, `GET /sessions?chatbot_id=…`, `POST /sessions` with `chatbot_id`; `POST /chat` unchanged in shape.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/unit/test_routes_chat.py

def test_the_roster_comes_from_the_chats_chatbot(monkeypatch):
    """Read from the chat row, never the request: otherwise a client could ask
    one chatbot's question against another's roster."""
    seen = {}

    def record(self, query, roster, history=None):
        seen["roster"] = [a["id"] for a in roster]
        return Decision(None)

    monkeypatch.setattr(chat_route.OrchestratorService, "route", record)
    monkeypatch.setattr(chat_route, "ChatService", FakeChatService)
    svc = FakeSessionService()
    svc.row = dict(svc.row, chatbot_id="cb-1")

    class ScopedAgents:
        def list(self, chatbot_id):
            seen["asked_for"] = chatbot_id
            return [{"id": "ag-1", "name": "A", "powabase_agent_id": "pa-1",
                     "kb_id": "kb-1", "kb_full_id": None, "model": "gpt-4o-mini",
                     "use_general_kb": False}]

    TestClient(build_app(svc, ScopedAgents())).post(
        "/chat", json={"session_id": "s1", "query": "hi", "chatbot_id": "cb-OTHER"}
    )

    assert seen["asked_for"] == "cb-1"
    assert seen["roster"] == ["ag-1"]
```

```python
# append to backend/tests/unit/test_routes_agents.py

def test_listing_agents_requires_a_chatbot_you_own():
    svc = FakeAgentService()
    app = build_app(svc, chatbots=FakeChatbots(owned=False))
    assert TestClient(app).get("/agents?chatbot_id=cb-1").status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_chat.py tests/unit/test_routes_agents.py -q -k "chatbot"`
Expected: FAIL

- [ ] **Step 3: Implement**

In `chat.py`, take the chatbot from the row:

```python
    # Every chat belongs to a chatbot; the roster is that chatbot's agents.
    # Read from the row rather than the request so a client cannot ask one
    # chatbot's question against another's roster.
    roster = roster_for(
        agents.list(row.get("chatbot_id")), row.get("excluded_agent_ids")
    )
```

In `agents.py`, `list_agents` and `create_agent` take `chatbot_id` and check ownership first:

```python
@router.get("", response_model=list)
async def list_agents(
    chatbot_id: str,
    user: dict = Depends(get_current_user),
    agents: AgentService = Depends(get_agent_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    if await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        rows = await run_in_threadpool(agents.list, chatbot_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    ...
```

`AgentCreateRequest` gains `chatbot_id: str`, checked the same way before
`agents.create(req.chatbot_id, user["id"], ...)`.

Apply the identical pattern in `sessions.py` for `GET /sessions` and
`POST /sessions`, and in the `PATCH /sessions/{id}` handler use
`agents.list(row["chatbot_id"])` when sanitising exclusions.

- [ ] **Step 4: Run the whole suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS. Where an existing test app lacks the chatbot dependency, add
`app.dependency_overrides[get_chatbot_service] = lambda: SimpleNamespace(get_owned=lambda cid, oid: {"id": cid, "owner_id": oid})`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/ backend/tests/unit/
git commit -m "feat: scope agent and chat routes to a chatbot"
```

---

### Task 8: The chatbot picker

**Files:**
- Create: `frontend/chatbots.js`
- Modify: `frontend/index.html`, `frontend/app.js`, `frontend/agents.js`, `frontend/styles.css`

**Interfaces:**
- Consumes: Task 6's routes.
- Produces: `wireChatbots()`, `loadChatbots()`, and a module-level `currentChatbotId` other files read.

- [ ] **Step 1: Add the markup**

In `index.html`, above the chat list in the sidebar:

```html
        <label class="chatbot-picker">
          <select id="chatbot-select"></select>
        </label>
        <button type="button" id="new-chatbot" class="new-session">+ New chatbot</button>
```

- [ ] **Step 2: Write the module**

```javascript
// frontend/chatbots.js
// A chatbot groups agents and the chats that use them. Everything the sidebar
// shows below the picker belongs to the selected one.

const CHATBOT_KEY = "rag-chat-chatbot";
let chatbots = [];
let currentChatbotId = null;

const chatbotSelect = document.getElementById("chatbot-select");

function wireChatbots() {
  chatbotSelect.addEventListener("change", async () => {
    currentChatbotId = chatbotSelect.value;
    localStorage.setItem(CHATBOT_KEY, currentChatbotId);
    currentSessionId = null;
    clearThread("Pick or create a chat to start.");
    await loadAgents();
    await loadSessions();
  });
  document.getElementById("new-chatbot").addEventListener("click", createChatbot);
}

async function loadChatbots() {
  const res = await authFetch("/chatbots");
  if (!res.ok) return;
  chatbots = await res.json();
  const remembered = localStorage.getItem(CHATBOT_KEY);
  const exists = chatbots.some((c) => c.id === remembered);
  currentChatbotId = exists ? remembered : (chatbots[0] && chatbots[0].id) || null;
  renderChatbotSelect();
}

function renderChatbotSelect() {
  chatbotSelect.innerHTML = "";
  chatbots.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    chatbotSelect.appendChild(opt);
  });
  if (currentChatbotId) chatbotSelect.value = currentChatbotId;
}

async function createChatbot() {
  const name = prompt("Name this chatbot");
  if (!name) return;
  const res = await authFetch("/chatbots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) return;
  const created = await res.json();
  await loadChatbots();
  currentChatbotId = created.id;
  localStorage.setItem(CHATBOT_KEY, currentChatbotId);
  renderChatbotSelect();
  await loadAgents();
  await loadSessions();
}
```

- [ ] **Step 3: Thread the id through the existing calls**

In `app.js`: call `wireChatbots()` in `init()`, and `await loadChatbots()` at the
start of `enterApp()` before `loadAgents()` / `loadSessions()`. Change
`loadSessions` to fetch `/sessions?chatbot_id=${encodeURIComponent(currentChatbotId)}`
and `ensureSession` to POST `{ chatbot_id: currentChatbotId }`.

In `agents.js`: `loadAgents` fetches
`/agents?chatbot_id=${encodeURIComponent(currentChatbotId)}`, and
`saveAgentRequest` adds `chatbot_id: currentChatbotId` to the create payload.

Add `<script src="/chatbots.js"></script>` before `app.js` in `index.html`.

- [ ] **Step 4: Verify in a browser**

Run the server on a spare port, then:

```bash
cd backend && .venv/bin/python -m pytest -q && node --test ../frontend/*.test.js
```

Then by hand: log in, confirm the picker shows "My chatbot" with every existing
agent and chat inside it; create a second chatbot and confirm its chat lists no
agents from the first; ask the eyewash question in the original chatbot and
confirm Chem Tutor still answers.

- [ ] **Step 5: Commit**

```bash
git add frontend/ backend/
git commit -m "feat: chatbot picker in the sidebar"
```

---

### Task 9: Keep admin user deletion working

**Files:**
- Modify: `backend/app/clients/powabase_client.py`
- Modify: `backend/app/services/admin_users.py`
- Test: `backend/tests/unit/test_admin_users.py`

**Interfaces:**
- Consumes: Tasks 2 and 5.
- Produces: `list_sessions_by_owner(owner_id) -> list`, `list_agent_rows_by_owner(owner_id) -> list`.

**Why this task exists:** `delete_user` enumerates a user's chats and agents
with `client.list_sessions(user_id)` and `agent_service.list(user_id)`. Task 2
changed both to take a CHATBOT id, so a user id would now match nothing and
deleting an account would silently orphan every agent and chat — leaving their
knowledge bases and Powabase agents alive forever. That exact bug is recorded
in the function's own docstring as having been fixed once before.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/unit/test_admin_users.py

def test_deleting_a_user_still_removes_agents_in_every_chatbot():
    """Enumeration is by OWNER, not by chatbot. Listing by chatbot would find
    nothing for a user id and strand everything they own."""
    client = FakeClient(users=[{"id": "u1"}])
    client.sessions_by_owner = {"u1": [{"id": "s-1"}, {"id": "s-2"}]}
    client.agents_by_owner = {"u1": [{"id": "ag-1"}, {"id": "ag-2"}]}
    sessions, agents = FakeSessionService(), FakeAgentService()

    assert delete_user(client, sessions, agents, "u1") is True

    assert sessions.deleted == ["s-1", "s-2"]
    assert agents.deleted == ["ag-1", "ag-2"]
```

Extend the file's `FakeClient` with:

```python
    sessions_by_owner = {}
    agents_by_owner = {}

    def list_sessions_by_owner(self, owner_id):
        return list(type(self).sessions_by_owner.get(owner_id, []))

    def list_agent_rows_by_owner(self, owner_id):
        return list(type(self).agents_by_owner.get(owner_id, []))

    def list_chatbot_rows(self, owner_id):
        return []

    def delete_chatbot_row(self, chatbot_id):
        pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_admin_users.py -q -k chatbot`
Expected: FAIL — `AttributeError: 'FakeClient' object has no attribute 'list_sessions_by_owner'` once the implementation is wired, or an empty `deleted` list before it.

- [ ] **Step 3: Add owner-scoped listings to the client**

```python
    def list_sessions_by_owner(self, owner_id: str) -> list:
        """Every chat a user owns, across all their chatbots.

        list_sessions is scoped by chatbot; account deletion needs the whole
        set or it strands rows in chatbots it never looked at.
        """
        response = self._client.get(
            "/rest/v1/sessions", params={"owner_id": f"eq.{owner_id}"}
        )
        self._raise_for_status(response)
        return response.json()

    def list_agent_rows_by_owner(self, owner_id: str) -> list:
        """Every agent a user owns, across all their chatbots."""
        response = self._client.get(
            "/rest/v1/agents", params={"owner_id": f"eq.{owner_id}"}
        )
        self._raise_for_status(response)
        return response.json()
```

- [ ] **Step 4: Use them, and remove the now-empty chatbots**

```python
# in backend/app/services/admin_users.py, inside delete_user

    for session in client.list_sessions_by_owner(user_id):
        try:
            session_service.delete(session["id"])   # scratch sources + row
        except PowabaseAPIError:
            pass

    for agent in client.list_agent_rows_by_owner(user_id):
        try:
            agent_service.delete(agent["id"])       # permanent KBs + remote agent + row
        except PowabaseAPIError:
            pass

    # Their chatbots are empty now; remove them so no rows outlive the account.
    for chatbot in client.list_chatbot_rows(user_id):
        try:
            client.delete_chatbot_row(chatbot["id"])
        except PowabaseAPIError:
            pass
```

- [ ] **Step 5: Run the whole suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/clients/powabase_client.py backend/app/services/admin_users.py backend/tests/unit/test_admin_users.py
git commit -m "fix: account deletion must enumerate by owner, not by chatbot"
```

---

## Deployment

1. Apply `011_chatbots.sql` in Powabase Studio.
2. Run `python -m scripts.verify_chatbot_backfill` — must print `PASS`, zero orphans, and per-user counts matching 11/18, 5/4, 0/3.
3. Only then `git pull && sudo systemctl restart ragchat` on the box. Do **not** restart `cloudflared`.
4. Re-check: the picker lists "My chatbot"; the eyewash question still reaches Chem Tutor.
5. **After the restart, RE-RUN the two `update` statements at the bottom of `011_chatbots.sql`.** The old release stays live between steps 1 and 3, and any agent/session it creates in that window is stamped by neither backfill (it didn't exist yet when the migration ran) — it's left with a null `chatbot_id` forever otherwise, invisible to `GET /sessions` and a 400 from PostgREST on `POST /chat`. Then re-run `python -m scripts.verify_chatbot_backfill` again to confirm zero orphans remain.
