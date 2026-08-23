# Getting-Started Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A five-step Getting started panel on the dashboard, each step ticked from the account's real data, reopenable at any time from a Help button.

**Architecture:** A pure derivation module (`app/services/onboarding.py`) turns three lists of rows plus one boolean into five steps; a thin route (`app/api/routes/onboarding.py`) does the I/O and calls it. The frontend gets its own file (`frontend/onboarding.js`) holding one visibility boolean and one render function. No database change — completion is derived on every request, and the only stored state is a `localStorage` dismissal flag.

**Tech Stack:** FastAPI + pytest on the backend; vanilla JS with no build step and no module system on the frontend; jsdom via `tools/domtest/` for DOM behaviour.

**Spec:** `docs/superpowers/specs/2026-08-22-getting-started-checklist-design.md`

## Global Constraints

- **No migration.** Completion is derived on every request. Do not add columns, tables, or event writes.
- **The server owns all step copy.** `label` and `hint` text exists in `app/services/onboarding.py` and nowhere else. The frontend never hardcodes a label or hint string.
- **Five steps, these ids, this order:** `chatbot`, `agent`, `description`, `knowledge`, `answer`.
- **Step 5 checks `answered_by_id`, never a message count.** That column is null for user turns *and* for the general assistant, so it ticks only when a specialist answered.
- **One visibility boolean.** `helpOpen` governs rendering; no second `complete`/`dismissed` check in the render path.
- **`localStorage` key:** `rag-chat-onboarding-dismissed`. Every read and write wrapped in try/catch — a browser with site data blocked must still render the dashboard.
- **No module system.** `frontend/*.js` files are plain `<script src>` in one global scope, resolved by load order. Do not add `import`/`export`, and do not introduce a bundler.
- **Never assert panel visibility with `getComputedStyle`.** jsdom special-cases the `hidden` attribute and reports `display: none` regardless of the cascade, so such an assertion passes with the guard rule deleted. See `tools/domtest/README.md`.
- **Run backend tests with:** `cd backend && .venv/bin/python -m pytest tests/ -q`

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/onboarding.py` (create) | Pure derivation: rows in, steps out. No I/O, no HTTP. |
| `backend/app/api/routes/onboarding.py` (create) | One `GET /onboarding`. Fetches rows, calls the derivation, returns JSON. |
| `backend/app/clients/powabase_client.py` (modify) | One new method: `has_specialist_answer(session_ids)`. |
| `backend/app/main.py` (modify) | Register the router. |
| `backend/tests/unit/test_onboarding.py` (create) | Derivation unit tests. |
| `backend/tests/unit/test_routes_onboarding.py` (create) | Route + cross-owner isolation tests. |
| `frontend/onboarding.js` (create) | `helpOpen`, fetch, render, toggle wiring. |
| `frontend/index.html` (modify) | Help button, panel container, script tag. |
| `frontend/styles.css` (modify) | Panel styling **including the `[hidden]` guard**. |
| `tools/domtest/onboarding.mjs` (create) | jsdom checks for the toggle and the two modes. |

---

### Task 1: The derivation module

**Files:**
- Create: `backend/app/services/onboarding.py`
- Test: `backend/tests/unit/test_onboarding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `derive_steps(chatbots: list, agents: list, has_answer: bool) -> list[dict]` returning five dicts with keys `id`, `label`, `hint`, `done`; and `STEP_IDS: tuple` = `("chatbot", "agent", "description", "knowledge", "answer")`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_onboarding.py`:

```python
from app.services.onboarding import STEP_IDS, derive_steps


def done_map(chatbots, agents, has_answer):
    return {s["id"]: s["done"] for s in derive_steps(chatbots, agents, has_answer)}


def test_a_fresh_account_has_only_the_chatbot_step_ticked():
    """Signup creates one chatbot and nothing else. That single tick is
    deliberate: it shows what a done row looks like before you have earned one."""
    d = done_map([{"id": "cb1"}], [], False)

    assert d == {"chatbot": True, "agent": False, "description": False,
                 "knowledge": False, "answer": False}


def test_an_account_with_no_chatbot_at_all_ticks_nothing():
    assert not any(s["done"] for s in derive_steps([], [], False))


def test_an_agent_without_a_description_leaves_the_description_step_open():
    """The whole point of the panel. Routing matches the user's message against
    agent descriptions, so an agent with none is never chosen and nothing says
    why. Step 2 ticks, step 3 must not."""
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": ""}], False)

    assert d["agent"] is True
    assert d["description"] is False


def test_whitespace_is_not_a_description():
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "   \n"}], False)
    assert d["description"] is False


def test_a_missing_description_key_is_not_a_description():
    """PostgREST omits nothing here today, but a null column arrives as None
    and `None.strip()` would be a 500 on the dashboard's first paint."""
    d = done_map([{"id": "cb1"}], [{"id": "a1"}], False)
    assert d["description"] is False

    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": None}], False)
    assert d["description"] is False


def test_one_described_agent_among_many_is_enough():
    agents = [{"id": "a1", "description": ""}, {"id": "a2", "description": "Chemistry"}]
    assert done_map([{"id": "cb1"}], agents, False)["description"] is True


def test_knowledge_ticks_from_an_agent_kb():
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "x", "kb_id": "k1"}], False)
    assert d["knowledge"] is True


def test_knowledge_ticks_from_an_agents_full_kb():
    """Full-document retrieval stores its own kb id. An agent trained only that
    way is still trained."""
    d = done_map([{"id": "cb1"}],
                 [{"id": "a1", "description": "x", "kb_full_id": "k2"}], False)
    assert d["knowledge"] is True


def test_knowledge_ticks_from_chatbot_wide_knowledge():
    """Chatbot knowledge is read by every agent automatically, so a document
    uploaded there counts as training even with no agent KB anywhere."""
    d = done_map([{"id": "cb1", "kb_id": "k3"}], [{"id": "a1", "description": "x"}], False)
    assert d["knowledge"] is True


def test_an_empty_string_kb_id_is_not_knowledge():
    d = done_map([{"id": "cb1", "kb_id": ""}], [{"id": "a1", "kb_id": ""}], False)
    assert d["knowledge"] is False


def test_the_answer_step_comes_straight_from_the_flag():
    assert done_map([{"id": "cb1"}], [], True)["answer"] is True
    assert done_map([{"id": "cb1"}], [], False)["answer"] is False


def test_steps_are_always_five_in_a_fixed_order():
    """The frontend renders them in the order given and the DOM tests index by
    position, so order is part of the contract."""
    steps = derive_steps([], [], False)

    assert [s["id"] for s in steps] == list(STEP_IDS)
    assert len(steps) == 5


def test_every_step_carries_non_empty_copy():
    """The server owns all copy — the panel renders whatever arrives, so an
    empty label ships an empty row rather than falling back to anything."""
    for s in derive_steps([], [], False):
        assert s["label"].strip()
        assert s["hint"].strip()


def test_a_described_agent_with_no_document_leaves_the_knowledge_step_open():
    """Described and routable, but with nothing to retrieve from. The agent
    will be chosen and will then answer from the model alone — which is the
    failure this step exists to prevent."""
    d = done_map([{"id": "cb1"}], [{"id": "a1", "description": "Chemistry"}], False)

    assert d["description"] is True
    assert d["knowledge"] is False


def test_deleting_the_last_agent_un_ticks_its_steps():
    """The reason this is derived rather than stored. A flag set when the agent
    was created would still claim it exists, and the panel would be lying at
    exactly the moment someone needs it to be honest."""
    agents = [{"id": "a1", "description": "Chemistry", "kb_id": "k1"}]
    before = done_map([{"id": "cb1"}], agents, False)
    after = done_map([{"id": "cb1"}], [], False)

    assert (before["agent"], before["description"], before["knowledge"]) == (True, True, True)
    assert (after["agent"], after["description"], after["knowledge"]) == (False, False, False)


def test_the_description_hint_explains_routing():
    """This hint is the feature. If it does not say why a description matters,
    the panel has not solved the problem it exists for."""
    hint = next(s["hint"] for s in derive_steps([], [], False) if s["id"] == "description")

    assert "rout" in hint.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_onboarding.py -q`

Expected: collection error — `ModuleNotFoundError: No module named 'app.services.onboarding'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/onboarding.py`:

```python
"""The dashboard's getting-started checklist, derived from the account's data.

Nothing here is stored. Every step is recomputed from live rows on each
request, so deleting your only agent un-ticks its step rather than leaving a
tick for something that no longer exists — the panel is most useful exactly
when an account has gone backwards, which is when a stored flag would lie.

This module is pure on purpose: rows in, steps out. The route does the I/O.
"""
from __future__ import annotations

STEP_IDS = ("chatbot", "agent", "description", "knowledge", "answer")

# The server owns this copy so the panel is not a second place it can drift.
_COPY = {
    "chatbot": (
        "Create a chatbot",
        "A chatbot holds your agents, its knowledge, and its chats.",
    ),
    "agent": (
        "Add a specialist agent",
        "Agents are the specialists your questions get routed to.",
    ),
    "description": (
        "Give it a description",
        "Routing matches your question against each agent's description. "
        "An agent without one is never chosen.",
    ),
    "knowledge": (
        "Train it on a document",
        "Upload to an agent, or to the chatbot so every agent can read it.",
    ),
    "answer": (
        "Ask a question it can answer",
        "Ask something your document covers, and watch a specialist answer.",
    ),
}


def _text(row: dict, key: str) -> str:
    """A trimmed string for `key`, treating a missing or null column as empty."""
    return (row.get(key) or "").strip()


def _has_knowledge(row: dict) -> bool:
    """Either kind of knowledge base counts as trained.

    Chunked retrieval and full-document retrieval store separate ids, and an
    agent set up with only one of them is still trained.
    """
    return bool(_text(row, "kb_id") or _text(row, "kb_full_id"))


def derive_steps(chatbots: list, agents: list, has_answer: bool) -> list[dict]:
    """The five steps for one account, each ticked from the rows it owns.

    `has_answer` is passed in rather than derived from rows because it comes
    from a query the caller may legitimately skip: an account with no sessions
    cannot have an answer, and that is the account most likely to be looking
    at this panel.
    """
    done = {
        "chatbot": bool(chatbots),
        "agent": bool(agents),
        "description": any(_text(a, "description") for a in agents),
        "knowledge": (any(_has_knowledge(a) for a in agents)
                      or any(_has_knowledge(c) for c in chatbots)),
        "answer": bool(has_answer),
    }
    return [
        {"id": sid, "label": _COPY[sid][0], "hint": _COPY[sid][1], "done": done[sid]}
        for sid in STEP_IDS
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_onboarding.py -q`

Expected: 16 passed

- [ ] **Step 5: Mutation-check two tests that could be vacuous**

These two prove the tests can actually fail. Run each, confirm the stated failure, then **revert the edit**.

1. In `derive_steps`, change `"description": any(...)` to `"description": bool(agents)` — `test_an_agent_without_a_description_leaves_the_description_step_open` must FAIL. Revert.
2. In `_has_knowledge`, drop the `or _text(row, "kb_full_id")` — `test_knowledge_ticks_from_an_agents_full_kb` must FAIL. Revert.

Re-run the file afterwards and confirm 16 passed again.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/onboarding.py backend/tests/unit/test_onboarding.py
git commit -m "feat: derive the getting-started checklist from account data"
```

---

### Task 2: The client query for a specialist answer

**Files:**
- Modify: `backend/app/clients/powabase_client.py` (add a method next to `list_messages`, around line 491)
- Test: `backend/tests/unit/test_powabase_client_onboarding.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `PowabaseClient.has_specialist_answer(session_ids: list) -> bool`.

**Context for the implementer:** `PowabaseClient` wraps an `httpx.Client` at `self._client` and calls `self._raise_for_status(response)` after each request. Powabase's `/rest/v1/*` is PostgREST: `?id=in.(a,b)` filters to a set, `?select=id&limit=1` keeps the response tiny, and `not.is.null` matches a non-null column.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_powabase_client_onboarding.py`:

```python
from app.clients.powabase_client import PowabaseClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        return FakeResponse(self.payload)


def build(payload):
    client = PowabaseClient.__new__(PowabaseClient)
    http = FakeHTTP(payload)
    client._client = http
    return client, http


def test_no_sessions_asks_nothing_at_all():
    """An account with no chats cannot have an answer. The account most likely
    to be staring at this panel is exactly that one, so the common case must
    not cost a round trip."""
    client, http = build([])

    assert client.has_specialist_answer([]) is False
    assert http.calls == []


def test_a_returned_row_means_a_specialist_answered():
    client, http = build([{"id": "m1"}])

    assert client.has_specialist_answer(["s1", "s2"]) is True


def test_no_rows_means_no_specialist_answer():
    client, _ = build([])

    assert client.has_specialist_answer(["s1"]) is False


def test_every_session_is_asked_for_in_one_request():
    """One `in.()` filter, not a request per session. A user with forty chats
    must not produce forty round trips on every dashboard load."""
    client, http = build([])

    client.has_specialist_answer(["s1", "s2", "s3"])

    assert len(http.calls) == 1
    path, params = http.calls[0]
    assert path == "/rest/v1/messages"
    assert params["session_id"] == "in.(s1,s2,s3)"


def test_the_query_filters_to_rows_a_specialist_answered():
    """The filter must be on the server. Fetching every message and checking in
    Python would work on the fixture and fall over on a real transcript."""
    client, http = build([])

    client.has_specialist_answer(["s1"])

    _, params = http.calls[0]
    assert params["answered_by_id"] == "not.is.null"


def test_only_one_row_is_ever_fetched():
    """The answer is a yes/no. Pulling a whole transcript to compute a boolean
    is the kind of thing that is invisible until someone has 5,000 messages."""
    client, http = build([])

    client.has_specialist_answer(["s1"])

    _, params = http.calls[0]
    assert params["limit"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_powabase_client_onboarding.py -q`

Expected: FAIL — `AttributeError: 'PowabaseClient' object has no attribute 'has_specialist_answer'`

- [ ] **Step 3: Write the implementation**

In `backend/app/clients/powabase_client.py`, add directly after `list_messages`:

```python
    def has_specialist_answer(self, session_ids: list) -> bool:
        """Whether any of these chats has a turn a SPECIALIST agent answered.

        `answered_by_id` is null for user turns and for the general assistant,
        so a non-null one is proof that routing picked a specialist and it
        replied — the difference between "sent a message" and "the app did the
        thing it exists to do".

        One `in.()` query for every session rather than one per session, and no
        query at all when there are none: an account with no chats cannot have
        an answer, and that is the account most likely to be looking at the
        checklist this feeds.
        """
        if not session_ids:
            return False
        response = self._client.get(
            "/rest/v1/messages",
            params={
                "session_id": f"in.({','.join(session_ids)})",
                "answered_by_id": "not.is.null",
                "select": "id",
                "limit": 1,
            },
        )
        self._raise_for_status(response)
        return bool(response.json())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_powabase_client_onboarding.py -q`

Expected: 6 passed

- [ ] **Step 5: Mutation-check the two efficiency tests**

Efficiency assertions are the easiest to write vacuously. Make each edit, confirm the named test FAILS, then **revert**.

1. Delete the `if not session_ids: return False` guard — `test_no_sessions_asks_nothing_at_all` must FAIL.
2. Remove `"limit": 1` from the params — `test_only_one_row_is_ever_fetched` must FAIL.

- [ ] **Step 6: Commit**

```bash
git add backend/app/clients/powabase_client.py backend/tests/unit/test_powabase_client_onboarding.py
git commit -m "feat: one query for whether a specialist ever answered"
```

---

### Task 3: The endpoint

**Files:**
- Create: `backend/app/api/routes/onboarding.py`
- Modify: `backend/app/main.py` (import near the other route imports; `include_router` after line 135, before the StaticFiles mount)
- Test: `backend/tests/unit/test_routes_onboarding.py` (create)

**Interfaces:**
- Consumes: `derive_steps(chatbots, agents, has_answer) -> list[dict]` from Task 1; `client.has_specialist_answer(session_ids) -> bool` from Task 2.
- Produces: `GET /onboarding` → `{"steps": [{id, label, hint, done}], "complete": bool}`, and `router` exported as `router` from `app.api.routes.onboarding`.

**Context for the implementer:** `get_current_user` (in `app/api/deps.py`) returns the user dict and raises 401 otherwise. `get_powabase_client` lives in `app/clients/powabase_client.py`. The three existing client methods take one `owner_id` and return lists of rows: `list_chatbot_rows`, `list_agent_rows_by_owner`, `list_sessions_by_owner`.

**CRITICAL — the StaticFiles mount:** `app/main.py:137` mounts `StaticFiles` at `"/"`, and the comment above it warns that the mount swallows anything registered after it. The `include_router(onboarding_router)` line **must** go before that mount, with the other routers.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_routes_onboarding.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.onboarding import router
from app.clients.powabase_client import get_powabase_client


class FakeClient:
    """Rows for several owners at once, so isolation can actually be tested."""

    def __init__(self, chatbots=None, agents=None, sessions=None, answered=()):
        self.chatbots = list(chatbots or [])
        self.agents = list(agents or [])
        self.sessions = list(sessions or [])
        self.answered = set(answered)
        self.asked_with = None

    def list_chatbot_rows(self, owner_id):
        return [r for r in self.chatbots if r["owner_id"] == owner_id]

    def list_agent_rows_by_owner(self, owner_id):
        return [r for r in self.agents if r["owner_id"] == owner_id]

    def list_sessions_by_owner(self, owner_id):
        return [r for r in self.sessions if r["owner_id"] == owner_id]

    def has_specialist_answer(self, session_ids):
        self.asked_with = list(session_ids)
        return any(s in self.answered for s in session_ids)


def build(client, user_id="u1"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "username": "alice"}
    app.dependency_overrides[get_powabase_client] = lambda: client
    return TestClient(app)


def test_a_fresh_account_reports_only_the_chatbot_step():
    client = FakeClient(chatbots=[{"id": "cb1", "owner_id": "u1"}])

    body = build(client).get("/onboarding").json()

    assert body["complete"] is False
    assert {s["id"]: s["done"] for s in body["steps"]} == {
        "chatbot": True, "agent": False, "description": False,
        "knowledge": False, "answer": False,
    }


def test_a_fully_set_up_account_is_complete():
    client = FakeClient(
        chatbots=[{"id": "cb1", "owner_id": "u1"}],
        agents=[{"id": "a1", "owner_id": "u1", "description": "Chemistry", "kb_id": "k1"}],
        sessions=[{"id": "s1", "owner_id": "u1"}],
        answered=["s1"],
    )

    body = build(client).get("/onboarding").json()

    assert body["complete"] is True
    assert all(s["done"] for s in body["steps"])


def test_a_chat_only_the_general_assistant_answered_leaves_step_five_open():
    """The test this whole feature turns on. The user has chatted, so a naive
    implementation that counts messages ticks the step and declares them done.
    Only a specialist answering counts, and here none did."""
    client = FakeClient(
        chatbots=[{"id": "cb1", "owner_id": "u1"}],
        agents=[{"id": "a1", "owner_id": "u1", "description": "Chemistry", "kb_id": "k1"}],
        sessions=[{"id": "s1", "owner_id": "u1"}],
        answered=[],
    )

    body = build(client).get("/onboarding").json()

    assert {s["id"]: s["done"] for s in body["steps"]}["answer"] is False
    assert body["complete"] is False


def test_another_users_data_never_counts_toward_your_progress():
    """Every row here belongs to u2. If any of the four reads forgets its owner
    filter, a brand new account is congratulated on someone else's work."""
    client = FakeClient(
        chatbots=[{"id": "cb2", "owner_id": "u2", "kb_id": "k9"}],
        agents=[{"id": "a2", "owner_id": "u2", "description": "Physics", "kb_id": "k9"}],
        sessions=[{"id": "s2", "owner_id": "u2"}],
        answered=["s2"],
    )

    body = build(client, user_id="u1").get("/onboarding").json()

    assert not any(s["done"] for s in body["steps"])
    assert body["complete"] is False


def test_the_answer_query_is_scoped_to_this_users_sessions():
    """Belt and braces on the same leak: the session ids handed to the messages
    query must be the caller's, not every session in the table."""
    client = FakeClient(
        chatbots=[{"id": "cb1", "owner_id": "u1"}],
        sessions=[{"id": "s1", "owner_id": "u1"}, {"id": "s2", "owner_id": "u2"}],
    )

    build(client, user_id="u1").get("/onboarding")

    assert client.asked_with == ["s1"]


def test_the_messages_query_is_skipped_when_there_are_no_sessions():
    client = FakeClient(chatbots=[{"id": "cb1", "owner_id": "u1"}])

    build(client).get("/onboarding")

    assert client.asked_with is None


def test_the_payload_carries_server_owned_copy_for_every_step():
    """The panel renders what it is given. If the route drops label or hint,
    the UI is five blank rows."""
    body = build(FakeClient()).get("/onboarding").json()

    assert len(body["steps"]) == 5
    for step in body["steps"]:
        assert set(step) == {"id", "label", "hint", "done"}
        assert step["label"].strip() and step["hint"].strip()


def test_requires_authentication():
    app = FastAPI()
    app.include_router(router)
    # get_current_user resolves the shared client before rejecting, so state
    # has to exist for the 401 path to be reached at all.
    app.state.powabase_client = object()

    assert TestClient(app).get("/onboarding").status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_onboarding.py -q`

Expected: collection error — `ModuleNotFoundError: No module named 'app.api.routes.onboarding'`

- [ ] **Step 3: Write the route**

Create `backend/app/api/routes/onboarding.py`:

```python
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.services.onboarding import derive_steps

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding")
def get_onboarding(
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
):
    """The dashboard's getting-started checklist for the calling user.

    Derived, never stored: delete your only agent and its step un-ticks. That
    is the point — a stored flag would keep claiming an agent exists at exactly
    the moment the panel needs to be honest.

    Every read is filtered by owner. The checklist is a progress report, and a
    progress report that counts someone else's agents is both a lie and a leak.
    """
    owner_id = user["id"]
    chatbots = client.list_chatbot_rows(owner_id)
    agents = client.list_agent_rows_by_owner(owner_id)
    sessions = client.list_sessions_by_owner(owner_id)
    # No sessions means no answer, and the client skips the round trip — the
    # common case for the empty account this panel exists to help.
    has_answer = client.has_specialist_answer([s["id"] for s in sessions])

    steps = derive_steps(chatbots, agents, has_answer)
    return {"steps": steps, "complete": all(s["done"] for s in steps)}
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add the import alongside the other route imports:

```python
from app.api.routes.onboarding import router as onboarding_router
```

and the registration immediately after line 135's `app.include_router(share_router)`:

```python
    app.include_router(onboarding_router)
```

It must sit **above** the `app.mount("/", StaticFiles(...))` line — the mount swallows any route registered after it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_onboarding.py -q`

Expected: 8 passed

- [ ] **Step 6: Prove the route is actually reachable through the real app**

The mount trap above cannot be caught by the router-only tests. Append to `backend/tests/unit/test_routes_onboarding.py`:

```python
def test_the_route_is_registered_before_the_static_mount():
    """StaticFiles is mounted at "/" and swallows anything registered after it,
    so a correctly written router can still 404 in the real app. Assert against
    the assembled app, not just the router."""
    from app.main import create_app

    paths = {r.path for r in create_app().routes if hasattr(r, "path")}

    assert "/onboarding" in paths
```

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_routes_onboarding.py -q`

Expected: 9 passed. If `create_app()` needs env vars the test process lacks, set them the way `tests/unit/test_main_lifespan.py` does — read that file and follow its fixture.

- [ ] **Step 7: Mutation-check the isolation test**

In the route, change `client.list_agent_rows_by_owner(owner_id)` to pass a hardcoded `"u2"`. `test_another_users_data_never_counts_toward_your_progress` must FAIL. **Revert.**

- [ ] **Step 8: Run the whole backend suite and commit**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

Expected: all pass, 525+ tests.

```bash
git add backend/app/api/routes/onboarding.py backend/app/main.py backend/tests/unit/test_routes_onboarding.py
git commit -m "feat: GET /onboarding returns the derived checklist"
```

---

### Task 4: The panel markup and styles

**Files:**
- Modify: `frontend/index.html` (the `.dashboard` block, around lines 42-56)
- Modify: `frontend/styles.css` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: DOM ids `onboarding` (the panel), `onboarding-note`, `onboarding-steps` (the `<ul>`), `onboarding-close`, and `onboarding-help` (the header button). Task 5 wires all five.

**Context for the implementer:** `.dashboard__head-actions` currently holds `#dashboard-theme-toggle` and `#dashboard-logout`. The `.icon-btn` class is a 2.1rem round button already styled for both themes. Colour tokens are defined on `:root` in `styles.css` (`--bg-subtle`, `--text`, `--text-muted`, `--border`, `--accent`, `--accent-surface`, `--accent-border`, `--ok`) and redefined for dark mode, so styling through tokens gets dark mode for free.

- [ ] **Step 1: Add the Help button**

In `frontend/index.html`, inside `<div class="dashboard__head-actions">`, immediately **before** the `#dashboard-theme-toggle` button:

```html
          <button type="button" id="onboarding-help" class="icon-btn" aria-label="Getting started" title="Getting started" aria-expanded="false" aria-controls="onboarding">?</button>
```

- [ ] **Step 2: Add the panel**

In `frontend/index.html`, between `<p class="dashboard__status" id="dashboard-status"></p>` and `<div class="dashboard__grid" id="dashboard-grid"></div>`:

```html
      <section class="onboard" id="onboarding" hidden aria-labelledby="onboarding-title">
        <div class="onboard__head">
          <h2 class="onboard__title" id="onboarding-title">Getting started</h2>
          <button type="button" class="onboard__close" id="onboarding-close" aria-label="Hide getting started">×</button>
        </div>
        <p class="onboard__note" id="onboarding-note" hidden></p>
        <ul class="onboard__steps" id="onboarding-steps"></ul>
      </section>
```

- [ ] **Step 3: Add the styles**

Append to `frontend/styles.css`:

```css
/* Getting-started checklist. Shows itself while steps remain; the header's
   Help button reopens it afterwards as a reference. */
.onboard {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin: 0 0 1.25rem;
  padding: 1rem 1.15rem;
  border: 1px solid var(--accent-border);
  border-radius: 12px;
  background: var(--accent-surface);
}

/* The UA rule for [hidden] is display:none, but the author rule above outranks
   it — without this guard the panel renders even when hidden is set. This exact
   bug has shipped twice in this codebase (.app, then .bot-card__actions), and
   jsdom cannot catch it: see tools/domtest/README.md. */
.onboard[hidden] {
  display: none;
}

.onboard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.onboard__title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--text);
}

.onboard__close {
  flex: none;
  border: none;
  background: none;
  padding: 0.15rem 0.4rem;
  font-size: 1.15rem;
  line-height: 1;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 6px;
}

.onboard__close:hover {
  color: var(--text);
  background: var(--accent-hover);
}

.onboard__note {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.onboard__note[hidden] {
  display: none;
}

.onboard__steps {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.onboard__step {
  display: grid;
  grid-template-columns: 1.35rem 1fr;
  gap: 0.15rem 0.55rem;
  align-items: start;
}

.onboard__tick {
  grid-row: 1 / span 2;
  font-size: 0.95rem;
  line-height: 1.4;
  color: var(--text-muted);
}

.onboard__step--done .onboard__tick {
  color: var(--ok);
}

.onboard__label {
  font-size: 0.92rem;
  color: var(--text);
}

.onboard__step--done .onboard__label {
  color: var(--text-muted);
  text-decoration: line-through;
}

.onboard__hint {
  grid-column: 2;
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--text-muted);
}
```

- [ ] **Step 4: Verify the markup parses and the ids are unique**

```bash
cd /Users/oscar/Downloads/rag-chatbot && python3 - <<'EOF'
import re, collections
html = open("frontend/index.html").read()
ids = re.findall(r'id="([^"]+)"', html)
dupes = [i for i, n in collections.Counter(ids).items() if n > 1]
print("duplicate ids:", dupes or "none")
for need in ["onboarding", "onboarding-note", "onboarding-steps",
             "onboarding-close", "onboarding-help"]:
    print(f"  {need}: {'ok' if f'id=\"{need}\"' in html else 'MISSING'}")
assert not dupes
EOF
```

Expected: no duplicates, all five present.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat: getting-started panel markup and styles"
```

---

### Task 5: The panel behaviour

**Files:**
- Create: `frontend/onboarding.js`
- Modify: `frontend/index.html` (one `<script>` tag)
- Modify: `frontend/dashboard.js` (one call inside `wireDashboard`, and one inside `loadDashboard`)

**Interfaces:**
- Consumes: `GET /onboarding` from Task 3; the ids from Task 4; `authFetch(url, options)` from `frontend/app.js`.
- Produces: globals `wireOnboarding()` and `refreshOnboarding()`, both called from `frontend/dashboard.js`.

**Context for the implementer:** There is **no module system**. Every `frontend/*.js` is a plain `<script src>` sharing one global scope; a top-level `function foo()` in one file is callable from another. Do not add `import`/`export`. `authFetch` is defined in `app.js`, which loads last — that is fine, because these functions only run after boot.

- [ ] **Step 1: Write the module**

Create `frontend/onboarding.js`:

```javascript
// The dashboard's getting-started checklist.
//
// Two jobs in one panel. While steps remain it shows itself and reads as "what
// to do next". Pressing Help reopens it at any time — including long after
// everything is ticked — and it reads as "what each part does". Same five
// steps, same server-owned copy; `helpMode` decides how much of the hint text
// renders.
//
// Visibility is ONE boolean. The render path never re-checks `complete` or the
// dismissal flag: two opinions about when a panel is on screen is how a Help
// button ends up refusing to open.

const ONBOARD_DISMISS_KEY = "rag-chat-onboarding-dismissed";

const onboardPanel = document.getElementById("onboarding");
const onboardNote = document.getElementById("onboarding-note");
const onboardSteps = document.getElementById("onboarding-steps");
const onboardHelp = document.getElementById("onboarding-help");

let onboardState = null;   // last payload from GET /onboarding
let helpOpen = false;      // the single source of truth for visibility
let helpMode = false;      // opened deliberately, so show every hint

// Storage throws outright in some contexts (site data blocked, private mode),
// and a dashboard that fails to paint because of a dismissal flag would be a
// far worse bug than the panel showing once too often.
function onboardDismissed() {
  try {
    return localStorage.getItem(ONBOARD_DISMISS_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function rememberOnboardDismissed() {
  try {
    localStorage.setItem(ONBOARD_DISMISS_KEY, "1");
  } catch (err) {
    /* nothing to do — the panel simply shows again next visit */
  }
}

function renderOnboarding() {
  onboardPanel.hidden = !helpOpen;
  onboardHelp.setAttribute("aria-expanded", helpOpen ? "true" : "false");
  if (!helpOpen || !onboardState) return;

  const steps = onboardState.steps || [];
  // A wall of ticks needs a line saying what it is now for, or it reads as a
  // checklist with nothing left in it.
  const allDone = onboardState.complete;
  onboardNote.hidden = !(helpMode && allDone);
  onboardNote.textContent = "All set. Here is what each part does.";

  onboardSteps.innerHTML = "";
  steps.forEach((step) => {
    const li = document.createElement("li");
    li.className = "onboard__step" + (step.done ? " onboard__step--done" : "");
    li.dataset.step = step.id;

    const tick = document.createElement("span");
    tick.className = "onboard__tick";
    tick.textContent = step.done ? "✓" : "○";
    tick.setAttribute("aria-hidden", "true");
    li.appendChild(tick);

    const label = document.createElement("span");
    label.className = "onboard__label";
    // textContent, never innerHTML: this copy is the server's, but the habit is
    // what keeps the next person from interpolating a chatbot name in here.
    label.textContent = step.done ? `${step.label} — done` : step.label;
    li.appendChild(label);

    // The hint is the reason the step matters. Noise while you are working down
    // the list; the whole point when you came back to read.
    if (helpMode || !step.done) {
      const hint = document.createElement("span");
      hint.className = "onboard__hint";
      hint.textContent = step.hint;
      li.appendChild(hint);
    }

    onboardSteps.appendChild(li);
  });
}

function hideOnboarding() {
  // Only worth remembering while there is still something to come back to.
  // Once complete the panel never opens itself, so there is nothing to suppress.
  if (onboardState && !onboardState.complete) rememberOnboardDismissed();
  helpOpen = false;
  helpMode = false;
  renderOnboarding();
}

function toggleOnboarding() {
  if (helpOpen) {
    hideOnboarding();
    return;
  }
  helpOpen = true;
  helpMode = true;
  renderOnboarding();
}

// Called on every dashboard load. Decides whether the panel shows itself, and
// leaves an already-open panel open so a refresh mid-read does not shut it.
async function refreshOnboarding() {
  try {
    const res = await authFetch("/onboarding");
    if (!res.ok) return;
    onboardState = await res.json();
  } catch (err) {
    // A checklist is not worth breaking the dashboard over.
    return;
  }
  if (!helpOpen) {
    helpOpen = !onboardState.complete && !onboardDismissed();
    helpMode = false;
  }
  renderOnboarding();
}

function wireOnboarding() {
  onboardHelp.addEventListener("click", toggleOnboarding);
  document.getElementById("onboarding-close").addEventListener("click", hideOnboarding);
}
```

- [ ] **Step 2: Load the script**

In `frontend/index.html`, add before the `dashboard.js` tag:

```html
    <script src="/onboarding.js"></script>
```

- [ ] **Step 3: Wire it into the dashboard**

In `frontend/dashboard.js`, inside `wireDashboard()`, after the `wireShare();` line:

```javascript
  wireOnboarding();
```

And in `loadDashboard()`, add one call as the new last statement of the function. The end of that function currently reads:

```javascript
  details.forEach((detail) => dashboardGrid.appendChild(renderCard(detail)));
  dashboardGrid.appendChild(renderNewTile());
}
```

Change it to read:

```javascript
  details.forEach((detail) => dashboardGrid.appendChild(renderCard(detail)));
  dashboardGrid.appendChild(renderNewTile());
  // After the grid, not before: the checklist is a footnote to the dashboard,
  // and its fetch must never delay the cards. Deliberately not awaited.
  refreshOnboarding();
}
```

One statement is added. The closing brace is the one already there — do not add a second.

- [ ] **Step 4: Syntax-check every frontend file**

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && for f in *.js; do node --check "$f" || echo "SYNTAX FAIL $f"; done && echo "all parse"
```

Expected: `all parse`

- [ ] **Step 5: Commit**

```bash
git add frontend/onboarding.js frontend/index.html frontend/dashboard.js
git commit -m "feat: wire the getting-started panel and its Help toggle"
```

---

### Task 6: DOM tests for the toggle

**Files:**
- Create: `tools/domtest/onboarding.mjs`
- Modify: `tools/domtest/README.md` (add the new runner to the command list)

**Interfaces:**
- Consumes: everything from Tasks 3-5.
- Produces: a runner that exits non-zero on failure.

**Context for the implementer:** jsdom is installed **outside** the repo — the project has no dependency manifest and keeps none. Set up and run with:

```bash
mkdir -p /tmp/domtest && cd /tmp/domtest
npm init -y && npm install jsdom
cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs .
node onboarding.mjs
```

Read `tools/domtest/run.mjs` first and copy its `boot()` helper, its fake-server shape, and its `check()`/`flush()` helpers — this runner needs the same stubs for `/auth/signup-policy`, `/me`, `/models`, `/chatbots`, agents and sessions, plus a new `/onboarding` handler whose payload each check controls.

**Do NOT assert visibility with `getComputedStyle`.** jsdom special-cases the `hidden` attribute and returns `display: none` whether or not an author rule would really win, so such a check passes with `.onboard[hidden]` deleted from the stylesheet. Assert on `panel.hidden`. Section L of `run.mjs` already audits the cascade statically and walks every `[hidden]` element at runtime, so it covers the new panel for free — Step 4 below confirms that.

- [ ] **Step 1: Write the runner**

Create `tools/domtest/onboarding.mjs` covering exactly these checks. Use the payload helper below so each check states only what it varies:

```javascript
const STEPS = ["chatbot", "agent", "description", "knowledge", "answer"];

// `doneCount` steps ticked, in order. Mirrors the server's shape and order.
function payload(doneCount) {
  const steps = STEPS.map((id, i) => ({
    id,
    label: `Step ${id}`,
    hint: `Hint for ${id}`,
    done: i < doneCount,
  }));
  return { steps, complete: doneCount === STEPS.length };
}
```

The checks:

1. **Auto-shows when steps remain** — boot with `payload(1)`, no dismissal flag. `d.getElementById("onboarding").hidden === false`.
2. **Stays shut when complete** — boot with `payload(5)`. `panel.hidden === true`.
3. **Stays shut when dismissed** — boot with `payload(1)` and `localStorage` pre-set to `{"rag-chat-onboarding-dismissed": "1"}`. `panel.hidden === true`.
4. **Help opens it when the account is complete** — boot with `payload(5)`, click `#onboarding-help`. `panel.hidden === false`. *This is the check that matters most: the auto-show path deliberately refuses this case, so a stray `complete` test in the render path breaks it silently.*
5. **Help re-opens it after dismissal in the same page load** — boot with `payload(1)` (auto-shown), click `#onboarding-close`, assert hidden, click `#onboarding-help`, assert visible again.
6. **Help mode shows a hint under a completed step** — with `payload(5)` opened via Help, the first `.onboard__step` contains an `.onboard__hint`.
7. **Auto mode shows no hint under a completed step** — with `payload(4)` auto-shown, the first step (done) has **no** `.onboard__hint`, and the last step (not done) **has** one. *This pair is the visible difference between the two modes; assert both halves or the check proves nothing.*
8. **Every step renders** — five `.onboard__step` elements, `dataset.step` in the order of `STEPS`.
9. **Done steps are marked** — with `payload(2)`, exactly two `.onboard__step--done`.
10. **Hiding while steps remain writes the flag** — `payload(1)`, click close, `localStorage.getItem("rag-chat-onboarding-dismissed") === "1"`.
11. **Hiding when complete writes nothing** — `payload(5)`, open via Help, click close, the key is still `null`.
12. **`aria-expanded` tracks both directions** — `payload(5)`: `"false"` at boot, `"true"` after Help, `"false"` after close.
13. **The note appears only in help-mode-when-complete** — visible for `payload(5)` opened via Help; hidden for `payload(4)` opened via Help; hidden for `payload(1)` auto-shown.
14. **A failing `/onboarding` leaves the dashboard usable** — make the stub return 500. The chatbot cards still render, `panel.hidden === true`, and no uncaught error reaches the console.
15. **Storage that throws does not break the dashboard** — replace `localStorage.getItem` with a thrower before boot, using `payload(1)`. The cards render, the panel still shows, and no error escapes.

- [ ] **Step 2: Run it**

```bash
cd /tmp/domtest && cp /Users/oscar/Downloads/rag-chatbot/tools/domtest/*.mjs . && node onboarding.mjs
```

Expected: 15 checks, all `[ok  ]`, exit 0.

- [ ] **Step 3: Mutation-check the three load-bearing checks**

Make each edit in the repo, re-copy the frontend, re-run, confirm the named check FAILS, then **revert**.

1. In `renderOnboarding`, change `onboardPanel.hidden = !helpOpen;` to `onboardPanel.hidden = !helpOpen || onboardState.complete;` — check 4 must FAIL.
2. In `renderOnboarding`, change `if (helpMode || !step.done)` to `if (!step.done)` — check 6 must FAIL.
3. In `hideOnboarding`, drop the `&& !onboardState.complete` condition — check 11 must FAIL.

If any of these three still passes, the check is not testing what it claims — fix the check before moving on.

- [ ] **Step 4: Confirm the existing suites still pass, including the cascade audit**

```bash
cd /tmp/domtest && node run.mjs && node scope.mjs && node share_modal.mjs
```

Expected: 48, 14, and 5 checks, all green. `run.mjs` Section L walks every `[hidden]` element at runtime, so it now audits `.onboard` too — if `.onboard[hidden]` is missing from `styles.css`, Section L fails and names the selector.

Prove that guard is live: delete the `.onboard[hidden] { display: none; }` rule, re-copy, re-run `run.mjs`, confirm Section L FAILS naming `.onboard`, then **restore the rule**.

- [ ] **Step 5: Document the runner**

In `tools/domtest/README.md`, add to the command block:

```
node onboarding.mjs  # 15 checks: the getting-started panel, its Help toggle, and both hint modes
```

- [ ] **Step 6: Commit**

```bash
git add tools/domtest/onboarding.mjs tools/domtest/README.md
git commit -m "test: DOM coverage for the getting-started panel and Help toggle"
```

---

### Task 7: End-to-end verification against a live server

**Files:**
- Create: `tools/verify_onboarding.py`

**Interfaces:**
- Consumes: `GET /onboarding` from Task 3.
- Produces: a script that exits non-zero if the checklist misreports a real account.

**Context for the implementer:** The unit tests all use fakes. Powabase is a real service whose row shapes have already broken assumptions once in this project (a migration declared `uuid` where every KB column is `text`). This script talks to a running backend over HTTP with a real account, so the derivation is proven against real rows. Run the backend locally first: `cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000`.

- [ ] **Step 1: Write the script**

Create `tools/verify_onboarding.py`:

```python
"""Prove the getting-started checklist reports a REAL account correctly.

    cd backend && set -a && . .env && set +a && \
      .venv/bin/python ../tools/verify_onboarding.py

Every unit test for this feature runs against fakes. This signs up a throwaway
account against a running backend and walks it through the steps, asserting the
checklist moves only when it should — in particular that a chat the general
assistant answered does NOT tick the final step.

Requires the backend running on localhost:8000.
"""
import sys
import time

import httpx

BASE = "http://localhost:8000"
NAME = f"onboard_{int(time.time())}"
PASSWORD = "verify-onboarding-pw"

failures = []


def check(ok, label, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def steps(token):
    r = httpx.get(f"{BASE}/onboarding",
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    body = r.json()
    return {s["id"]: s["done"] for s in body["steps"]}, body["complete"]


r = httpx.post(f"{BASE}/auth/signup",
               json={"username": NAME, "password": PASSWORD}, timeout=60)
r.raise_for_status()
token = r.json()["access_token"]
print(f"signed up {NAME}")

print("\n=== a brand new account ===")
done, complete = steps(token)
check(done["chatbot"] is True, "the starter chatbot ticks step 1")
check(done["agent"] is False, "no agent yet")
check(done["description"] is False, "no description yet")
check(done["knowledge"] is False, "no knowledge yet")
check(done["answer"] is False, "no specialist answer yet")
check(complete is False, "a fresh account is not complete")

print("\n=== payload shape ===")
r = httpx.get(f"{BASE}/onboarding",
              headers={"Authorization": f"Bearer {token}"}, timeout=30)
body = r.json()
check(len(body["steps"]) == 5, "five steps", str(len(body["steps"])))
check([s["id"] for s in body["steps"]] ==
      ["chatbot", "agent", "description", "knowledge", "answer"],
      "steps arrive in the documented order")
check(all(s["label"].strip() and s["hint"].strip() for s in body["steps"]),
      "every step carries server-owned copy")

print("\n=== authentication ===")
r = httpx.get(f"{BASE}/onboarding", timeout=30)
check(r.status_code == 401, "unauthenticated access is refused", str(r.status_code))

print("\n" + "=" * 60)
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS — the checklist reports a real account correctly")
```

- [ ] **Step 2: Run it against a live backend**

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000 &
sleep 4
cd backend && set -a && . .env && set +a && .venv/bin/python ../tools/verify_onboarding.py
```

Expected: `PASS`. If any check fails, that is a real defect in Task 1 or 3 — fix it there, not in the script.

Stop the server afterwards.

- [ ] **Step 3: Commit**

```bash
git add tools/verify_onboarding.py
git commit -m "test: verify the checklist against a live backend"
```

- [ ] **Step 4: Report what remains manual**

The following cannot be automated here and must be reported to the user as unverified, not claimed as working:

- how the panel looks in a real browser, in both themes (jsdom has no layout engine — every user-visible bug in this area so far has been a layout or cascade bug);
- the panel on a narrow screen;
- whether the Help button reads as "getting started" rather than generic help.
