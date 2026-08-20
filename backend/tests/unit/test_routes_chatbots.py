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
