from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.chatbots import router
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


def build_app(bots, share=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "o1", "username": "alice"}
    app.dependency_overrides[get_chatbot_service] = lambda: bots
    app.dependency_overrides[get_agent_service] = lambda: SimpleNamespace()
    app.dependency_overrides[get_session_service] = lambda: SimpleNamespace()
    app.dependency_overrides[get_share_service] = lambda: share or FakeShare()
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


def test_reading_another_users_share_state_is_not_found(client, auth, other_chatbot):
    res = client.get(f"/chatbots/{other_chatbot}/share", headers=auth)
    assert res.status_code == 404
