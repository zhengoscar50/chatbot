# Sharing and Embedding a Chatbot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give one chatbot an unlisted public link and an embeddable iframe, capped per day, without leaking anything about its owner.

**Architecture:** `/chat`'s handler body is extracted into `answer_turn(deps, session_row, chatbot_row, query)` so a public route reuses the same orchestration rather than copying it. A `share_token` column resolves a token to a chatbot; visitor chats are ordinary sessions flagged `shared = true`, which both hides them from the owner's sidebar and proves a session is a visitor's. Citations are redacted server-side before they leave the process.

**Tech Stack:** FastAPI, Pydantic v2, PostgREST via `httpx`, pytest. Frontend is vanilla browser JS loaded with `<script src>` tags — no modules, no bundler, no build step.

**Spec:** `docs/superpowers/specs/2026-08-21-share-and-embed-design.md`

## Global Constraints

**These are leak-prevention rules. A task that violates one is wrong even if its tests pass.**

- **A filename must never leave the process on a public route.** Redaction is server-side. Assert on the serialised response body, never on how a page renders it.
- **Unknown or foreign identifiers return `404`, never `403`.** A guessed token must be indistinguishable from a missing one.
- **A visitor session must satisfy BOTH `chatbot_id` match AND `shared = true`.** Chatbot membership alone is not enough — the owner's own chats live in the same chatbot.
- **The public page receives the chatbot's `name` and `description` and nothing else.** No agent ids, no agent list, no knowledge, no chat list, no username, no owner id.
- **No visitor upload path.** The public chat route accepts a session id and a query; nothing else.
- **Public routes must be registered before the static mount.** `main.py:133` mounts `StaticFiles` at `/`, and its own comment records that the mount swallows anything registered after it.
- Never build DOM from `innerHTML` with server-supplied values; use `createElement`/`textContent`. `innerHTML = ""` to clear is the existing idiom.
- Every colour in new CSS comes from an existing custom property. No hard-coded hex.
- Test commands (bare `python` is NOT on the PATH):
  - `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q`
  - `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js`
- Baseline entering this plan: **457 Python tests, 16 JS tests.**

---

## File Structure

**Created**
- `backend/migrations/014_chatbot_sharing.sql` — share columns and the `shared` flag
- `backend/app/services/chat_turn.py` — `TurnDeps` and `answer_turn`, extracted from `/chat`
- `backend/app/services/share_service.py` — tokens, the daily cap, citation redaction
- `backend/app/api/routes/share.py` — the public `/s` routes
- `frontend/share.html`, `frontend/share.js` — the public page
- Tests: `test_chat_turn.py`, `test_share_service.py`, `test_routes_share.py`

**Modified**
- `backend/app/api/routes/chat.py` — becomes a thin authenticated wrapper
- `backend/app/api/routes/chatbots.py` — owner-facing share endpoints
- `backend/app/api/routes/sessions.py` — `shared` filter on listing
- `backend/app/services/session_service.py` — create with `shared`, list filtered
- `backend/app/clients/powabase_client.py` — `get_chatbot_by_share_token`
- `backend/app/models/schemas.py` — share and public-chat models
- `backend/app/main.py` — register the share router before the mount
- `frontend/dashboard.js`, `frontend/index.html`, `frontend/styles.css` — the Share modal

---

### Task 1: Migration 014

**Files:**
- Create: `backend/migrations/014_chatbot_sharing.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: `chatbots.share_token` (text, null = not shared), `chatbots.share_daily_limit` (int, default 100), `chatbots.share_used_today` (int, default 0), `chatbots.share_used_date` (date), and `sessions.shared` (boolean, default false).

No application code changes. Every column is additive with a default, so the running release is unaffected.

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/014_chatbot_sharing.sql
-- Run once in the Powabase Studio SQL Editor.
--
-- Sharing: a chatbot gets an unlisted token and a per-day message cap.
--
-- share_token IS the "is it shared" state — null means not shared. There is
-- deliberately no separate boolean, because two fields describing one fact
-- eventually disagree.
--
-- TEXT, not uuid: the token is secrets.token_urlsafe output, not a uuid. Every
-- id column in this schema that holds an opaque string is already text
-- (sessions.kb_id in 001, agents.kb_id in 004, chatbots.kb_id in 012).
--
-- share_used_date sits beside share_used_today so "resets at midnight" needs
-- no scheduled job: a request arriving on a new date resets the counter in the
-- same write that increments it.

alter table public.chatbots add column if not exists share_token       text;
alter table public.chatbots add column if not exists share_daily_limit int  not null default 100;
alter table public.chatbots add column if not exists share_used_today  int  not null default 0;
alter table public.chatbots add column if not exists share_used_date   date;

-- Partial: many chatbots may have no token, but two may never share one.
create unique index if not exists chatbots_share_token_idx
  on public.chatbots (share_token) where share_token is not null;

-- Marks a chat as belonging to a visitor rather than the owner. Does two jobs:
-- keeps visitor chats out of the owner's sidebar, and proves a session is a
-- visitor's when a public request names it.
alter table public.sessions add column if not exists shared boolean not null default false;
```

- [ ] **Step 2: Confirm nothing else changed**

Run: `cd /Users/oscar/Downloads/rag-chatbot && git status --porcelain`
Expected: only the new migration file.

- [ ] **Step 3: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `457 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/014_chatbot_sharing.sql
git commit -m "feat: migration 014 — chatbot sharing columns"
```

---

### Task 2: Extract the answering core

**Files:**
- Create: `backend/app/services/chat_turn.py`
- Create: `backend/tests/unit/test_chat_turn.py`
- Modify: `backend/app/api/routes/chat.py`

**Interfaces:**
- Consumes: everything `/chat` already imports.
- Produces: `TurnDeps` (a frozen dataclass) and
  `answer_turn(deps: TurnDeps, session_row: dict, chatbot_row: dict | None, query: str) -> ChatResponse`.
  Later tasks call this from the public route.

**This is the riskiest task in the plan.** It moves the most security-sensitive code in the application. The governing rule:

> **Every existing `/chat` test must pass UNCHANGED.** If a test needs editing to accommodate the move, the move changed behaviour and is wrong. Revert and try again.

`chatbot_row` may be `None` — a legacy chat with no `chatbot_id` degrades to no chatbot knowledge rather than raising, exactly as today.

- [ ] **Step 1: Create `chat_turn.py` by moving the handler body verbatim**

```python
"""One conversational turn: route it, retrieve for it, answer it, record it.

Extracted from the /chat handler so the public share route runs the SAME
orchestration rather than a second copy of it. Two copies would drift, and the
copy that drifted would be the one strangers can reach.

This function knows nothing about authentication. Every caller is responsible
for proving the session is theirs to use BEFORE calling — /chat by ownership,
the share route by token plus the `shared` flag.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.clients.powabase_client import PowabaseAPIError
from app.models.schemas import AnsweredBy, ChatResponse
from app.services.agent_scope import roster_for
from app.services.chat_service import ChatService
from app.services.context_budget import clamp_context_tokens
from app.services.conversation import conversation_message
from app.services.orchestrator import OrchestratorService
from app.services.retrieval_scope import kb_ids_for
from app.services.session_service import DEFAULT_NAME


@dataclass(frozen=True)
class TurnDeps:
    """Everything a turn needs that is not the turn itself.

    A frozen dataclass rather than positional arguments: the handler had eleven
    dependencies, and a caller that silently swapped two of them would be very
    hard to see in review.
    """
    client: object
    sessions: object
    agents: object
    messages: object
    chatbot_kb: object
    scratch_kb_id: str | None
    orchestrator_agent_id: str
    general_assistant_id: str
    settings: object


def title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


def recent_turns(raw, turns: int) -> list:
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    history = [
        {"role": m.get("role", "user"), "text": m.get("content") or m.get("text") or ""}
        for m in items
    ]
    return history[-(turns * 2):] if turns > 0 else []


def answer_turn(deps: TurnDeps, session_row: dict, chatbot_row: dict | None,
                query: str) -> ChatResponse:
    session_id = session_row["id"]
    try:
        history = deps.messages.recent_turns(session_id, deps.settings.history_turns)
    except PowabaseAPIError:
        history = []

    # Every chat starts with the whole roster; this chat may exclude some.
    roster = roster_for(
        deps.agents.list(session_row.get("chatbot_id")),
        session_row.get("excluded_agent_ids"),
    )
    decision = OrchestratorService(deps.client, deps.orchestrator_agent_id).route(
        query, roster, history
    )

    agent_row = next((a for a in roster if a["id"] == decision.agent_id), None)
    if agent_row is not None:
        answering_agent_id = agent_row["powabase_agent_id"]
        answered_by = AnsweredBy(id=agent_row["id"], name=agent_row["name"])
    else:
        answering_agent_id = deps.general_assistant_id
        answered_by = AnsweredBy(id=None, name="General assistant")

    service = ChatService(
        deps.client, answering_agent_id,
        kb_ids_for(agent_row, session_row, deps.chatbot_kb.kb_ids(chatbot_row),
                   deps.scratch_kb_id),
        None,
        clamp_context_tokens(
            (agent_row or {}).get("max_context_tokens"),
            (agent_row or {}).get("model"),
        ),
    )
    # Agents run statelessly: a Powabase thread is bound to exactly one agent,
    # so a chat several agents take turns in cannot use one. History travels in
    # the message instead.
    result = service.ask(query, message=conversation_message(history, query))

    # Persist best-effort: the answer is already computed (and paid for), so a
    # write failure must not fail the request.
    updates: dict = {}
    if session_row.get("name") == DEFAULT_NAME:
        updates["name"] = title_from(query)
    try:
        deps.messages.add_user_turn(session_id, query)
        deps.messages.add_assistant_turn(
            session_id, result["answer"], result["citations"],
            answered_by_id=answered_by.id, answered_by_name=answered_by.name,
        )
        deps.sessions.touch(session_id, **updates)
    except (PowabaseAPIError, RuntimeError):
        pass

    return ChatResponse(
        answer=result["answer"], citations=result["citations"],
        answered_by=answered_by,
    )
```

Note `answer_turn` does **not** catch `ModelBusyError`, `InsufficientCreditsError`, `ProviderKeyError` or `PowabaseAPIError` from `service.ask`. Those map to HTTP status codes, which is a route concern — each caller keeps its own `try/except` around the call.

- [ ] **Step 2: Rewrite the `/chat` handler to call it**

Replace the body of `chat()` in `backend/app/api/routes/chat.py` from the
session lookup onward with:

```python
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # The chatbot comes off the CHAT ROW, never the request body: a client must
    # not be able to aim one chatbot's question at another chatbot's knowledge
    # or roster.
    chatbot_id = row.get("chatbot_id")
    chatbot = chatbots.get_owned(chatbot_id, user["id"]) if chatbot_id else None

    deps = TurnDeps(
        client=client, sessions=sessions, agents=agents, messages=messages,
        chatbot_kb=chatbot_kb, scratch_kb_id=scratch_kb_id,
        orchestrator_agent_id=orchestrator_agent_id,
        general_assistant_id=general_assistant_id, settings=settings,
    )
    try:
        return answer_turn(deps, row, chatbot, req.query)
    except ModelBusyError as e:
        raise HTTPException(status_code=503, detail=e.message)
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)
    except ProviderKeyError as e:
        raise HTTPException(
            status_code=424,
            detail=f"{e.message} (configure a provider key in Powabase Studio -> Settings -> LLM Provider Keys)",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

Delete the now-unused `_title_from` and `_recent_turns` from `chat.py`, and
remove imports that are no longer used there. Add
`from app.services.chat_turn import TurnDeps, answer_turn`.

- [ ] **Step 3: Run the existing chat tests — they must pass unedited**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_routes_chat.py`
Expected: PASS, with **no edits to that file**. If it fails, the extraction changed behaviour — fix `chat_turn.py`, not the test.

- [ ] **Step 4: Add a direct test of the extracted function**

Create `backend/tests/unit/test_chat_turn.py`:

```python
from app.models.schemas import ChatResponse
from app.services.chat_turn import TurnDeps, answer_turn, title_from


class FakeMessages:
    def __init__(self):
        self.user_turns = []
        self.assistant_turns = []

    def recent_turns(self, session_id, turns):
        return []

    def add_user_turn(self, session_id, text):
        self.user_turns.append((session_id, text))

    def add_assistant_turn(self, session_id, answer, citations, **kw):
        self.assistant_turns.append((session_id, answer, kw))


class FakeSessions:
    def __init__(self):
        self.touched = []

    def touch(self, session_id, **fields):
        self.touched.append((session_id, fields))


class FakeAgents:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.asked_for = []

    def list(self, chatbot_id):
        self.asked_for.append(chatbot_id)
        return list(self.rows)


class FakeChatbotKb:
    def kb_ids(self, row):
        return [row["kb_id"]] if row and row.get("kb_id") else []


def make_deps(agents=None, messages=None, sessions=None):
    return TurnDeps(
        client=object(), sessions=sessions or FakeSessions(),
        agents=agents or FakeAgents(), messages=messages or FakeMessages(),
        chatbot_kb=FakeChatbotKb(), scratch_kb_id="scratch",
        orchestrator_agent_id="orch", general_assistant_id="gen",
        settings=type("S", (), {"history_turns": 2})(),
    )


def test_the_roster_comes_from_the_sessions_chatbot(monkeypatch):
    """The roster must follow the chat row, not anything a caller passes
    separately — that is the boundary keeping one chatbot's question off
    another chatbot's agents."""
    import app.services.chat_turn as ct

    monkeypatch.setattr(ct, "OrchestratorService",
                        lambda *a, **k: type("O", (), {"route": lambda s, *a: ct.__dict__["_D"]})())
    ct._D = type("D", (), {"agent_id": None})()
    monkeypatch.setattr(ct, "ChatService",
                        lambda *a, **k: type("C", (), {
                            "ask": lambda s, q, message=None: {"answer": "ok", "citations": []}
                        })())
    agents = FakeAgents()
    deps = make_deps(agents=agents)

    answer_turn(deps, {"id": "s1", "chatbot_id": "cb-7", "name": "n"}, None, "hi")

    assert agents.asked_for == ["cb-7"]


def test_a_write_failure_does_not_lose_the_answer(monkeypatch):
    """The answer is already paid for. A persistence failure must not turn a
    successful, billed turn into a 500."""
    import app.services.chat_turn as ct

    monkeypatch.setattr(ct, "OrchestratorService",
                        lambda *a, **k: type("O", (), {"route": lambda s, *a: ct.__dict__["_D"]})())
    ct._D = type("D", (), {"agent_id": None})()
    monkeypatch.setattr(ct, "ChatService",
                        lambda *a, **k: type("C", (), {
                            "ask": lambda s, q, message=None: {"answer": "kept", "citations": []}
                        })())

    class Exploding(FakeMessages):
        def add_user_turn(self, *a, **k):
            raise RuntimeError("db down")

    result = answer_turn(make_deps(messages=Exploding()),
                         {"id": "s1", "chatbot_id": "cb", "name": "n"}, None, "hi")

    assert isinstance(result, ChatResponse)
    assert result.answer == "kept"


def test_title_from_truncates_long_queries():
    assert title_from("  hello  ") == "hello"
    assert title_from("x" * 80).endswith("…")
    assert len(title_from("x" * 80)) == 61
```

- [ ] **Step 5: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `460 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 6: Confirm the existing chat tests were not edited**

Run: `cd /Users/oscar/Downloads/rag-chatbot && git diff --stat HEAD -- backend/tests/unit/test_routes_chat.py`
Expected: **no output.** Any change there means the extraction altered behaviour.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/chat_turn.py backend/app/api/routes/chat.py backend/tests/unit/test_chat_turn.py
git commit -m "refactor: extract answer_turn from the chat handler"
```

---

### Task 3: Citation redaction

**Files:**
- Create: `backend/app/services/share_service.py`
- Create: `backend/tests/unit/test_share_service.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `redact_citations(citations: list) -> list`. Later tasks apply it to every public response.

**This is the anti-leak core.** A citation from Powabase looks like
`{"key": 1, "source_id": "<uuid>", "source_name": "Q3.pdf", "text_excerpt": "…"}`.
The public shape keeps the marker and the excerpt, replaces the name with a
stable label, and **drops `source_id` entirely** — the frontend falls back to
`source_id` when there is no name, so leaving it would print a raw identifier
where the filename used to be.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_share_service.py`:

```python
from app.services.share_service import redact_citations


def test_the_filename_never_survives():
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "Q3_confidential.pdf",
         "text_excerpt": "revenue rose"},
    ])
    assert "Q3_confidential.pdf" not in repr(out)
    assert out[0]["source_name"] == "Source 1"
    assert out[0]["text_excerpt"] == "revenue rose"


def test_the_source_id_never_survives():
    """The UI falls back to source_id when there is no name, so leaving it in
    would print a raw identifier exactly where the filename used to be."""
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "x"},
    ])
    assert "source_id" not in out[0]
    assert "u-1" not in repr(out)


def test_the_same_document_keeps_one_label():
    out = redact_citations([
        {"key": 1, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "one"},
        {"key": 2, "source_id": "u-2", "source_name": "b.pdf", "text_excerpt": "two"},
        {"key": 3, "source_id": "u-1", "source_name": "a.pdf", "text_excerpt": "three"},
    ])
    assert [c["source_name"] for c in out] == ["Source 1", "Source 2", "Source 1"]


def test_a_bare_string_citation_is_dropped():
    """The legacy citation shape IS the filename. There is nothing to redact,
    so drop it — a missing marker is better than a leaked name."""
    assert redact_citations(["secret.pdf"]) == []


def test_empty_and_none_are_safe():
    assert redact_citations([]) == []
    assert redact_citations(None) == []


def test_a_citation_with_no_excerpt_still_redacts():
    out = redact_citations([{"key": 1, "source_name": "x.pdf"}])
    assert out == [{"key": 1, "source_name": "Source 1", "text_excerpt": ""}]
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_share_service.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.share_service'`.

- [ ] **Step 3: Write the function**

Create `backend/app/services/share_service.py`:

```python
"""Sharing a chatbot: tokens, the daily cap, and public-view redaction."""
from __future__ import annotations


def redact_citations(citations: list) -> list:
    """Citations as a stranger may see them: markers and excerpts, no filename.

    The excerpt is what makes an answer credible — proof it came from a
    document rather than the model's memory. The filename carries almost none
    of that value and all of the exposure: anyone with the link would otherwise
    learn the name of every document in the chatbot's knowledge, including ones
    no answer of theirs ever touched.

    `source_id` is dropped too, not just the name. The frontend falls back to
    `source_id` when a name is absent, so keeping it would print a raw
    identifier in exactly the place the filename used to be.

    Labels are assigned per distinct source, so two markers quoting the same
    document agree — which is what the citation de-duplication in the UI needs.
    """
    labels: dict = {}
    out: list = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            # The legacy citation shape is the filename itself. There is
            # nothing to redact, so drop it: a missing marker beats a leak.
            continue
        identity = citation.get("source_id") or citation.get("source_name")
        if identity not in labels:
            labels[identity] = "Source %d" % (len(labels) + 1)
        out.append({
            "key": citation.get("key"),
            "source_name": labels[identity],
            "text_excerpt": citation.get("text_excerpt") or "",
        })
    return out
```

- [ ] **Step 4: Run the tests**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_share_service.py`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — expected `466 passed`.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/share_service.py backend/tests/unit/test_share_service.py
git commit -m "feat: redact filenames from public citations"
```

---

### Task 4: Tokens and the daily cap

**Files:**
- Modify: `backend/app/services/share_service.py`, `backend/tests/unit/test_share_service.py`
- Modify: `backend/app/clients/powabase_client.py`, `backend/tests/unit/test_powabase_client.py`

**Interfaces:**
- Consumes: `redact_citations` from Task 3 (same module); `client.update_chatbot_row(chatbot_id, fields)`, existing.
- Produces: `ShareService(client)` with `enable(chatbot_id) -> str`, `disable(chatbot_id) -> None`, `resolve(token) -> dict | None`, `consume(chatbot_row, today=None) -> bool`; the dependency `get_share_service(request)`; and `client.get_chatbot_by_share_token(token) -> dict | None`.

`consume` returns `False` when the cap is reached and `True` when it has room, incrementing as it goes.

- [ ] **Step 1: Add the client lookup**

In `backend/app/clients/powabase_client.py`, beside `get_chatbot_row`:

```python
    def get_chatbot_by_share_token(self, token: str):
        """The chatbot an unlisted share token belongs to, or None.

        An empty token would become `share_token=eq.` and match rows with an
        empty string, so it is refused before the request goes out.
        """
        if not token:
            return None
        response = self._client.get(
            "/rest/v1/chatbots", params={"share_token": f"eq.{token}"}
        )
        if response.status_code == 400:
            return None
        self._raise_for_status(response)
        rows = response.json()
        return rows[0] if rows else None
```

- [ ] **Step 2: Write the failing service tests**

Append to `backend/tests/unit/test_share_service.py`:

```python
from datetime import date

import pytest

from app.services.share_service import ShareService


class FakeClient:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.updates = []

    def update_chatbot_row(self, chatbot_id, fields):
        self.updates.append((chatbot_id, fields))
        self.rows.setdefault(chatbot_id, {"id": chatbot_id}).update(fields)

    def get_chatbot_by_share_token(self, token):
        return next((r for r in self.rows.values() if r.get("share_token") == token), None)


def bot(**over):
    return dict({"id": "cb-1", "share_token": None, "share_daily_limit": 3,
                 "share_used_today": 0, "share_used_date": None}, **over)


def test_enable_returns_a_long_unguessable_token():
    client = FakeClient([bot()])
    token = ShareService(client).enable("cb-1")
    assert len(token) >= 32
    assert client.rows["cb-1"]["share_token"] == token


def test_enabling_again_replaces_the_old_token():
    """Regeneration IS revocation-and-reissue: the old link must die."""
    client = FakeClient([bot()])
    service = ShareService(client)
    first = service.enable("cb-1")
    second = service.enable("cb-1")
    assert first != second
    assert service.resolve(first) is None
    assert service.resolve(second)["id"] == "cb-1"


def test_disable_removes_the_token():
    client = FakeClient([bot(share_token="tok")])
    ShareService(client).disable("cb-1")
    assert client.rows["cb-1"]["share_token"] is None


def test_resolving_an_unknown_or_empty_token_is_none():
    client = FakeClient([bot(share_token="tok")])
    service = ShareService(client)
    assert service.resolve("nope") is None
    assert service.resolve("") is None
    assert service.resolve(None) is None


def test_consume_allows_up_to_the_limit_then_refuses():
    client = FakeClient([bot(share_daily_limit=2)])
    service = ShareService(client)
    row = client.rows["cb-1"]
    assert service.consume(row, today=date(2026, 1, 1)) is True
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is True
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False


def test_a_new_day_resets_the_counter():
    """The whole point of storing the date beside the count: no scheduled job
    resets anything, a request on a new date does it."""
    client = FakeClient([bot(share_daily_limit=1, share_used_today=1,
                             share_used_date="2026-01-01")])
    service = ShareService(client)
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False
    assert service.consume(client.rows["cb-1"], today=date(2026, 1, 2)) is True


def test_a_zero_limit_refuses_everything():
    client = FakeClient([bot(share_daily_limit=0)])
    assert ShareService(client).consume(client.rows["cb-1"], today=date(2026, 1, 1)) is False
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_share_service.py`
Expected: FAIL — `ImportError: cannot import name 'ShareService'`.

- [ ] **Step 4: Write the service**

Append to `backend/app/services/share_service.py`:

```python
import secrets
from datetime import date

from fastapi import Request

# 32 bytes of urlsafe randomness. The link is unlisted rather than secret, but
# it must not be guessable by anyone who finds one other link.
TOKEN_BYTES = 32


class ShareService:
    """An unlisted link to one chatbot, with a per-day message cap.

    The cap is this feature's load-bearing safety property: there is no rate
    limiting anywhere else in the application, and a public route bypasses both
    authentication and ownership. Every anonymous message spends the owner's
    credits.
    """

    def __init__(self, client):
        self.client = client

    def enable(self, chatbot_id: str) -> str:
        """Create or REPLACE the token, returning the new one.

        Replacing is how revoke-and-reissue works: the previous link stops
        resolving the moment this returns.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.client.update_chatbot_row(chatbot_id, {"share_token": token})
        return token

    def disable(self, chatbot_id: str) -> None:
        self.client.update_chatbot_row(chatbot_id, {"share_token": None})

    def resolve(self, token: str):
        """The chatbot this token belongs to, or None."""
        if not token:
            return None
        return self.client.get_chatbot_by_share_token(token)

    def consume(self, chatbot_row: dict, today: date | None = None) -> bool:
        """Claim one message against today's allowance.

        Returns False when the cap is reached, having changed nothing.

        A counter from an earlier date is treated as zero and overwritten in
        the same write, so "resets at midnight" needs no scheduled job.

        Two simultaneous requests can both read the same count and both
        proceed. At this scale that costs one extra message, not a breach, and
        locking is not worth its complexity here.
        """
        today = today or date.today()
        stamp = today.isoformat()
        used = int(chatbot_row.get("share_used_today") or 0)
        limit = int(chatbot_row.get("share_daily_limit") or 0)
        if str(chatbot_row.get("share_used_date") or "") != stamp:
            used = 0
        if used >= limit:
            return False
        self.client.update_chatbot_row(chatbot_row["id"], {
            "share_used_today": used + 1,
            "share_used_date": stamp,
        })
        return True


def get_share_service(request: Request) -> "ShareService":
    """FastAPI dependency returning the shared ShareService."""
    return request.app.state.share_service
```

- [ ] **Step 5: Wire it into `main.py`**

Beside the other `app.state` assignments in `backend/app/main.py`:

```python
        app.state.share_service = ShareService(client)
```

with `from app.services.share_service import ShareService` at the top.

- [ ] **Step 6: Add a client test**

Append to `backend/tests/unit/test_powabase_client.py`, following that file's
existing `respx`/mock conventions: assert `get_chatbot_by_share_token("tok")`
issues `GET /rest/v1/chatbots?share_token=eq.tok` and returns the first row;
that it returns `None` for an empty result; and that it returns `None` without
issuing any request when the token is `""`.

- [ ] **Step 7: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 8: Commit**

```bash
git add -A backend
git commit -m "feat: share tokens and the daily message cap"
```

---

### Task 5: Visitor sessions are flagged and hidden

**Files:**
- Modify: `backend/app/services/session_service.py`, `backend/app/clients/powabase_client.py`
- Modify: `backend/app/api/routes/sessions.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_session_service.py`, `backend/tests/unit/test_routes_sessions.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SessionService.create_session(owner_id, chatbot_id, name=None, shared=False)`;
  `SessionService.list(chatbot_id, shared=False)`;
  `client.list_sessions(chatbot_id, shared=False)`;
  `GET /sessions?chatbot_id=X&shared=true` listing visitor chats.

The owner's sidebar must keep showing exactly what it shows today, which means
`shared=False` has to be the default everywhere.

- [ ] **Step 1: Write the failing service tests**

Append to `backend/tests/unit/test_session_service.py`, matching that file's
existing `FakeClient` conventions:

```python
def test_a_session_is_not_shared_by_default():
    client = FakeClient()
    SessionService(client, None, "scratch").create_session("u1", "cb-1")
    assert client.inserted[-1]["shared"] is False


def test_a_visitor_session_is_flagged_shared():
    client = FakeClient()
    SessionService(client, None, "scratch").create_session("u1", "cb-1", shared=True)
    assert client.inserted[-1]["shared"] is True


def test_listing_excludes_shared_sessions_by_default():
    """The owner's sidebar must not fill with strangers' conversations."""
    client = FakeClient()
    client.session_rows = [
        {"id": "s1", "name": "mine", "shared": False},
        {"id": "s2", "name": "a visitor's", "shared": True},
    ]
    listed = SessionService(client, None, "scratch").list("cb-1")
    assert [s["id"] for s in listed] == ["s1"]


def test_listing_shared_returns_only_visitor_sessions():
    client = FakeClient()
    client.session_rows = [
        {"id": "s1", "name": "mine", "shared": False},
        {"id": "s2", "name": "a visitor's", "shared": True},
    ]
    listed = SessionService(client, None, "scratch").list("cb-1", shared=True)
    assert [s["id"] for s in listed] == ["s2"]
```

Add `self.inserted = []` and `self.session_rows = []` to that file's `FakeClient`
if absent, with `insert_session` appending to `inserted` and `list_sessions`
returning rows filtered by the `shared` argument.

- [ ] **Step 2: Run and watch them fail**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_session_service.py`
Expected: FAIL — `create_session()` takes no `shared` argument.

- [ ] **Step 3: Implement**

In `backend/app/services/session_service.py`:

```python
    def create_session(self, owner_id: str, chatbot_id: str, name: str | None = None,
                       shared: bool = False) -> dict:
        """Create a chat. Chats belong to the user, not to an agent — the
        orchestrator picks an agent per message.

        `shared` marks a visitor's chat on a public link. It keeps the chat out
        of the owner's sidebar AND is the gate proving, later, that a session
        named by a public request belongs to a visitor rather than the owner.
        """
        return self.client.insert_session({
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "chatbot_id": chatbot_id,
            "name": name or DEFAULT_NAME,
            "shared": shared,
        })

    def list(self, chatbot_id: str, shared: bool = False) -> list:
        rows = self.client.list_sessions(chatbot_id, shared=shared)
        return [
            {"id": r["id"], "name": r["name"], "updated_at": r.get("updated_at"),
             "excluded_agent_ids": r.get("excluded_agent_ids") or []}
            for r in rows
        ]
```

In `backend/app/clients/powabase_client.py`:

```python
    def list_sessions(self, chatbot_id: str, shared: bool = False) -> list:
        response = self._client.get(
            "/rest/v1/sessions",
            params={
                "chatbot_id": f"eq.{chatbot_id}",
                "shared": f"is.{'true' if shared else 'false'}",
                "order": "updated_at.desc",
            },
        )
        self._raise_for_status(response)
        return response.json()
```

In `backend/app/api/routes/sessions.py`, add `shared: bool = Query(False)` to the
list handler and pass it through to `sessions.list(chatbot_id, shared=shared)`.

- [ ] **Step 4: Add the route test**

Append to `backend/tests/unit/test_routes_sessions.py`, using that file's fixtures:

```python
def test_listing_defaults_to_the_owners_own_chats(client, auth, fake):
    fake.sessions["v1"] = {"id": "v1", "owner_id": "o1", "chatbot_id": "cb-1",
                           "name": "a visitor's", "shared": True}
    res = client.get("/sessions?chatbot_id=cb-1", headers=auth)
    assert res.status_code == 200
    assert all(s["id"] != "v1" for s in res.json())
```

- [ ] **Step 5: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 6: Commit**

```bash
git add -A backend
git commit -m "feat: flag visitor sessions and keep them out of the sidebar"
```

---

### Task 6: Owner-facing share endpoints

**Files:**
- Modify: `backend/app/api/routes/chatbots.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/unit/test_routes_chatbots.py`

**Interfaces:**
- Consumes: `ShareService` and `get_share_service` from Task 4; `ChatbotService.get_owned(chatbot_id, owner_id)`, existing.
- Produces: `POST /chatbots/{id}/share`, `DELETE /chatbots/{id}/share`, `GET /chatbots/{id}/share`, all returning `ShareResponse`.

Add to `backend/app/models/schemas.py`:

```python
class ShareResponse(BaseModel):
    token: Optional[str] = None       # None when the chatbot is not shared
    url: Optional[str] = None
    embed: Optional[str] = None
    daily_limit: int = 0
    used_today: int = 0
```

- [ ] **Step 1: Write the failing route tests**

Append to `backend/tests/unit/test_routes_chatbots.py`, following its fixtures:

```python
def test_sharing_a_chatbot_returns_a_link_and_an_embed(client, auth, my_chatbot):
    res = client.post(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["token"]
    assert body["token"] in body["url"]
    assert body["token"] in body["embed"]
    assert "<iframe" in body["embed"]


def test_sharing_another_users_chatbot_is_not_found(client, auth, other_chatbot):
    res = client.post(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404
    assert res.json()["detail"] == "Chatbot not found"


def test_stopping_sharing_clears_the_token(client, auth, my_chatbot):
    client.post(f"/chatbots/{my_chatbot}/share", headers=auth)
    res = client.delete(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    assert res.json()["token"] is None


def test_reading_share_state_for_an_unshared_chatbot(client, auth, my_chatbot):
    res = client.get(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    assert res.json()["token"] is None


def test_reading_another_users_share_state_is_not_found(client, auth, other_chatbot):
    res = client.get(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_routes_chatbots.py`
Expected: FAIL — 404/405 on routes that do not exist.

- [ ] **Step 3: Add the routes**

In `backend/app/api/routes/chatbots.py`:

```python
def _share_response(request: Request, row: dict) -> ShareResponse:
    token = row.get("share_token")
    base = str(request.base_url).rstrip("/")
    url = f"{base}/s/{token}" if token else None
    embed = (
        f'<iframe src="{url}" width="420" height="640" style="border:0"></iframe>'
        if token else None
    )
    return ShareResponse(
        token=token, url=url, embed=embed,
        daily_limit=int(row.get("share_daily_limit") or 0),
        used_today=int(row.get("share_used_today") or 0),
    )


@router.post("/{chatbot_id}/share", response_model=ShareResponse)
async def start_sharing(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    share: ShareService = Depends(get_share_service),
):
    """Create or regenerate this chatbot's unlisted link.

    Regenerating is how revocation-and-reissue works: the previous link stops
    resolving immediately.
    """
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    token = await run_in_threadpool(share.enable, chatbot_id)
    return _share_response(request, dict(row, share_token=token))


@router.delete("/{chatbot_id}/share", response_model=ShareResponse)
async def stop_sharing(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    share: ShareService = Depends(get_share_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    await run_in_threadpool(share.disable, chatbot_id)
    return _share_response(request, dict(row, share_token=None))


@router.get("/{chatbot_id}/share", response_model=ShareResponse)
async def share_state(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return _share_response(request, row)
```

Add `Request` to the `fastapi` import, and import `ShareResponse`, `ShareService`
and `get_share_service`.

- [ ] **Step 4: Run both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 5: Commit**

```bash
git add -A backend
git commit -m "feat: owner endpoints to start, stop and read sharing"
```

---

### Task 7: The public routes

**Files:**
- Create: `backend/app/api/routes/share.py`, `backend/tests/unit/test_routes_share.py`
- Modify: `backend/app/main.py`, `backend/app/models/schemas.py`

**Interfaces:**
- Consumes: `answer_turn` / `TurnDeps` (Task 2), `redact_citations` (Task 3), `ShareService` (Task 4), `SessionService.create_session(..., shared=True)` (Task 5).
- Produces: `GET /s/{token}`, `GET /s/{token}/info`, `POST /s/{token}/session`, `POST /s/{token}/chat`.

Add to `backend/app/models/schemas.py`:

```python
class PublicChatRequest(BaseModel):
    """Deliberately minimal: a session id and a question, nothing else.

    No file, no chatbot id, no agent id, no scope. Anything a visitor could
    otherwise use to widen what they reach simply has no field to arrive in.
    """
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=4000)


class PublicChatbotInfo(BaseModel):
    name: str
    description: str = ""
```

Import `ConfigDict` from `pydantic` if it is not already imported there.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_routes_share.py`. Build a `FakeClient` and app in
the style of `tests/unit/test_routes_sessions.py`, with two chatbots — one
shared (`share_token="tok"`), one not — plus an owner-owned session
(`shared=False`) and a visitor session (`shared=True`) in the shared chatbot.

```python
def test_an_unknown_token_is_not_found(client):
    assert client.get("/s/nope/info").status_code == 404
    assert client.post("/s/nope/session").status_code == 404
    assert client.post("/s/nope/chat", json={"session_id": "v1", "query": "hi"}).status_code == 404


def test_info_exposes_only_the_name_and_description(client):
    body = client.get("/s/tok/info").json()
    assert set(body) == {"name", "description"}


def test_a_visitor_cannot_use_an_owners_session(client, fake):
    """THE test. The owner's chats live in the same chatbot as the visitors',
    so chatbot membership alone would let a stranger read and inject into a
    private conversation."""
    res = client.post("/s/tok/chat", json={"session_id": "owner-1", "query": "hi"})
    assert res.status_code == 404
    assert fake.answered == []


def test_a_visitor_cannot_use_a_session_from_another_chatbot(client):
    res = client.post("/s/tok/chat", json={"session_id": "other-1", "query": "hi"})
    assert res.status_code == 404


def test_a_visitor_session_is_created_flagged_shared(client, fake):
    body = client.post("/s/tok/session").json()
    assert fake.sessions[body["session_id"]]["shared"] is True


def test_the_answer_contains_no_filename(client, fake):
    fake.citations = [{"key": 1, "source_id": "u-1",
                       "source_name": "Q3_confidential.pdf", "text_excerpt": "x"}]
    raw = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).text
    assert "Q3_confidential.pdf" not in raw
    assert "u-1" not in raw
    assert "Source 1" in raw


def test_the_answer_exposes_no_agent_id(client, fake):
    body = client.post("/s/tok/chat", json={"session_id": "v1", "query": "hi"}).json()
    assert body["answered_by"]["id"] is None
    assert body["answered_by"]["name"]


def test_the_cap_refuses_once_the_limit_is_reached(client, fake):
    fake.chatbots["cb-shared"]["share_daily_limit"] = 1
    assert client.post("/s/tok/chat", json={"session_id": "v1", "query": "a"}).status_code == 200
    res = client.post("/s/tok/chat", json={"session_id": "v1", "query": "b"})
    assert res.status_code == 429
    assert len(fake.answered) == 1


def test_an_upload_field_is_rejected(client):
    res = client.post("/s/tok/chat",
                      json={"session_id": "v1", "query": "hi", "chatbot_id": "cb-other"})
    assert res.status_code == 422
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_routes_share.py`
Expected: FAIL — the module and routes do not exist.

- [ ] **Step 3: Write the routes**

Create `backend/app/api/routes/share.py`:

```python
"""The public face of a shared chatbot. No authentication reaches these.

Every handler answers 404 for anything it cannot serve — an unknown token, a
session that is not a visitor's, a chatbot that is not shared. A stranger must
not be able to tell "wrong token" from "no such chatbot", because that
difference is how a guessed token gets confirmed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import FRONTEND_DIR, get_settings
from app.models.schemas import AnsweredBy, ChatResponse, PublicChatbotInfo, PublicChatRequest
from app.services.agent_service import AgentService, get_agent_service
# FRONTEND_DIR comes from config, NOT from app.main: main.py imports this
# router, so importing back from it would be a circular import.
from app.services.chat_service import (
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.chat_turn import TurnDeps, answer_turn
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.general_assistant import get_general_assistant_id
from app.services.message_store import MessageStore, get_message_store
from app.services.orchestrator import get_orchestrator_agent_id
from app.services.scratch_kb import get_scratch_kb_id
from app.services.session_service import SessionService, get_session_service
from app.services.share_service import ShareService, get_share_service, redact_citations

router = APIRouter(prefix="/s", tags=["share"])

NOT_FOUND = "Not found"


def _chatbot_or_404(share: ShareService, token: str) -> dict:
    row = share.resolve(token)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


@router.get("/{token}", include_in_schema=False)
async def public_page(token: str):
    """The visitor's page. The token is not validated here on purpose — the
    page fetches /info and shows its own error, and serving the shell either
    way keeps a valid token from being distinguishable by response size."""
    return FileResponse(str(FRONTEND_DIR / "share.html"))


@router.get("/{token}/info", response_model=PublicChatbotInfo)
async def public_info(token: str, share: ShareService = Depends(get_share_service)):
    row = await run_in_threadpool(_chatbot_or_404, share, token)
    # Name and description ONLY. Never the owner, the agents, or the id.
    return PublicChatbotInfo(name=row["name"], description=row.get("description") or "")


@router.post("/{token}/session")
async def public_session(
    token: str,
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
):
    """A visitor's own conversation, so two visitors never share one."""
    row = await run_in_threadpool(_chatbot_or_404, share, token)
    created = await run_in_threadpool(
        sessions.create_session, row["owner_id"], row["id"], None, True,
    )
    return {"session_id": created["id"]}


@router.post("/{token}/chat", response_model=ChatResponse)
async def public_chat(
    token: str,
    req: PublicChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
    messages: MessageStore = Depends(get_message_store),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    scratch_kb_id: str = Depends(get_scratch_kb_id),
    orchestrator_agent_id: str = Depends(get_orchestrator_agent_id),
    general_assistant_id: str = Depends(get_general_assistant_id),
    settings=Depends(get_settings),
):
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)

    session_row = await run_in_threadpool(sessions.get, req.session_id)
    # BOTH conditions. Chatbot membership alone is not enough: the owner's own
    # chats live in this same chatbot, and without the `shared` check a visitor
    # could name one and read or inject into a private conversation.
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    if not await run_in_threadpool(share.consume, chatbot):
        raise HTTPException(
            status_code=429,
            detail="This demo has reached its limit for today — try again tomorrow.",
        )

    deps = TurnDeps(
        client=client, sessions=sessions, agents=agents, messages=messages,
        chatbot_kb=chatbot_kb, scratch_kb_id=scratch_kb_id,
        orchestrator_agent_id=orchestrator_agent_id,
        general_assistant_id=general_assistant_id, settings=settings,
    )
    try:
        result = await run_in_threadpool(
            answer_turn, deps, session_row, chatbot, req.query
        )
    except (ModelBusyError, InsufficientCreditsError, ProviderKeyError,
            PowabaseAPIError, RuntimeError):
        # Deliberately opaque. The authenticated route reports which upstream
        # failed and how; a stranger learns only that it did not work.
        raise HTTPException(status_code=503, detail="Sorry — that didn't work. Try again.")

    # Redact on the way out: markers and excerpts, never a filename, and never
    # the internal agent id.
    return ChatResponse(
        answer=result.answer,
        citations=redact_citations(result.citations),
        # The agent NAME is worth showing; its id is an internal identifier a
        # stranger has no use for and no business holding.
        answered_by=None if result.answered_by is None
        else AnsweredBy(id=None, name=result.answered_by.name),
    )
```

- [ ] **Step 4: Register it before the static mount**

In `backend/app/main.py`, add `app.include_router(share_router)` immediately
**above** the `app.mount("/", StaticFiles(...))` line, importing it as
`from app.api.routes.share import router as share_router`.

- [ ] **Step 5: Run the share tests, then both suites**

Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q tests/unit/test_routes_share.py` — PASS.
Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.

- [ ] **Step 6: Prove the leak tests bite**

Temporarily replace `redact_citations(result.citations)` with
`result.citations`, re-run `test_the_answer_contains_no_filename`, and confirm
it FAILS. Restore the line and confirm it passes. Report both outputs — an
anti-leak test that cannot fail is worse than none.

- [ ] **Step 7: Commit**

```bash
git add -A backend
git commit -m "feat: public share routes, capped and redacted"
```

---

### Task 8: The public page

**Files:**
- Create: `frontend/share.html`, `frontend/share.js`
- Modify: `frontend/styles.css`

**Interfaces:**
- Consumes: `GET /s/{token}/info`, `POST /s/{token}/session`, `POST /s/{token}/chat` from Task 7; `parseMarkdown` / `renderMarkdown` from `frontend/markdown.js`, unchanged.
- Produces: nothing later tasks depend on.

The page loads **only** `markdown.js` and `share.js`. It must not load `app.js`,
`agents.js`, `chatbots.js`, `dashboard.js`, `knowledge.js` or `scope.js` — those
carry account concepts that have no business on a public page, and loading them
is how one leaks by accident.

- [ ] **Step 1: Write `share.html`**

A minimal document with the chatbot name in a header, a `#thread`, a composer
(`#q` input plus a send button), and a `#status` line. Load exactly two scripts:

```html
    <script src="/markdown.js"></script>
    <script src="/share.js"></script>
```

Reuse existing class names (`.thread`, `.composer`, `.bubble`) so no new CSS is
needed beyond a small `.share-head` rule.

- [ ] **Step 2: Write `share.js`**

```js
// The public chat page. No accounts, no uploads, no navigation.
//
// The token comes from the path — /s/<token> — and is the only credential.
// Everything the page shows comes from three endpoints that return the
// chatbot's name and its answers, and nothing else about the owner.

const TOKEN = location.pathname.split("/")[2] || "";
const thread = document.getElementById("thread");
const input = document.getElementById("q");
const status = document.getElementById("status");
let sessionId = null;
let busy = false;

async function api(path, body) {
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
}

function bubble(role, text) {
  const el = document.createElement("div");
  el.className = `bubble bubble--${role}`;
  if (role === "assistant") {
    el.appendChild(renderMarkdown(text));
  } else {
    el.textContent = text;          // never innerHTML: this is user input
  }
  thread.appendChild(el);
  thread.scrollTop = thread.scrollHeight;
  return el;
}

function citations(list) {
  if (!list || !list.length) return;
  const box = document.createElement("div");
  box.className = "citations";
  list.forEach((c) => {
    const item = document.createElement("p");
    item.className = "citation";
    // source_name is already "Source 1" — the server redacts filenames.
    item.textContent = `[${c.key}] ${c.source_name}: ${c.text_excerpt}`;
    box.appendChild(item);
  });
  thread.appendChild(box);
}

async function boot() {
  const res = await fetch(`/s/${encodeURIComponent(TOKEN)}/info`);
  if (!res.ok) {
    status.textContent = "This link isn't available.";
    input.disabled = true;
    return;
  }
  const info = await res.json();
  document.getElementById("bot-name").textContent = info.name;
  document.title = info.name;
  if (info.description) {
    document.getElementById("bot-desc").textContent = info.description;
  }
}

async function send() {
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true;
  input.value = "";
  bubble("user", text);
  status.textContent = "Thinking…";
  try {
    if (!sessionId) {
      const created = await api("/session");
      if (!created.ok) throw new Error("unavailable");
      sessionId = (await created.json()).session_id;
    }
    const res = await api("/chat", { session_id: sessionId, query: text });
    const body = await res.json().catch(() => ({}));
    if (res.status === 429) {
      status.textContent = body.detail || "This demo has reached its limit for today.";
      return;
    }
    if (!res.ok) {
      status.textContent = "Sorry — that didn't work. Try again.";
      return;
    }
    status.textContent = body.answered_by ? `answered by ${body.answered_by.name}` : "";
    bubble("assistant", body.answer);
    citations(body.citations);
  } catch (err) {
    status.textContent = "Sorry — that didn't work. Try again.";
  } finally {
    busy = false;
  }
}

document.getElementById("send").addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
boot();
```

- [ ] **Step 3: Confirm the page loads nothing it should not**

Run:

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && \
  grep -o 'src="/[a-z]*\.js"' share.html
```

Expected: exactly `src="/markdown.js"` and `src="/share.js"`. Any other script
is a finding.

- [ ] **Step 4: Run both suites and syntax-check**

Run: `node --check frontend/share.js` — no output.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/share.html frontend/share.js frontend/styles.css
git commit -m "feat: the public chat page"
```

---

### Task 9: The Share modal on the dashboard

**Files:**
- Modify: `frontend/dashboard.js`, `frontend/index.html`, `frontend/styles.css`

**Interfaces:**
- Consumes: `GET/POST/DELETE /chatbots/{id}/share` from Task 6; `renderCard`, `loadDashboard`, `dashboardStatus`, `closeAllCardMenus` from the existing dashboard module.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the modal markup to `index.html`**

Beside the other modals, a `#share-modal` with `.modal` class containing: a
title, a read-only `#share-url` input with a `#share-copy` button, a read-only
`#share-embed` textarea with a `#share-embed-copy` button, a `#share-usage`
line, and three buttons — `#share-regenerate`, `#share-stop`, `#share-close`.

`.modal[hidden]` already exists in `styles.css:1034`, so the modal hides
correctly without a new rule.

- [ ] **Step 2: Add "Share" to the card menu in `dashboard.js`**

In `renderCard`, extend the actions list so it reads:

```js
  [["Share", () => openShare(bot)],
   ["Rename", () => renameChatbot(bot)],
   ["Delete", () => deleteChatbotFromDashboard(bot)]].forEach(([text, run]) => {
```

- [ ] **Step 3: Implement the modal**

```js
const shareModal = document.getElementById("share-modal");
let sharingBot = null;

async function openShare(bot) {
  sharingBot = bot;
  shareModal.hidden = false;
  await refreshShare();
}

async function refreshShare() {
  const res = await authFetch(`/chatbots/${encodeURIComponent(sharingBot.id)}/share`);
  if (!res.ok) {
    document.getElementById("share-usage").textContent = "Could not load sharing.";
    return;
  }
  paintShare(await res.json());
}

function paintShare(state) {
  const on = Boolean(state.token);
  document.getElementById("share-url").value = state.url || "";
  document.getElementById("share-embed").value = state.embed || "";
  document.getElementById("share-usage").textContent = on
    ? `${state.used_today} / ${state.daily_limit} messages used today`
    : "Not shared yet.";
  document.getElementById("share-regenerate").textContent =
    on ? "Regenerate link" : "Create link";
  document.getElementById("share-stop").hidden = !on;
}

function wireShare() {
  document.getElementById("share-close").addEventListener("click", () => {
    shareModal.hidden = true;
  });
  document.getElementById("share-regenerate").addEventListener("click", async () => {
    const res = await authFetch(
      `/chatbots/${encodeURIComponent(sharingBot.id)}/share`, { method: "POST" });
    if (res.ok) { paintShare(await res.json()); await loadDashboard(); }
  });
  document.getElementById("share-stop").addEventListener("click", async () => {
    // Regenerating leaves the old link dead; stopping leaves no link at all.
    if (!confirm("Stop sharing? The existing link will stop working.")) return;
    const res = await authFetch(
      `/chatbots/${encodeURIComponent(sharingBot.id)}/share`, { method: "DELETE" });
    if (res.ok) { paintShare(await res.json()); await loadDashboard(); }
  });
  [["share-copy", "share-url"], ["share-embed-copy", "share-embed"]].forEach(
    ([button, field]) => {
      document.getElementById(button).addEventListener("click", () => {
        const el = document.getElementById(field);
        el.select();
        navigator.clipboard.writeText(el.value);
        document.getElementById("share-usage").textContent = "Copied.";
      });
    });
}
```

Call `wireShare();` from `wireDashboard()`, and add
`shareModal.hidden = true;` to `showDashboard()` alongside the other modal
resets so an open Share modal cannot survive a return to the grid.

- [ ] **Step 4: Show sharing state on the card**

The spec requires the dashboard to answer "what am I currently exposing?" at a
glance. Without it, a chatbot can stay shared indefinitely with nothing on
screen saying so.

`GET /chatbots` does not return `share_token`, and it should not start to —
the dashboard would then hold live tokens for every chatbot in memory. Instead
`loadCardDetail` already fans out per chatbot; add the share state to that fan-out
alongside the agents and chats it already fetches:

```js
    authFetch(`/chatbots/${encodeURIComponent(bot.id)}/share`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
```

Destructure it as `share` and pass it into `renderCard`. Then, after the chat
count line:

```js
  if (share && share.token) {
    const shared = document.createElement("p");
    shared.className = "bot-card__shared";
    shared.textContent = `Shared · ${share.used_today}/${share.daily_limit} today`;
    card.appendChild(shared);
  }
```

And a visitor-chat count, which uses the endpoint from an earlier task:

```js
    authFetch(`/sessions?chatbot_id=${encodeURIComponent(bot.id)}&shared=true`)
      .then((r) => (r.ok ? r.json() : [])).catch(() => []),
```

rendered as `${visitors.length} visitor chats` when greater than zero.

Style `.bot-card__shared` with `color: var(--accent)` and the same font size as
`.bot-card__count`. No hard-coded colour.

- [ ] **Step 5: Static id cross-check**

Run:

```bash
cd /Users/oscar/Downloads/rag-chatbot/frontend && \
  grep -ho 'getElementById("[a-z-]*")' *.js | sed 's/.*("\(.*\)")/\1/' | sort -u > /tmp/js_ids.txt && \
  grep -ho 'id="[a-z-]*"' *.html | sed 's/id="\(.*\)"/\1/' | sort -u > /tmp/html_ids.txt && \
  comm -23 /tmp/js_ids.txt /tmp/html_ids.txt
```

Expected: **no output.** Any line is an id referenced in JS that no element
provides — which throws at load and kills every script after it.

- [ ] **Step 6: Run both suites**

Run: `node --check frontend/dashboard.js` — no output.
Run: `cd /Users/oscar/Downloads/rag-chatbot && node --test frontend/*.test.js` — expected 16 pass.
Run: `cd /Users/oscar/Downloads/rag-chatbot/backend && .venv/bin/python -m pytest -q` — all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/dashboard.js frontend/index.html frontend/styles.css
git commit -m "feat: the Share modal and sharing state on the dashboard"
```

---

## Deploy sequence

1. Apply **`014`** in the Powabase Studio SQL Editor. Every column is additive
   with a default, so the running release is unaffected and this can go first.
2. Push, then on the box: `git pull && sudo systemctl restart ragchat`.
   Not `cloudflared` — the public hostname is regenerated when it restarts, and
   that hostname is inside every link you have shared.
3. Hard-refresh the browser; several JS files changed.

## Live verification

Static checks cannot see any of this. Do it in a browser:

1. Share a chatbot from `⋯ → Share`; copy the link.
2. Open it in a **private window**: it answers, names the agent, and shows
   citations reading `Source 1`, never a filename.
3. Open the network tab on that request and read the raw JSON — confirm no
   filename and no agent id appear in the payload, not merely on screen.
4. A second private window gets its own conversation; neither sees the other's
   messages.
5. The owner's sidebar shows no visitor chats.
6. Regenerate the link, then reload the old one — it is gone.
7. Paste the embed snippet into a plain HTML file and open it: the iframe
   renders and works.
8. Set `share_daily_limit` to 1 in Studio and send two messages: the second is
   refused with the cap message, and no answer is generated.
