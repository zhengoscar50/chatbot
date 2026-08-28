from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.chatbots import router
from app.clients.powabase_client import get_powabase_client
from app.services.agent_service import get_agent_service
from app.services.chatbot_service import ChatbotService, LastChatbotError, get_chatbot_service
from app.services.session_service import get_session_service
from app.services.share_service import get_share_service


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


class FakeShare:
    """Stands in for ShareService. Records calls; mints a fixed token."""

    def __init__(self, token="tok-abc123"):
        self.token = token
        self.enabled = []
        self.disabled = []

    def enable(self, chatbot_id):
        self.enabled.append(chatbot_id)
        return self.token

    def disable(self, chatbot_id):
        self.disabled.append(chatbot_id)


class FakeSessions:
    """Sessions for one chatbot, holding BOTH the owner's private chats and
    visitors' shared ones — because telling those apart is the inbox's whole
    access story, and a fake carrying only shared rows could not fail the test
    that checks it."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def get(self, session_id):
        return next((r for r in self.rows if r["id"] == session_id), None)

    def delete(self, session_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != session_id]
        return len(self.rows) != before

    def list(self, chatbot_id, shared=False):
        return [
            {"id": r["id"], "name": r.get("name", "New chat"),
             "updated_at": r.get("updated_at")}
            for r in self.rows
            if r["chatbot_id"] == chatbot_id and bool(r.get("shared")) == shared
        ]


class FakeMessageClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.asked_for = None

    def messages_for_sessions(self, session_ids):
        self.asked_for = list(session_ids)
        return [m for m in self.rows if m["session_id"] in set(session_ids)]


def build_app(bots, share=None, sessions=None, client=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_chatbot_service] = lambda: bots
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace()
    app.dependency_overrides[get_session_service] = lambda: sessions or SimpleNamespace()
    app.dependency_overrides[get_share_service] = lambda: share or FakeShare()
    app.dependency_overrides[get_powabase_client] = lambda: client or FakeMessageClient()
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


def test_a_whitespace_only_name_is_rejected():
    """min_length counts characters, so "   " passes it — and renders as a
    blank, unselectable row in the chatbot picker."""
    from app.models.schemas import ChatbotCreateRequest, ChatbotUpdateRequest
    import pytest as _pytest

    for model in (ChatbotCreateRequest, ChatbotUpdateRequest):
        with _pytest.raises(ValueError):
            model(name="   ")


def test_a_name_is_stored_trimmed():
    from app.models.schemas import ChatbotCreateRequest

    assert ChatbotCreateRequest(name="  Work  ").name == "Work"


# --- share endpoints -----------------------------------------------------

@pytest.fixture
def bots():
    return FakeChatbots()


@pytest.fixture
def share():
    return FakeShare()


@pytest.fixture
def client(bots, share):
    return TestClient(build_app(bots, share))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def my_chatbot(bots):
    row = {"id": "cb-mine", "owner_id": "o1", "name": "Mine", "description": ""}
    bots.rows.append(row)
    return row["id"]


@pytest.fixture
def other_chatbot(bots):
    row = {"id": "cb-theirs", "owner_id": "someone-else", "name": "Theirs",
           "description": ""}
    bots.rows.append(row)
    return row["id"]


def test_sharing_a_chatbot_returns_a_link_and_an_embed(client, auth, my_chatbot):
    res = client.post(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["token"]
    assert body["token"] in body["url"]
    assert body["token"] in body["embed"]
    assert "<iframe" in body["embed"]


def test_sharing_another_users_chatbot_is_not_found(client, auth, other_chatbot, share):
    res = client.post(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404
    assert res.json()["detail"] == "Chatbot not found"
    # The ownership check must run before any token is minted: minting one
    # here would both create a working public link into a stranger's
    # chatbot and (since enable() replaces) destroy the real owner's link.
    assert share.enabled == []


def test_stopping_sharing_clears_the_token(client, auth, my_chatbot):
    client.post(f"/chatbots/{my_chatbot}/share", headers=auth)
    res = client.delete(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    assert res.json()["token"] is None


def test_stopping_sharing_on_another_users_chatbot_is_not_found(client, auth, other_chatbot, share):
    res = client.delete(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404
    assert res.json()["detail"] == "Chatbot not found"
    assert share.disabled == []


def test_reading_share_state_for_an_unshared_chatbot(client, auth, my_chatbot):
    res = client.get(f"/chatbots/{my_chatbot}/share", headers=auth)
    assert res.status_code == 200
    assert res.json()["token"] is None


def test_used_today_is_stale_across_a_date_change(client, auth, bots):
    """`consume` is the only path that applies the date reset when it writes.
    A read-only response must apply that same reset itself, or the morning
    after 87 messages the modal still says 87 — a full day after the true
    count reset to zero."""
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bots.rows.append({
        "id": "cb-stale", "owner_id": "o1", "name": "Stale", "description": "",
        "share_token": "tok-stale", "share_daily_limit": 100,
        "share_used_today": 87, "share_used_date": yesterday,
    })
    res = client.get("/chatbots/cb-stale/share", headers=auth)
    assert res.status_code == 200
    assert res.json()["used_today"] == 0


def test_reading_another_users_share_state_is_not_found(client, auth, other_chatbot):
    res = client.get(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404


def test_share_response_offers_the_widget_snippet_too(client, auth, my_chatbot, share):
    """Both snippets, because they are for different situations: the iframe for
    somebody who wants to place a rectangle themselves, the script tag for
    somebody who wants a tab on the edge of their page."""
    share.token = "tok"
    body = client.post(f"/chatbots/{my_chatbot}/share", headers=auth).json()

    assert '<iframe' in body["embed"]
    assert '<script' in body["widget"]
    assert 'data-token="tok"' in body["widget"]
    assert "/widget.js" in body["widget"]


def test_an_unshared_chatbot_offers_neither_snippet(client, auth, my_chatbot):
    body = client.get(f"/chatbots/{my_chatbot}/share", headers=auth).json()

    assert body["embed"] is None
    assert body["widget"] is None


# --- GET /chatbots/{id}/inbox ---------------------------------------------

BOT = {"id": "cb1", "owner_id": "o1", "name": "Support"}


def inbox_app(session_rows, message_rows=(), bots=None):
    sessions = FakeSessions(session_rows)
    msgs = FakeMessageClient(message_rows)
    app = build_app(bots or FakeChatbots([BOT]), sessions=sessions, client=msgs)
    return TestClient(app), msgs


def test_the_inbox_never_shows_the_owners_own_chats():
    """The guard this endpoint exists to hold. Visitor sessions and the owner's
    private chats sit in one table separated only by `shared`, so listing
    without that filter quietly turns an inbox of visitor conversations into a
    dump of the owner's own. Asserted by absence, because that failure looks
    like extra rows rather than an error."""
    client, _ = inbox_app([
        {"id": "visitor-1", "chatbot_id": "cb1", "shared": True},
        {"id": "my-private-chat", "chatbot_id": "cb1", "shared": False},
    ])

    ids = [r["id"] for r in client.get("/chatbots/cb1/inbox").json()]

    assert ids == ["visitor-1"]
    assert "my-private-chat" not in ids


def test_the_inbox_only_covers_the_chatbot_asked_for():
    client, _ = inbox_app([
        {"id": "mine", "chatbot_id": "cb1", "shared": True},
        {"id": "other-bot", "chatbot_id": "cb2", "shared": True},
    ])

    assert [r["id"] for r in client.get("/chatbots/cb1/inbox").json()] == ["mine"]


def test_another_users_chatbot_is_not_found():
    """Ownership is checked before anything is read, so a stranger cannot use
    this to discover that a chatbot exists."""
    client, msgs = inbox_app(
        [{"id": "v1", "chatbot_id": "cb-theirs", "shared": True}],
        bots=FakeChatbots([{"id": "cb-theirs", "owner_id": "someone-else",
                            "name": "Theirs"}]),
    )

    assert client.get("/chatbots/cb-theirs/inbox").status_code == 404
    assert msgs.asked_for is None


def test_rows_carry_the_visitors_first_question():
    client, _ = inbox_app(
        [{"id": "v1", "chatbot_id": "cb1", "shared": True}],
        # `content`, the real column name — see migrations/006.
        [{"session_id": "v1", "role": "assistant", "content": "Hi!",
          "created_at": "2026-08-27T10:00:00Z"},
         {"session_id": "v1", "role": "user", "content": "are you open sundays",
          "created_at": "2026-08-27T10:00:05Z"}],
    )

    row = client.get("/chatbots/cb1/inbox").json()[0]

    assert row["preview"] == "are you open sundays"
    assert row["message_count"] == 2


def test_messages_are_fetched_only_for_the_listed_sessions():
    """One batched query, and it must be scoped to the rows actually being
    shown — asking for the owner's private session ids would pull transcripts
    the inbox then has no reason to hold."""
    client, msgs = inbox_app([
        {"id": "v1", "chatbot_id": "cb1", "shared": True},
        {"id": "private", "chatbot_id": "cb1", "shared": False},
    ])

    client.get("/chatbots/cb1/inbox")

    assert msgs.asked_for == ["v1"]


def test_a_chatbot_nobody_has_messaged_returns_an_empty_list():
    client, msgs = inbox_app([])

    res = client.get("/chatbots/cb1/inbox")

    assert res.status_code == 200
    assert res.json() == []
    assert msgs.asked_for == []


def test_the_inbox_is_capped_at_one_page_of_conversations():
    """The cap is what bounds the batched message query: without it, a chatbot
    with thousands of visitor sessions asks for every message ever sent to it
    in a single request."""
    from app.api.routes.chatbots import INBOX_LIMIT

    client, msgs = inbox_app([
        {"id": f"v{n}", "chatbot_id": "cb1", "shared": True,
         "updated_at": f"2026-08-27T{n // 60:02d}:{n % 60:02d}:00Z"}
        for n in range(INBOX_LIMIT + 10)
    ])

    rows = client.get("/chatbots/cb1/inbox").json()

    assert len(rows) == INBOX_LIMIT
    assert len(msgs.asked_for) == INBOX_LIMIT


# --- the embed snippets ----------------------------------------------------


def snippet_for(host="https://chat.example.com/", token="tok"):
    from app.api.routes.chatbots import _share_response
    from types import SimpleNamespace

    return _share_response(
        SimpleNamespace(base_url=host),
        {"share_token": token, "share_daily_limit": 100},
    )


def test_the_widget_snippet_reports_a_host_that_stops_resolving():
    """The one failure nothing else can speak for. If widget.js does not load,
    none of the widget's code runs, so there is nothing left on the page to
    notice — the message has to be in the snippet itself, on the embedding
    site. It names the address that failed, because the person reading that
    console is usually not the person who owns the chatbot."""
    widget = snippet_for().widget

    assert "onerror=" in widget
    assert "chat.example.com" in widget
    assert "re-copy the embed snippet" in widget


def test_a_hostile_host_header_cannot_break_out_of_the_snippet():
    """base_url comes from the Host header, which the client sets, and it is
    interpolated into an HTML attribute the owner is invited to paste onto
    another site."""
    widget = snippet_for(host='https://evil"onload="alert(1)/').widget

    assert '"onload="alert(1)' not in widget
    assert "&quot;" in widget


def test_snippets_are_absent_entirely_when_sharing_is_off():
    off = snippet_for(token=None)

    assert off.widget is None and off.embed is None and off.url is None


# --- deleting visitor conversations ----------------------------------------


def test_deleting_a_conversation_removes_it():
    sessions = FakeSessions([{"id": "v1", "chatbot_id": "cb1", "shared": True}])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    assert client.delete("/chatbots/cb1/inbox/v1").status_code == 204
    assert sessions.rows == []


def test_this_route_cannot_reach_the_owners_private_chats():
    """The guard that matters most. Visitor and owner sessions live in one
    table separated only by `shared`, so a delete route missing that check
    would let the inbox destroy the owner's own conversations — worse than
    having no delete at all. Asserted on the row surviving, not on the status
    code, because a route that 404s and deletes anyway would still pass a
    status-only check."""
    sessions = FakeSessions([{"id": "mine", "chatbot_id": "cb1", "shared": False}])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    res = client.delete("/chatbots/cb1/inbox/mine")

    assert res.status_code == 404
    assert [r["id"] for r in sessions.rows] == ["mine"]


def test_a_conversation_from_another_chatbot_is_not_deletable_here():
    sessions = FakeSessions([{"id": "v1", "chatbot_id": "cb2", "shared": True}])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    res = client.delete("/chatbots/cb1/inbox/v1")

    assert res.status_code == 404
    assert len(sessions.rows) == 1


def test_another_users_chatbot_cannot_be_purged():
    sessions = FakeSessions([{"id": "v1", "chatbot_id": "cb-theirs", "shared": True}])
    bots = FakeChatbots([{"id": "cb-theirs", "owner_id": "someone-else", "name": "T"}])
    client = TestClient(build_app(bots, sessions=sessions))

    assert client.delete("/chatbots/cb-theirs/inbox/v1").status_code == 404
    assert client.delete("/chatbots/cb-theirs/inbox").status_code == 404
    assert len(sessions.rows) == 1


def test_clearing_the_inbox_deletes_every_visitor_conversation():
    sessions = FakeSessions([
        {"id": "v1", "chatbot_id": "cb1", "shared": True},
        {"id": "v2", "chatbot_id": "cb1", "shared": True},
    ])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    res = client.delete("/chatbots/cb1/inbox")

    assert res.json() == {"deleted": 2}
    assert sessions.rows == []


def test_clearing_the_inbox_spares_the_owners_own_chats():
    """The same flag, on the bulk path. This is where getting it wrong costs
    the most, because it takes everything at once."""
    sessions = FakeSessions([
        {"id": "visitor", "chatbot_id": "cb1", "shared": True},
        {"id": "mine", "chatbot_id": "cb1", "shared": False},
        {"id": "other-bot", "chatbot_id": "cb2", "shared": True},
    ])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    client.delete("/chatbots/cb1/inbox")

    assert sorted(r["id"] for r in sessions.rows) == ["mine", "other-bot"]


def test_clearing_an_empty_inbox_is_harmless():
    sessions = FakeSessions([])
    client = TestClient(build_app(FakeChatbots([BOT]), sessions=sessions))

    assert client.delete("/chatbots/cb1/inbox").json() == {"deleted": 0}
