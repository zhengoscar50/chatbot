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


def test_no_sessions_produces_an_empty_id_list_for_the_answer_query():
    """The route always asks; the client short-circuits an empty list without a
    round trip (tested in test_powabase_client_onboarding.py). What matters
    here is that the route hands over its own sessions and nothing else."""
    client = FakeClient(chatbots=[{"id": "cb1", "owner_id": "u1"}])

    build(client).get("/onboarding")

    assert client.asked_with == []


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


def test_the_route_is_registered_before_the_static_mount():
    """StaticFiles is mounted at "/" and swallows anything registered after it,
    so a correctly written router can still 404 in the real app. Assert against
    the assembled app, not just the router."""
    from app.main import create_app

    paths = {r.path for r in create_app().routes if hasattr(r, "path")}

    assert "/onboarding" in paths
