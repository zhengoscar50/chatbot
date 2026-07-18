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
    # Real Powabase "complete" events carry the final text under "content",
    # not "answer" (see references/streaming-sse.md).
    client = FakeClient(
        events=[
            {"event": "start", "data": {"session_id": "sess-1"}},
            {
                "event": "complete",
                "data": {"content": "42", "citations": [{"source_id": "src-1"}]},
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
    # Standalone "error" events carry {message, code} per the docs.
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {"code": "insufficient_credits", "message": "no credits"},
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
                "data": {"code": "provider_key_decrypt_failed", "message": "bad key"},
            }
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(ProviderKeyError):
        service.ask("hello")


def test_ask_raises_runtime_error_when_complete_event_reports_failure():
    # Real-world observed shape: a "complete" event can carry status:"failed"
    # plus a raw provider error string in "error" (e.g. a downstream LLM
    # provider rejecting the call) — this is not one of the documented
    # standalone "error" event codes, so it surfaces as a generic failure.
    client = FakeClient(
        events=[
            {"event": "start", "data": {"session_id": "sess-1"}},
            {
                "event": "complete",
                "data": {
                    "content": "",
                    "status": "failed",
                    "error": "litellm.APIError: insufficient OpenRouter credits",
                },
            },
        ]
    )
    service = ChatService(client, agent_id="agent-1")

    with pytest.raises(RuntimeError, match="insufficient OpenRouter credits"):
        service.ask("hello")
