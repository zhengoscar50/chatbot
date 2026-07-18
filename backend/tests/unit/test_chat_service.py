import pytest

from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ProviderKeyError,
)


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def run_agent(self, agent_id, message, session_id=None, citations_enabled=True):
        self.calls.append({"agent_id": agent_id, "message": message, "session_id": session_id})
        return self.events


def test_ask_returns_answer_and_session_id():
    client = FakeClient(
        events=[
            {"event": "start", "data": {"session_id": "sess-1"}},
            {
                "event": "complete",
                "data": {"answer": "42", "citations": [{"source_id": "src-1"}]},
            },
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    result = service.ask("What is the answer?")

    assert result == {
        "answer": "42",
        "session_id": "sess-1",
        "citations": [{"source_id": "src-1"}],
    }
    assert client.calls[0]["agent_id"] == "agent-1"


def test_ask_raises_insufficient_credits():
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {"error": "insufficient_credits", "message": "no credits"},
            }
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(InsufficientCreditsError):
        service.ask("hello")


def test_ask_raises_provider_key_error():
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {"error": "provider_key_decrypt_failed", "message": "bad key"},
            }
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(ProviderKeyError):
        service.ask("hello")
