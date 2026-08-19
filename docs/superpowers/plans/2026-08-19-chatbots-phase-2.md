# Chatbots Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move knowledge from the user down to the chatbot, and delete the deployment-wide general knowledge base.

**Architecture:** `chatbots` gains the same two knowledge-base columns `users` has today (`kb_id` chunked, `kb_full_id` whole-document), and a migration copies each user's personal pointers onto their oldest chatbot. Every agent in a chatbot reads that chatbot's knowledge unconditionally — there is no per-agent opt-in, because the tier that had one is being deleted. A chat's uploads stay temporary and gain an explicit promote into chatbot knowledge.

**Tech Stack:** FastAPI, Pydantic v2, pytest, vanilla ES modules (no build step), Powabase REST via `httpx`, node:test for frontend.

## Global Constraints

- Ownership failures return **404, never 403** — a foreign id must be indistinguishable from a missing one.
- A route resolves a chatbot from the **session row or an explicit owned-and-checked `chatbot_id`**, never from unchecked request data.
- New DB columns are added; **no column is dropped** in this change. `users.kb_id`, `users.kb_full_id` and `agents.use_general_kb` stay in the schema and stop being read.
- The live `general-knowledge-kb` in Powabase is **never deleted by code**.
- Every Powabase resource cleanup is **best-effort** (`except PowabaseAPIError: pass`) so a stale resource cannot block a delete; the row deletion is authoritative.
- Never delete a Powabase **Source**; only unlink it from a KB. `upload_source` deduplicates identical content, so one Source is shared across users and KBs.
- Tests are `pytest` under `backend/tests/unit/`, using hand-written fake clients with **instance** attributes (never `type(self)` class state).
- Backend test command: `cd backend && python -m pytest -q`. Frontend: `node --test frontend/*.test.js`.
- Baseline before this plan starts: **444 Python tests + 16 JS tests passing.**

---

## File Structure

**Created**
- `backend/migrations/012_chatbot_knowledge.sql` — add columns, copy pointers
- `backend/migrations/013_chatbot_id_not_null.sql` — phase 1 tightening, applied last
- `backend/scripts/verify_chatbot_kb.py` — proves no two chatbots share a KB
- `backend/app/services/chatbot_kb.py` — `ChatbotKbService` (replaces `user_kb.py`)
- `backend/tests/unit/test_chatbot_kb.py` — replaces `test_user_kb.py`

**Deleted**
- `backend/app/services/general_kb.py`, `backend/tests/unit/test_general_kb.py`
- `backend/app/services/user_kb.py`, `backend/tests/unit/test_user_kb.py`

**Modified** — `retrieval_scope.py`, `session_service.py`, `chatbot_service.py`, `agent_service.py`, `main.py`, `schemas.py`, routes `chat.py` `knowledge.py` `agents.py` `admin.py` `sessions.py`, frontend `knowledge.js` `chatbots.js` `agents.js` `app.js` `index.html` `styles.css`.

---

### Task 1: Migration 012 and its verifier

**Files:**
- Create: `backend/migrations/012_chatbot_knowledge.sql`
- Create: `backend/scripts/verify_chatbot_kb.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `chatbots.kb_id` and `chatbots.kb_full_id`, both `uuid` and nullable. Later tasks read and write them through `ChatbotKbService`.

No application code changes. The running release ignores both columns, exactly as phase 1's did.

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/012_chatbot_knowledge.sql
-- Run once in the Powabase Studio SQL Editor (or via the Database URL).
--
-- Moves knowledge down a level: a chatbot owns its knowledge base, where the
-- user used to. Same two tiers agents and users already have — a short
-- document is indexed whole, a long one is chunked.
--
-- users.kb_id and users.kb_full_id are deliberately LEFT IN PLACE. Dropping a
-- column in the same migration that stops reading it means a rollback loses
-- data. Drop them by hand once this release has been live long enough to
-- trust.

alter table public.chatbots add column if not exists kb_id      uuid;
alter table public.chatbots add column if not exists kb_full_id uuid;

-- Copy each user's personal knowledge onto their OLDEST chatbot.
--
-- The subselect is the whole safety argument. "Every user has exactly one
-- chatbot" is true today and stops being true the moment anyone creates a
-- second one. Without it, a user with two chatbots gets the same kb_id written
-- to both — two chatbots reading one knowledge base, which is precisely the
-- leak this migration exists to prevent.
--
-- The `is null` guards make this idempotent: re-running never re-stamps a
-- chatbot whose knowledge has since diverged.
update public.chatbots c
   set kb_id = u.kb_id, kb_full_id = u.kb_full_id
  from public.users u
 where u.id = c.owner_id
   and c.kb_id is null and c.kb_full_id is null
   and c.id = (select c2.id from public.chatbots c2
                where c2.owner_id = u.id
                order by c2.created_at asc
                limit 1);
```

- [ ] **Step 2: Record the pre-migration baseline**

Before applying, run this in Studio and paste the result into the verifier's
`EXPECTED` dict in Step 3. After the migration there is nothing left to compare
against, so this is the only chance to capture it.

```sql
select u.username,
       (u.kb_id is not null) as had_chunked,
       (u.kb_full_id is not null) as had_full
  from public.users u
 order by u.username;
```

- [ ] **Step 3: Write the verifier**

```python
"""Verify chatbot knowledge after migration 012.

The failure this must catch is two chatbots sharing one knowledge base — the
exact leak the migration's oldest-chatbot subselect exists to prevent.

    python -m scripts.verify_chatbot_kb
"""
import os
import sys

import httpx

# Paste the Step 2 result here: username -> (had_chunked, had_full).
EXPECTED = {
    "oscarzheng": (True, True),
    "oscar":      (True, False),
    "zheng":      (False, False),
}

BASE = os.environ["POWABASE_BASE_URL"].rstrip("/")
KEY = os.environ["POWABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def rows(path, **params):
    r = httpx.get(BASE + path, params=params, headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


users = {u["id"]: u for u in rows("/rest/v1/users", select="id,username,kb_id,kb_full_id")}
bots = rows("/rest/v1/chatbots", select="id,owner_id,name,kb_id,kb_full_id,created_at")

ok = True

# Check 1: no two chatbots share a knowledge base. THE critical check.
seen = {}
for b in bots:
    for kb in (b.get("kb_id"), b.get("kb_full_id")):
        if not kb:
            continue
        if kb in seen:
            print("SHARED KB %s: %s and %s" % (kb, seen[kb], b["id"]))
            ok = False
        seen[kb] = b["id"]
print("chatbots sharing a knowledge base :", 0 if ok else "SEE ABOVE")

# Check 2: every user who had knowledge has exactly one chatbot carrying it.
for uid, u in users.items():
    mine = [b for b in bots if b["owner_id"] == uid]
    for column in ("kb_id", "kb_full_id"):
        carriers = [b for b in mine if b.get(column) == u.get(column) and u.get(column)]
        want = 1 if u.get(column) else 0
        if len(carriers) != want:
            print("%s %s: %d chatbots carry it, want %d"
                  % (u["username"], column, len(carriers), want))
            ok = False

# Check 3: the baseline recorded before the migration still describes reality.
for username, (had_chunked, had_full) in EXPECTED.items():
    u = next((x for x in users.values() if x["username"] == username), None)
    if u is None:
        print("user %s vanished" % username)
        ok = False
        continue
    if bool(u.get("kb_id")) != had_chunked or bool(u.get("kb_full_id")) != had_full:
        print("%s: personal pointers changed, migration should not touch users"
              % username)
        ok = False

print("VERDICT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
```

- [ ] **Step 4: Byte-compile the verifier**

Run: `cd backend && python -m py_compile scripts/verify_chatbot_kb.py`
Expected: no output, exit 0. It is not unit-tested — it talks to the live
project, like `verify_chatbot_backfill.py` beside it.

- [ ] **Step 5: Run the full suite to confirm nothing moved**

Run: `cd backend && python -m pytest -q`
Expected: 444 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/012_chatbot_knowledge.sql backend/scripts/verify_chatbot_kb.py
git commit -m "feat: migration 012 — chatbot knowledge columns and verifier"
```

---

### Task 2: Delete the general knowledge base

**Files:**
- Delete: `backend/app/services/general_kb.py`, `backend/tests/unit/test_general_kb.py`
- Modify: `backend/app/main.py`, `backend/app/services/retrieval_scope.py`, `backend/app/services/agent_service.py`, `backend/app/models/schemas.py`, `backend/app/api/routes/chat.py`, `backend/app/api/routes/agents.py`, `backend/app/api/routes/admin.py`
- Modify: `frontend/agents.js`, `frontend/index.html`
- Test: `backend/tests/unit/test_retrieval_scope.py`, `backend/tests/unit/test_routes_agents.py`, `backend/tests/unit/test_routes_admin.py`, `backend/tests/unit/test_main_lifespan.py`, `backend/tests/unit/test_agent_service.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `kb_ids_for(agent_row, session_row, scratch_kb_id=None)` — the
  `general_kb_id` parameter is gone. `AgentService.create` loses its
  `use_general_kb` positional parameter, becoming
  `create(chatbot_id, owner_id, name, instructions, description, model, grounding, max_context_tokens=None)`.

This is wide and shallow. Do it first: everything after is simpler against a
smaller surface.

`agents.use_general_kb` stays in the database (Global Constraints) — only the
code that reads and writes it goes.

- [ ] **Step 1: Rewrite the retrieval-scope tests**

Replace the whole of `backend/tests/unit/test_retrieval_scope.py`:

```python
from app.services.retrieval_scope import kb_ids_for

AGENT = {"kb_id": "ag-chunk", "kb_full_id": "ag-full"}


def test_agent_permanent_kbs_are_always_in_scope():
    assert kb_ids_for(AGENT, None) == ["ag-chunk", "ag-full"]


def test_legacy_per_chat_kb_is_added_when_present():
    assert kb_ids_for(AGENT, {"kb_id": "sc"}) == ["ag-chunk", "ag-full", "sc"]


def test_no_general_kb_entry_is_ever_emitted():
    # The shared general KB is gone. An agent row left over from before, still
    # carrying the disused flag, must not resurrect it.
    stale = dict(AGENT, use_general_kb=True)
    assert kb_ids_for(stale, None) == ["ag-chunk", "ag-full"]


def test_general_assistant_sees_no_specialist_kbs():
    # agent_row=None is the general assistant. Leaking a specialist's documents
    # into an answer the UI attributes to someone else is the one thing this
    # must never do.
    assert kb_ids_for(None, {"kb_id": "sc"}) == ["sc"]


def test_untrained_agent_with_no_uploads_has_empty_scope():
    # Correct behaviour, not a failure state: the agent answers from the model.
    bare = {"kb_id": None, "kb_full_id": None}
    assert kb_ids_for(bare, {"kb_id": None}) == []


def test_scratch_documents_are_restricted_to_this_chats_source_ids():
    session = {"kb_id": None, "source_ids": ["s1", "s2"]}
    assert kb_ids_for(AGENT, session, scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", {"id": "scratch", "source_ids": ["s1", "s2"]},
    ]


def test_chat_with_no_uploads_contributes_no_scratch_entry():
    # Emitting the shared scratch KB bare would make EVERY other chat's uploads
    # answerable here. Drop it, never widen it.
    session = {"kb_id": None, "source_ids": []}
    assert kb_ids_for(AGENT, session, scratch_kb_id="scratch") == ["ag-chunk", "ag-full"]


def test_no_duplicates_when_ids_repeat():
    same = {"kb_id": "x", "kb_full_id": "x"}
    assert kb_ids_for(same, {"kb_id": "x"}) == ["x"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: FAIL — `kb_ids_for` still requires the third positional
`general_kb_id`, so calls with two arguments raise `TypeError`.

- [ ] **Step 3: Rewrite `retrieval_scope.py`**

Replace the file:

```python
from __future__ import annotations


def kb_ids_for(agent_row, session_row, scratch_kb_id=None) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    Entries are either a bare KB id (search all of it) or a dict
    ``{"id", "source_ids"}`` (search only those documents). The dict form is
    how ONE shared scratch KB serves every chat: a chat sees its own uploads
    and no others, because retrieval is restricted to the source ids recorded
    on that chat's row.

    With a specialist: its permanent KBs, then this chat's scratch documents.

    With ``agent_row=None`` the general assistant is answering: it sees this
    chat's scratch documents and never a specialist's permanent KBs — that
    would leak one agent's documents into an answer the UI attributes to
    another.

    A chat that has uploaded nothing contributes NO scratch entry. Emitting
    the shared KB without source_ids would make every other chat's uploads
    answerable here, which is the one failure this design must not have.

    ``session_row.kb_id`` is the legacy per-chat KB. Chats created before the
    shared KB keep theirs and are searched as a bare id; both forms coexist so
    live user data never needed migrating.

    Falsy ids are dropped, so an untrained agent with no uploads yields [] and
    answers from the model, which is correct rather than a failure.

    ``session_row`` and ``scratch_kb_id`` may be None.
    """
    ids: list = []
    if agent_row:
        ids.extend([agent_row.get("kb_id"), agent_row.get("kb_full_id")])
    if session_row:
        ids.append(session_row.get("kb_id"))          # legacy per-chat KB
        source_ids = session_row.get("source_ids") or []
        if scratch_kb_id and source_ids:
            ids.append({"id": scratch_kb_id, "source_ids": list(source_ids)})

    out: list = []
    for entry in ids:
        if not entry:
            continue
        if isinstance(entry, dict):
            out.append(entry)
        elif entry not in out:
            out.append(entry)
    return out
```

Chatbot knowledge is added to this function in Task 5, not here.

- [ ] **Step 4: Run the retrieval-scope tests**

Run: `cd backend && python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: PASS.

- [ ] **Step 5: Remove the general KB everywhere else**

Delete `backend/app/services/general_kb.py` and
`backend/tests/unit/test_general_kb.py`.

In `backend/app/main.py`: remove the `ensure_general_kb` import, the
`general_kb_id = ensure_general_kb(client, reranker_config)` line, and
`app.state.general_kb_id = general_kb_id`.

In `backend/app/api/routes/chat.py`: remove the `get_general_kb_id` import and
the `general_kb_id: str = Depends(get_general_kb_id),` parameter. Change the
call at line 87 to:

```python
        kb_ids_for(agent_row, row, scratch_kb_id),
```

In `backend/app/api/routes/admin.py`: delete the entire `admin_train` route
function and the `get_general_kb_id` import. Leave every user-management route
untouched. If `IngestService`, `IngestResponse`, `AttentionRequiredError` or
`UploadFile`/`File` become unused imports there, remove them too.

In `backend/app/services/agent_service.py`: remove the `use_general_kb: bool,`
parameter from `create` and the `"use_general_kb": use_general_kb,` entry from
the inserted row.

In `backend/app/models/schemas.py`: remove `use_general_kb` from
`AgentCreateRequest`, `AgentUpdateRequest` and `AgentResponse`.

In `backend/app/api/routes/agents.py`: remove `use_general_kb=bool(row.get("use_general_kb")),`
from the response builder, and `req.use_general_kb,` from the `agents.create`
call.

In `frontend/agents.js`: remove the `agentGeneralKbInput` constant, the
`agentGeneralKbInput.checked = a.use_general_kb;` line, and the
`use_general_kb: agentGeneralKbInput.checked,` entry.

In `frontend/index.html`: remove the label containing
`<input type="checkbox" id="agent-general-kb" />` and the admin page's training
form.

- [ ] **Step 6: Update every test that referenced the removed surface**

Run: `cd backend && python -m pytest -q` and fix each failure by deleting the
`use_general_kb` argument, key or assertion. The files that will fail are
`test_routes_agents.py`, `test_routes_admin.py`, `test_agent_service.py`,
`test_main_lifespan.py`, `test_routes_chat.py`.

Do not add new assertions here beyond deleting what no longer exists — except
one, in `test_routes_admin.py`:

```python
def test_admin_train_route_is_gone(client):
    # The shared general KB was the only cross-user knowledge. Removing the
    # route is what actually removes the feature; leaving it would keep an
    # authenticated path to a KB nothing reads.
    assert client.post("/admin/train", data={"password": "x"}).status_code == 404
```

- [ ] **Step 7: Run both suites**

Run: `cd backend && python -m pytest -q` — expected: all pass.
Run: `node --test frontend/*.test.js` — expected: 16 pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: remove the deployment-wide general knowledge base"
```

---

### Task 3: ChatbotKbService

**Files:**
- Create: `backend/app/services/chatbot_kb.py`
- Delete: `backend/app/services/user_kb.py`
- Create: `backend/tests/unit/test_chatbot_kb.py`
- Delete: `backend/tests/unit/test_user_kb.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `chatbots.kb_id` / `chatbots.kb_full_id` from Task 1.
- Produces: `ChatbotKbService(client, reranker_config=None)` with
  `kb_ids(chatbot_row) -> list`, `ensure_kb(chatbot_row, full_document=False) -> str`,
  `documents(chatbot_row) -> list`, `untrain(chatbot_row, source_id) -> bool`;
  and `get_chatbot_kb_service(request)` returning
  `request.app.state.chatbot_kb_service`.

This is `user_kb.py` rekeyed from a user row to a chatbot row. The client call
inside `ensure_kb` changes from `update_user` to `update_chatbot_row`, which
already exists.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_chatbot_kb.py`:

```python
import pytest

from app.clients.powabase_client import PowabaseAPIError
from app.services.chatbot_kb import ChatbotKbService


class FakeClient:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.created_kbs = []
        self.updated_chatbots = []
        self.kb_items = {}
        self.removed = []

    def get_chatbot_row(self, chatbot_id):
        return self.rows.get(chatbot_id)

    def update_chatbot_row(self, chatbot_id, fields):
        self.updated_chatbots.append((chatbot_id, fields))
        self.rows[chatbot_id].update(fields)

    def create_knowledge_base(self, name, description="", indexing_config=None,
                              retrieval_config=None):
        kb = {"id": "kb-%d" % (len(self.created_kbs) + 1), "name": name,
              "indexing_config": indexing_config,
              "retrieval_config": retrieval_config}
        self.created_kbs.append(kb)
        return kb

    def list_kb_sources(self, kb_id):
        return {"items": self.kb_items.get(kb_id, [])}

    def remove_source_from_kb(self, kb_id, indexed_source_id):
        self.removed.append((kb_id, indexed_source_id))


def bot(**over):
    return dict({"id": "cb-1", "kb_id": None, "kb_full_id": None}, **over)


def test_untrained_chatbot_has_no_kbs():
    assert ChatbotKbService(FakeClient()).kb_ids(bot()) == []


def test_kb_ids_are_chunked_then_full():
    row = bot(kb_id="chunk", kb_full_id="full")
    assert ChatbotKbService(FakeClient()).kb_ids(row) == ["chunk", "full"]


def test_kb_ids_of_a_missing_chatbot_is_empty():
    assert ChatbotKbService(FakeClient()).kb_ids(None) == []


def test_ensure_kb_creates_the_chunked_tier_lazily_and_records_it():
    row = bot()
    client = FakeClient([row])
    kb_id = ChatbotKbService(client).ensure_kb(row, full_document=False)
    assert kb_id == "kb-1"
    assert client.updated_chatbots == [("cb-1", {"kb_id": "kb-1"})]
    # The chunked tier takes Powabase's default strategy.
    assert client.created_kbs[0]["indexing_config"] is None


def test_ensure_kb_creates_the_full_document_tier_with_its_strategy():
    row = bot()
    client = FakeClient([row])
    kb_id = ChatbotKbService(client).ensure_kb(row, full_document=True)
    assert client.updated_chatbots == [("cb-1", {"kb_full_id": "kb-1"})]
    assert client.created_kbs[0]["indexing_config"] == {"strategy": "full_document"}


def test_ensure_kb_reuses_an_existing_tier():
    row = bot(kb_id="already")
    client = FakeClient([row])
    assert ChatbotKbService(client).ensure_kb(row) == "already"
    assert client.created_kbs == []


def test_two_chatbots_get_separate_knowledge_bases():
    # The whole point of phase 2. Same owner, different documents.
    a, b = bot(id="cb-a"), bot(id="cb-b")
    client = FakeClient([a, b])
    service = ChatbotKbService(client)
    assert service.ensure_kb(a) != service.ensure_kb(b)


def test_documents_spans_both_tiers():
    row = bot(kb_id="chunk", kb_full_id="full")
    client = FakeClient([row])
    client.kb_items["chunk"] = [
        {"id": "i1", "source_id": "s1", "source_name": "a.pdf", "index_status": "indexed"}
    ]
    client.kb_items["full"] = [
        {"id": "i2", "source_id": "s2", "source_name": "b.pdf", "index_status": "indexed"}
    ]
    docs = ChatbotKbService(client).documents(row)
    assert [d["source_id"] for d in docs] == ["s1", "s2"]
    assert docs[0]["filename"] == "a.pdf"


def test_untrain_unlinks_by_indexed_id_and_never_deletes_the_source():
    # upload_source deduplicates identical content, so the same Source may
    # belong to another chatbot or another user. Only the LINK may go.
    row = bot(kb_id="chunk")
    client = FakeClient([row])
    client.kb_items["chunk"] = [{"id": "i1", "source_id": "s1"}]
    assert ChatbotKbService(client).untrain(row, "s1") is True
    assert client.removed == [("chunk", "i1")]


def test_untrain_reports_a_document_it_does_not_hold():
    row = bot(kb_id="chunk")
    client = FakeClient([row])
    client.kb_items["chunk"] = []
    assert ChatbotKbService(client).untrain(row, "nope") is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_chatbot_kb.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.chatbot_kb'`.

- [ ] **Step 3: Write the service**

Create `backend/app/services/chatbot_kb.py`:

```python
from __future__ import annotations

from fastapi import Request


class ChatbotKbService:
    """A chatbot's knowledge base: trained once, searched by every agent in it.

    Deliberately distinct from an AGENT's permanent tier, which belongs to one
    agent and which the general assistant is blocked from so one agent's
    documents cannot surface in an answer attributed to another.

    This one belongs to the chatbot, so every agent inside reads it — the
    general assistant included. There is no per-agent opt-in: the chatbot is
    already the boundary, and a toggle would be a second place to look when an
    answer comes back thin.

    Two tiers for the same reason agents have two: a short document is indexed
    whole, a long one is chunked. Both are created lazily, so a chatbot that is
    never trained costs no knowledge base.
    """

    def __init__(self, client, reranker_config: dict | None = None):
        self.client = client
        self.reranker_config = reranker_config

    def kb_ids(self, chatbot_row) -> list:
        """This chatbot's knowledge bases, in retrieval order. Empty if untrained."""
        if not chatbot_row:
            return []
        return [kb for kb in (chatbot_row.get("kb_id"),
                              chatbot_row.get("kb_full_id")) if kb]

    def ensure_kb(self, chatbot_row: dict, full_document: bool = False) -> str:
        """Return the tier that holds this document class, creating it lazily."""
        column = "kb_full_id" if full_document else "kb_id"
        existing = chatbot_row.get(column)
        if existing:
            return existing
        chatbot_id = chatbot_row["id"]
        if full_document:
            name = f"chatbot-{chatbot_id}-knowledge-full"
            indexing_config = {"strategy": "full_document"}
        else:
            name = f"chatbot-{chatbot_id}-knowledge"
            indexing_config = None
        kb = self.client.create_knowledge_base(
            name,
            description=f"Knowledge for chatbot {chatbot_id}",
            indexing_config=indexing_config,
            retrieval_config=self.reranker_config,
        )
        self.client.update_chatbot_row(chatbot_id, {column: kb["id"]})
        return kb["id"]

    def documents(self, chatbot_row: dict) -> list:
        """Every document across both tiers, newest first where available."""
        out = []
        for kb_id in self.kb_ids(chatbot_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                out.append({
                    "source_id": item.get("source_id"),
                    # Powabase names these source_name / index_status.
                    "filename": item.get("source_name") or item.get("source_id"),
                    "status": item.get("index_status"),
                })
        return out

    def untrain(self, chatbot_row: dict, source_id: str) -> bool:
        """Unlink one document from whichever tier holds it.

        Never deletes the Source itself: upload_source deduplicates identical
        content, so the same source may belong to an agent, another chatbot, or
        another user. Promotion from a chat makes multi-KB sources routine, so
        this matters more than it used to, not less.
        """
        for kb_id in self.kb_ids(chatbot_row):
            for item in self.client.list_kb_sources(kb_id).get("items", []):
                if item.get("source_id") == source_id:
                    self.client.remove_source_from_kb(kb_id, item["id"])
                    return True
        return False


def get_chatbot_kb_service(request: Request) -> "ChatbotKbService":
    """FastAPI dependency returning the shared ChatbotKbService."""
    return request.app.state.chatbot_kb_service
```

- [ ] **Step 4: Run the new tests**

Run: `cd backend && python -m pytest tests/unit/test_chatbot_kb.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Wire it and remove the old service**

In `backend/app/main.py`, replace the `UserKbService` import with
`from app.services.chatbot_kb import ChatbotKbService` and the assignment with:

```python
        app.state.chatbot_kb_service = ChatbotKbService(client, reranker_config)
```

Delete `backend/app/services/user_kb.py` and `backend/tests/unit/test_user_kb.py`.

`knowledge.py` and `chat.py` still import `user_kb` — Tasks 4 and 5 fix them.
Until then the app will not import, which is expected between commits within
this task, not across them. Complete Step 6 before committing.

- [ ] **Step 6: Point the two importers at the new service**

In `backend/app/api/routes/knowledge.py` and `backend/app/api/routes/chat.py`,
replace `from app.services.user_kb import UserKbService, get_user_kb_service`
with `from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service`,
and rename the dependency parameters from
`user_kb: UserKbService = Depends(get_user_kb_service)` to
`chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service)`.

In `chat.py` the renamed dependency is **unused** until Task 5. Task 2 already
removed the `user_kb_ids=` argument along with the general KB, so nothing calls
it in between. Leaving the dependency in place across the gap is deliberate:
removing and re-adding it would be two pointless diffs.

In `knowledge.py` rename every `user_kb.` call to `chatbot_kb.`, still passing
the user row; Task 4 replaces the row.

- [ ] **Step 7: Run both suites**

Run: `cd backend && python -m pytest -q` — expected: all pass.
Run: `node --test frontend/*.test.js` — expected: 16 pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: ChatbotKbService replaces UserKbService"
```

---

### Task 4: Knowledge routes take a chatbot

**Files:**
- Modify: `backend/app/api/routes/knowledge.py`
- Test: `backend/tests/unit/test_routes_knowledge.py`

**Interfaces:**
- Consumes: `ChatbotKbService` and `get_chatbot_kb_service` from Task 3;
  `ChatbotService.get_owned(chatbot_id, owner_id)` which already exists.
- Produces: all four `/knowledge` endpoints scoped to a chatbot.
  `POST /knowledge/train` takes `chatbot_id` as a **form field** (the request is
  multipart); the other three take it as a **query parameter**.

The ownership check is copied verbatim from `agents.py`, which is the
established pattern:

```python
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
```

404 not 403 (Global Constraints). `get_owned` re-reads the row, so it doubles as
the fresh read the background task's tier creation requires — the existing
`client.get_user` re-read goes away with it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_routes_knowledge.py`, following the fixtures
already in that file:

```python
def test_listing_documents_requires_a_chatbot_id(client, auth):
    assert client.get("/knowledge/documents", headers=auth).status_code == 422


def test_listing_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.get(
        f"/knowledge/documents?chatbot_id={other_chatbot}", headers=auth
    )
    # 404 not 403: a foreign id must be indistinguishable from a missing one.
    assert res.status_code == 404


def test_documents_come_from_the_named_chatbot(client, auth, my_chatbot, fake):
    fake.kb_items["cb-kb"] = [
        {"id": "i1", "source_id": "s1", "source_name": "a.pdf", "index_status": "indexed"}
    ]
    res = client.get(f"/knowledge/documents?chatbot_id={my_chatbot}", headers=auth)
    assert res.status_code == 200
    assert [d["source_id"] for d in res.json()] == ["s1"]


def test_untraining_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.delete(
        f"/knowledge/documents/s1?chatbot_id={other_chatbot}", headers=auth
    )
    assert res.status_code == 404


def test_training_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.post(
        "/knowledge/train",
        data={"chatbot_id": other_chatbot},
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth,
    )
    assert res.status_code == 404
```

Add the fixtures the file does not already have: `my_chatbot` returning a
chatbot id owned by the authenticated user whose row carries `kb_id="cb-kb"`,
and `other_chatbot` returning a chatbot id owned by a different user. Build
them on whatever fake client the file already uses.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_routes_knowledge.py -q`
Expected: FAIL — the routes take no `chatbot_id`, so the ownership tests get
200 or 404-from-missing-document rather than the ownership 404, and the
422 test gets 200.

- [ ] **Step 3: Rewrite the routes**

Replace the module docstring and all four handlers in
`backend/app/api/routes/knowledge.py`. The new docstring:

```python
"""A chatbot's knowledge base.

Trained once, searched by every agent in that chatbot — the general assistant
included. Distinct from /agents/{id}/train, which teaches one agent, and from a
chat's uploads, which are temporary until promoted.
"""
```

Add `Form` and `Query` to the `fastapi` import, and import `ChatbotService` and
`get_chatbot_service` from `app.services.chatbot_service` alongside the existing
imports. Rename `_finish_training`'s parameters from `user_kb, user_row` to
`chatbot_kb, chatbot_row` and its log messages from `"user knowledge %s"` to
`"chatbot knowledge %s"`. Its body is otherwise unchanged.

```python
@router.post("/train", response_model=IngestResponse)
async def train_chatbot_knowledge(
    background_tasks: BackgroundTasks,
    chatbot_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    content = await file.read()
    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(
        _finish_training, service, chatbot_kb, chatbot, source_id,
        settings.full_document_max_chars,
    )
    return JSONResponse(
        status_code=202,
        content=IngestResponse(source_id=source_id, status="processing").model_dump(),
    )


@router.get("/documents/{source_id}/status", response_model=IngestStatusResponse)
async def chatbot_knowledge_status(
    source_id: str,
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    # get_owned re-reads the row, which is also what this needs: the tier is
    # created during the background task, so any earlier copy predates it.
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        status, detail = await run_in_threadpool(
            source_status, client, source_id, chatbot_kb.kb_ids(chatbot)
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return IngestStatusResponse(source_id=source_id, status=status, detail=detail)


@router.get("/documents", response_model=list)
async def list_chatbot_knowledge(
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        return await run_in_threadpool(chatbot_kb.documents, chatbot)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/documents/{source_id}", status_code=204)
async def untrain_chatbot_knowledge(
    source_id: str,
    chatbot_id: str = Query(...),
    user: dict = Depends(get_current_user),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    chatbot = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        found = await run_in_threadpool(chatbot_kb.untrain, chatbot, source_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    return None
```

Remove the now-unused `PowabaseClient`/`get_powabase_client` import only if no
handler still uses it — `train` and `status` both do, so it stays.

- [ ] **Step 4: Run the knowledge route tests**

Run: `cd backend && python -m pytest tests/unit/test_routes_knowledge.py -q`
Expected: PASS. Existing tests in the file that call these endpoints without a
`chatbot_id` must be updated to pass one, not deleted.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: knowledge routes are scoped to a chatbot"
```

---

### Task 5: Retrieval reads chatbot knowledge

**Files:**
- Modify: `backend/app/services/retrieval_scope.py`
- Modify: `backend/app/api/routes/chat.py`
- Test: `backend/tests/unit/test_retrieval_scope.py`, `backend/tests/unit/test_routes_chat.py`

**Interfaces:**
- Consumes: `kb_ids_for(agent_row, session_row, scratch_kb_id=None)` from Task 2;
  `ChatbotKbService.kb_ids(chatbot_row)` from Task 3.
- Produces: `kb_ids_for(agent_row, session_row, chatbot_kb_ids=None, scratch_kb_id=None)`.
  `chatbot_kb_ids` is a list of KB ids, third positional.

This is the smallest diff in the plan and the one that decides what every answer
can see. Getting it wrong does not crash anything — it silently widens or
narrows retrieval.

- [ ] **Step 1: Add the failing tests**

Append to `backend/tests/unit/test_retrieval_scope.py`:

```python
def test_chatbot_knowledge_follows_the_agents_own_kbs():
    assert kb_ids_for(AGENT, None, ["cb-chunk", "cb-full"]) == [
        "ag-chunk", "ag-full", "cb-chunk", "cb-full",
    ]


def test_general_assistant_reads_chatbot_knowledge():
    # Unlike a specialist's permanent tier, chatbot knowledge belongs to the
    # container, so the agent with no row of its own still sees it.
    assert kb_ids_for(None, None, ["cb-chunk"]) == ["cb-chunk"]


def test_every_agent_reads_it_with_no_opt_in():
    # There is no per-agent flag. A row carrying the disused one changes
    # nothing in either direction.
    assert kb_ids_for(dict(AGENT, use_general_kb=False), None, ["cb"]) == [
        "ag-chunk", "ag-full", "cb",
    ]


def test_untrained_chatbot_contributes_nothing():
    assert kb_ids_for(AGENT, None, []) == ["ag-chunk", "ag-full"]


def test_full_order_is_agent_then_chatbot_then_legacy_then_scratch():
    session = {"kb_id": "legacy", "source_ids": ["s1"]}
    assert kb_ids_for(AGENT, session, ["cb"], scratch_kb_id="scratch") == [
        "ag-chunk", "ag-full", "cb", "legacy",
        {"id": "scratch", "source_ids": ["s1"]},
    ]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: FAIL — the third positional is currently `scratch_kb_id`, so a list
lands there and no chatbot entry appears.

- [ ] **Step 3: Add the parameter**

In `retrieval_scope.py` change the signature to:

```python
def kb_ids_for(agent_row, session_row, chatbot_kb_ids=None,
               scratch_kb_id=None) -> list:
```

Insert immediately after the `if agent_row:` block:

```python
    # The chatbot's own knowledge, searched by every agent inside it —
    # including the general assistant, because this belongs to the container
    # rather than to any one agent. That is deliberately unlike a specialist's
    # permanent tier above, which the general assistant never sees.
    ids.extend(chatbot_kb_ids or [])
```

Extend the docstring's second paragraph to read: "With a specialist: its
permanent KBs, then the chatbot's knowledge, then this chat's scratch
documents."

- [ ] **Step 4: Run the retrieval-scope tests**

Run: `cd backend && python -m pytest tests/unit/test_retrieval_scope.py -q`
Expected: PASS.

- [ ] **Step 5: Feed the chatbot row in from `chat.py`**

Add `ChatbotService` / `get_chatbot_service` to the imports. Add the dependency
parameter `chatbots: ChatbotService = Depends(get_chatbot_service),`. After the
session lookup and before the roster line, add:

```python
    # The chatbot comes off the CHAT ROW, never the request body — the same
    # rule the roster follows below, for the same reason. A legacy chat with no
    # chatbot_id degrades to no chatbot knowledge rather than raising.
    chatbot_id = row.get("chatbot_id")
    chatbot = chatbots.get_owned(chatbot_id, user["id"]) if chatbot_id else None
```

and change the `kb_ids_for` call to:

```python
        kb_ids_for(agent_row, row, chatbot_kb.kb_ids(chatbot), scratch_kb_id),
```

`get_owned` returns `None` for a chat whose `chatbot_id` is null, and
`kb_ids(None)` returns `[]`, so an unstamped legacy row degrades to no chatbot
knowledge rather than raising.

- [ ] **Step 6: Add the chat-route test**

Append to `backend/tests/unit/test_routes_chat.py`, using that file's existing
fixtures:

```python
def test_chat_retrieves_from_the_chats_own_chatbot(client, auth, fake):
    # Two chatbots, one owner. The chat belongs to A, so B's knowledge must
    # never appear in the scope passed to retrieval.
    fake.chatbots["cb-a"] = {"id": "cb-a", "owner_id": "u1", "kb_id": "kb-a"}
    fake.chatbots["cb-b"] = {"id": "cb-b", "owner_id": "u1", "kb_id": "kb-b"}
    fake.sessions["s1"]["chatbot_id"] = "cb-a"
    client.post("/chat", json={"session_id": "s1", "query": "hi"}, headers=auth)
    assert "kb-a" in fake.last_kb_ids
    assert "kb-b" not in fake.last_kb_ids
```

If the file's fake does not already record the KB ids handed to `ChatService`,
add a `last_kb_ids` instance attribute to it — an instance attribute, never
class state, so tests cannot leak into each other.

- [ ] **Step 7: Run both suites**

Run: `cd backend && python -m pytest -q` — expected: all pass.
Run: `node --test frontend/*.test.js` — expected: 16 pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: answers retrieve from the chat's own chatbot knowledge"
```

---

### Task 6: Promote a chat upload into chatbot knowledge

**Files:**
- Modify: `backend/app/services/session_service.py`
- Modify: `backend/app/api/routes/sessions.py`
- Test: `backend/tests/unit/test_session_service.py`, `backend/tests/unit/test_routes_sessions.py`

**Interfaces:**
- Consumes: `ChatbotKbService.ensure_kb(chatbot_row, full_document)` from Task 3;
  `IngestService.char_count(source_id) -> int` and
  `IngestService.index_into(kb_id, source_id) -> str`, both existing.
- Produces: `SessionService.forget_source(session_id, source_id) -> None` and
  `POST /sessions/{session_id}/documents/{source_id}/promote -> 202`.

Promote is a **move**, not a copy: after it succeeds the source is gone from the
chat's `source_ids`. Left in both places the chat would search the same document
twice, once through scratch and once through chatbot knowledge, for no benefit.
The chat loses nothing, because it reads chatbot knowledge too.

**Promote must NOT unlink the source from the scratch KB.** `upload_source`
deduplicates identical content, so another chat may hold the same source id;
unlinking it there is the exact bug that produced the duplicate-document 502.
Leaving the link is harmless because scratch retrieval is gated on the row's
`source_ids`, which no longer names it.

- [ ] **Step 1: Write the failing service test**

Append to `backend/tests/unit/test_session_service.py`:

```python
def test_forget_source_removes_only_that_source():
    client = FakeClient()
    client.sessions["s1"] = {"id": "s1", "source_ids": ["a", "b", "c"]}
    SessionService(client, None, "scratch").forget_source("s1", "b")
    assert client.sessions["s1"]["source_ids"] == ["a", "c"]


def test_forget_source_is_a_no_op_for_a_source_not_there():
    client = FakeClient()
    client.sessions["s1"] = {"id": "s1", "source_ids": ["a"]}
    SessionService(client, None, "scratch").forget_source("s1", "zzz")
    assert client.sessions["s1"]["source_ids"] == ["a"]


def test_forget_source_re_reads_rather_than_trusting_a_copy():
    # Same reason record_source re-reads: promotion finishes in a background
    # task, so a stale copy would write back a list missing a concurrent upload.
    client = FakeClient()
    client.sessions["s1"] = {"id": "s1", "source_ids": ["a", "b"]}
    service = SessionService(client, None, "scratch")
    client.sessions["s1"]["source_ids"] = ["a", "b", "late"]
    service.forget_source("s1", "a")
    assert client.sessions["s1"]["source_ids"] == ["b", "late"]
```

Match the file's existing `FakeClient` and `SessionService(...)` construction —
copy the arguments from a neighbouring test rather than the ones written above
if they differ.

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_session_service.py -q`
Expected: FAIL — `AttributeError: 'SessionService' object has no attribute 'forget_source'`.

- [ ] **Step 3: Add `forget_source`**

In `session_service.py`, directly after `record_source`:

```python
    def forget_source(self, session_id: str, source_id: str) -> None:
        """Drop one upload from this chat's scratch scope.

        The other half of record_source, used when a document is promoted into
        chatbot knowledge: the chat reads that knowledge too, so keeping the id
        here as well would search the same document twice.

        Re-reads the row for the same reason record_source does — promotion
        finishes in a background task, and a stale copy would write back a list
        missing a concurrent upload.

        Deliberately does NOT unlink the source from the shared scratch KB.
        upload_source deduplicates identical content, so another chat may hold
        the same source id, and unlinking it would break that chat's retrieval.
        An unreferenced link is never searched, because scratch retrieval is
        restricted to the ids named on this row.
        """
        row = self.client.get_session_row(session_id)
        if row is None:
            return
        source_ids = list(row.get("source_ids") or [])
        if source_id not in source_ids:
            return
        source_ids.remove(source_id)
        self.client.update_session(session_id, {"source_ids": source_ids})
```

- [ ] **Step 4: Run the service tests**

Run: `cd backend && python -m pytest tests/unit/test_session_service.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing route tests**

Append to `backend/tests/unit/test_routes_sessions.py`:

```python
def test_promote_moves_the_document_into_chatbot_knowledge(client, auth, fake):
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    res = client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    assert res.status_code == 202
    assert "src-1" not in fake.sessions["s1"]["source_ids"]


def test_promote_rejects_a_source_this_chat_never_uploaded(client, auth, fake):
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    res = client.post("/sessions/s1/documents/other/promote", headers=auth)
    assert res.status_code == 404


def test_promote_on_another_users_chat_is_not_found(client, auth, foreign_session):
    res = client.post(
        f"/sessions/{foreign_session}/documents/src-1/promote", headers=auth
    )
    assert res.status_code == 404


def test_promote_twice_is_not_an_error(client, auth, fake):
    # Powabase deduplicates identical content, so promoting the same file twice
    # is a thing users will do. The second call finds nothing left to move.
    fake.sessions["s1"]["source_ids"] = ["src-1"]
    client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    second = client.post("/sessions/s1/documents/src-1/promote", headers=auth)
    assert second.status_code == 404
```

The fourth test asserts the honest behaviour: once moved, the source is no
longer in the chat, so a repeat promote is a 404 rather than a silent success.
That is the same 404 as any unknown source and needs no special case.

- [ ] **Step 6: Run and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_routes_sessions.py -q`
Expected: FAIL — 404 for every case, because the route does not exist.

- [ ] **Step 7: Add the route**

In `backend/app/api/routes/sessions.py`, importing `BackgroundTasks`,
`JSONResponse`, `run_in_threadpool`, `IngestService`, the four ingest exception
types, `ChatbotKbService`/`get_chatbot_kb_service`,
`ChatbotService`/`get_chatbot_service`, `get_settings`, `PowabaseClient`/
`get_powabase_client` and `logging` as needed:

```python
def _finish_promotion(service, chatbot_kb, chatbot_row, sessions, session_id,
                      source_id, full_document_max_chars) -> None:
    """Index one already-extracted document into chatbot knowledge.

    Backgrounded like every other indexing path: chunking a long document takes
    minutes. The source is only forgotten from the chat AFTER indexing
    succeeds, so a failure leaves the chat exactly as it was rather than
    dropping the document on the floor.
    """
    try:
        full_document = 0 < service.char_count(source_id) <= full_document_max_chars
        kb_id = chatbot_kb.ensure_kb(chatbot_row, full_document)
        service.index_into(kb_id, source_id)
        sessions.forget_source(session_id, source_id)
    # No extraction errors are possible here: the source was already extracted
    # when it was ingested into the chat, so only indexing can fail.
    except IndexingFailedError as e:
        logger.warning("promote %s failed: %s", source_id, e.message)
    except IngestTimeoutError as e:
        logger.warning("promote %s timed out while %s", source_id, e.status)
    except PowabaseAPIError as e:
        logger.warning("promote %s: upstream %s", source_id, e.status_code)


@router.post("/{session_id}/documents/{source_id}/promote", status_code=202)
async def promote_document(
    session_id: str,
    source_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    """Move one of this chat's uploads into its chatbot's knowledge.

    The chatbot is resolved from the SESSION ROW, never from the request — the
    same rule /chat follows for its roster.
    """
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if source_id not in (row.get("source_ids") or []):
        raise HTTPException(status_code=404, detail="Document not found")
    chatbot = await run_in_threadpool(
        chatbots.get_owned, row.get("chatbot_id"), user["id"]
    )
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    background_tasks.add_task(
        _finish_promotion, service, chatbot_kb, chatbot, sessions, session_id,
        source_id, settings.full_document_max_chars,
    )
    return JSONResponse(status_code=202, content={"source_id": source_id,
                                                  "status": "processing"})
```

Add `logger = logging.getLogger(__name__)` at module level if the file has none.
`await_extraction` is deliberately not called: the source was already extracted
when it was ingested into the chat.

- [ ] **Step 8: Run the route tests**

Run: `cd backend && python -m pytest tests/unit/test_routes_sessions.py -q`
Expected: PASS. FastAPI's `TestClient` runs background tasks before returning,
so the move is visible immediately in the first test.

- [ ] **Step 9: Run the full suite**

Run: `cd backend && python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: promote a chat upload into chatbot knowledge"
```

---

### Task 7: Deleting a chatbot deletes its knowledge

**Files:**
- Modify: `backend/app/services/chatbot_service.py`
- Test: `backend/tests/unit/test_chatbot_service.py`

**Interfaces:**
- Consumes: `client.delete_knowledge_base(kb_id)`, existing.
- Produces: no signature change. `ChatbotService.delete(chatbot_id, owner_id, agents, sessions)`
  now also drops the chatbot's two knowledge bases.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_chatbot_service.py`:

```python
def test_delete_removes_both_knowledge_tiers():
    client = FakeClient([
        {"id": "cb-1", "owner_id": "u1", "kb_id": "k1", "kb_full_id": "k2"},
        {"id": "cb-2", "owner_id": "u1"},
    ])
    ChatbotService(client).delete("cb-1", "u1", FakeAgents(), FakeSessions())
    assert set(client.deleted_kbs) == {"k1", "k2"}


def test_delete_survives_a_knowledge_base_already_gone():
    # Best-effort cleanup: a stale resource must never block the delete, and
    # the row deletion is what is authoritative.
    client = FakeClient([
        {"id": "cb-1", "owner_id": "u1", "kb_id": "missing"},
        {"id": "cb-2", "owner_id": "u1"},
    ])
    client.kb_delete_raises = {"missing"}
    assert ChatbotService(client).delete("cb-1", "u1", FakeAgents(), FakeSessions()) is True
    assert "cb-1" in client.deleted_chatbots


def test_delete_of_an_untrained_chatbot_touches_no_knowledge_base():
    client = FakeClient([
        {"id": "cb-1", "owner_id": "u1"},
        {"id": "cb-2", "owner_id": "u1"},
    ])
    ChatbotService(client).delete("cb-1", "u1", FakeAgents(), FakeSessions())
    assert client.deleted_kbs == []
```

Extend that file's `FakeClient` with instance attributes
`self.deleted_kbs = []` and `self.kb_delete_raises = set()`, and a method:

```python
    def delete_knowledge_base(self, kb_id):
        if kb_id in self.kb_delete_raises:
            raise PowabaseAPIError(404, "gone")
        self.deleted_kbs.append(kb_id)
```

Match `PowabaseAPIError`'s real constructor as used elsewhere in the test suite.

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && python -m pytest tests/unit/test_chatbot_service.py -q`
Expected: FAIL — `deleted_kbs` is empty; `delete` does not touch knowledge bases.

- [ ] **Step 3: Delete the knowledge bases**

Add `from app.clients.powabase_client import PowabaseAPIError` to
`chatbot_service.py`. In `delete`, between the session loop and
`delete_chatbot_row`:

```python
        # Best-effort, like the agent and chat cleanup above: a stale knowledge
        # base must not block the delete. The row deletion is authoritative.
        for kb_id in (row.get("kb_id"), row.get("kb_full_id")):
            if not kb_id:
                continue
            try:
                self.client.delete_knowledge_base(kb_id)
            except PowabaseAPIError:
                pass
```

Extend the docstring: "Deletes its agents, its chats and its own knowledge
bases. Sources are never deleted — only unlinked — because identical content is
deduplicated and may belong to another chatbot or user."

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_chatbot_service.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: deleting a chatbot deletes its knowledge bases"
```

---

### Task 8: Frontend

**Files:**
- Modify: `frontend/knowledge.js`, `frontend/chatbots.js`, `frontend/app.js`, `frontend/index.html`, `frontend/styles.css`

**Interfaces:**
- Consumes: `currentChatbotId` from `chatbots.js`; `agents` from `agents.js`;
  `sessions` from the sessions module; the endpoints from Tasks 4, 6 and 7.
- Produces: no new globals beyond `promoteButton`.

Three separate changes. There is no build step and no frontend test harness for
these modules, so verification is the browser plus the existing
`node --test frontend/*.test.js` staying green.

**Known limitation, accepted:** the promote control lives on the attachment
chip, which is cleared when you switch chats or reload. Promotion is therefore
available in the session where you uploaded, until you navigate away. Promoting
an older upload means re-uploading it first, which costs one upload and yields
the same source id because Powabase deduplicates identical content.

- [ ] **Step 1: Scope the knowledge panel to the current chatbot**

In `frontend/knowledge.js`, rewrite the header comment to describe a chatbot's
knowledge rather than a user's, and append the chatbot id to all four calls:

```js
    const res = await authFetch(
      `/knowledge/documents?chatbot_id=${encodeURIComponent(currentChatbotId)}`
    );
```

```js
    const res = await authFetch(
      `/knowledge/documents/${encodeURIComponent(sourceId)}` +
      `?chatbot_id=${encodeURIComponent(currentChatbotId)}`,
      { method: "DELETE" }
    );
```

```js
    const res = await authFetch(
      `/knowledge/documents/${encodeURIComponent(sourceId)}/status` +
      `?chatbot_id=${encodeURIComponent(currentChatbotId)}`
    );
```

In `trainKnowledge`, add the chatbot to the form body before posting:

```js
  data.append("chatbot_id", currentChatbotId);
```

Update the two user-facing strings that promise the wrong scope:

- empty state → `"Nothing yet. Anything you add here is available to every agent in this chatbot."`
- success → `` `Added ${file.name}. Every agent in this chatbot can use it now.` ``

In `frontend/index.html`, change the `#my-knowledge` button's label to
**Chatbot knowledge** and the modal's description paragraph to "Documents every
agent in this chatbot can draw on, including the general assistant. Train once
here instead of teaching each agent separately."

In `frontend/chatbots.js`, the `change` handler already reloads agents and
sessions; the knowledge modal reads `currentChatbotId` at open time, so it needs
no additional wiring.

- [ ] **Step 2: Add the promote control**

In `frontend/index.html`, inside `#attachment-chip` after `#attachment-status`:

```html
            <button type="button" id="attachment-promote" class="chip-action" hidden>
              Save to chatbot knowledge
            </button>
```

In `frontend/app.js`, add near the other attachment constants:

```js
const attachmentPromote = document.getElementById("attachment-promote");
```

Track the current upload so the button knows what to send. Add beside
`uploadPollToken`:

```js
let attachedSource = null;   // {sessionId, sourceId} of the chip's document
```

In the `fileInput` change handler, immediately after `pollIngestStatus(...)` is
called, record it:

```js
      attachedSource = { sessionId, sourceId: body.source_id };
```

In `pollIngestStatus`, reveal the button only once indexing has succeeded — a
document that is still processing cannot be promoted:

```js
      if (body.status === "indexed") {
        showAttachment(fileName, "indexed", "ok");
        attachmentPromote.hidden = false;
        return;
      }
```

Hide it wherever the chip is hidden (the three `attachmentChip.hidden = true;`
sites) and whenever `showAttachment` runs, so a new upload never inherits the
previous document's button:

```js
  attachmentPromote.hidden = true;
```

Add the handler:

```js
attachmentPromote.addEventListener("click", async () => {
  if (!attachedSource) return;
  attachmentPromote.disabled = true;
  const { sessionId, sourceId } = attachedSource;
  try {
    const res = await authFetch(
      `/sessions/${encodeURIComponent(sessionId)}` +
      `/documents/${encodeURIComponent(sourceId)}/promote`,
      { method: "POST" }
    );
    if (res.ok || res.status === 202) {
      attachmentPromote.hidden = true;
      attachmentStatus.textContent = "saved to chatbot knowledge";
    } else {
      attachmentStatus.textContent = "could not save";
    }
  } catch (err) {
    attachmentStatus.textContent = err.message;
  } finally {
    attachmentPromote.disabled = false;
  }
});
```

Add a `.chip-action` rule to `frontend/styles.css` matching the existing
`.attachment-chip__status` treatment — small, inline, no layout shift when it
appears.

- [ ] **Step 3: Add the delete-chatbot control**

`DELETE /chatbots/{id}` exists from phase 1 but nothing in the UI calls it. Add
the button beside the picker in `frontend/index.html`, after `#new-chatbot`:

```html
        <button type="button" id="delete-chatbot" class="new-session">Delete chatbot</button>
```

In `frontend/chatbots.js`, wire it inside `wireChatbots`:

```js
  document.getElementById("delete-chatbot").addEventListener("click", deleteChatbot);
```

```js
async function deleteChatbot() {
  if (!currentChatbotId) return;
  const bot = chatbots.find((c) => c.id === currentChatbotId);
  // Count before asking: a confirmation that names the damage is worth two
  // round trips, and "are you sure?" is not. `agents` is a module global kept
  // current by loadAgents; chats are NOT — loadSessions renders straight from
  // the response without storing them — so the count is fetched here.
  const countOf = async (path) => {
    try {
      const res = await authFetch(
        `${path}?chatbot_id=${encodeURIComponent(currentChatbotId)}`
      );
      return res.ok ? (await res.json()).length : 0;
    } catch (err) {
      return 0;
    }
  };
  const [chatCount, docCount] = await Promise.all([
    countOf("/sessions"),
    countOf("/knowledge/documents"),
  ]);
  const message =
    `Delete "${bot ? bot.name : "this chatbot"}"?\n\n` +
    `This removes ${agents.length} agents, ${chatCount} chats, ` +
    `and ${docCount} documents.\n\nThis can't be undone.`;
  if (!confirm(message)) return;
  const res = await authFetch(`/chatbots/${encodeURIComponent(currentChatbotId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    // Phase 1 returns 400 for LastChatbotError, with the message to show.
    let detail = "Could not delete this chatbot.";
    try {
      detail = (await res.json()).detail || detail;
    } catch (err) { /* keep the fallback */ }
    alert(detail);
    return;
  }
  localStorage.removeItem(CHATBOT_KEY);
  await loadChatbots();
  currentSessionId = null;
  clearThread("Pick or create a chat to start.");
  await loadAgents();
  await loadSessions();
}
```

All three counts describe the selected chatbot, which is the only one this
control can delete.

- [ ] **Step 4: Check the JS suite still passes**

Run: `node --test frontend/*.test.js`
Expected: 16 pass. These cover the markdown renderer and are untouched; a
failure means something unrelated broke.

- [ ] **Step 5: Verify in a browser**

Start the backend, sign in, and confirm: the knowledge panel is labelled
**Chatbot knowledge** and empties when you switch to a second chatbot; a PDF
uploaded into a chat shows **Save to chatbot knowledge** once indexed and the
button disappears after clicking it; the delete confirmation names three counts.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: chatbot-scoped knowledge panel, promote, and delete"
```

---

### Task 9: Migration 013 — tighten phase 1

**Files:**
- Create: `backend/migrations/013_chatbot_id_not_null.sql`

**Interfaces:**
- Consumes: a fully stamped `agents.chatbot_id` and `sessions.chatbot_id`.
- Produces: nothing the application reads. This is a database constraint only.

Separate from `012` on purpose: it cannot run until the phase 1 post-deploy
sweep has stamped every row, and it must not block the rest of this work.

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/013_chatbot_id_not_null.sql
-- Run once in the Powabase Studio SQL Editor, AFTER the phase 1 post-deploy
-- sweep has stamped every row and verify_chatbot_backfill.py prints PASS.
--
-- Phase 1 left chatbot_id nullable so the then-current release kept working the
-- moment the column appeared. The backfill is verified, so an unstamped row is
-- now a bug rather than a migration state — and a row with no chatbot is a chat
-- nobody can ever see again.
--
-- If either statement fails, a row is still unstamped. That failure is the
-- desired behaviour: louder than a silently invisible chat. Re-run the phase 1
-- sweep and try again.

alter table public.agents   alter column chatbot_id set not null;
alter table public.sessions alter column chatbot_id set not null;

alter table public.agents   add constraint agents_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
alter table public.sessions add constraint sessions_chatbot_fk
  foreign key (chatbot_id) references public.chatbots (id);
```

- [ ] **Step 2: Confirm the safety check is documented**

Re-read the file and confirm it states the ordering requirement and what a
failure means. No code change accompanies this task.

- [ ] **Step 3: Run the full suite one last time**

Run: `cd backend && python -m pytest -q` — expected: all pass.
Run: `node --test frontend/*.test.js` — expected: 16 pass.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/013_chatbot_id_not_null.sql
git commit -m "feat: migration 013 — chatbot_id NOT NULL with foreign keys"
```

---

## Deploy sequence

Migrations are applied by hand in Powabase Studio; there is no SQL endpoint.

1. Finish the **phase 1 post-deploy sweep** (the two `UPDATE` statements and
   `python -m scripts.verify_chatbot_backfill`) if it is still outstanding.
2. Record the **Task 1 Step 2 baseline** query and paste it into the verifier.
3. Apply **`012`**. The running release ignores both new columns.
4. Run `python -m scripts.verify_chatbot_kb` — expect `VERDICT: PASS`.
5. Deploy the code, restart `ragchat`. Do not restart `cloudflared`.
6. Apply **`013`**. A failure here means step 1 was skipped.
7. Delete the orphaned `general-knowledge-kb` in Studio by hand, once you have
   confirmed nothing misses it.
