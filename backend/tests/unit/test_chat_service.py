import pytest

from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)


class FakeGate:
    def __init__(self, needs=True):
        self.needs = needs
        self.calls = []

    def needs_kb(self, query, history=None):
        self.calls.append((query, history))
        return self.needs


class FakeClient:
    def __init__(self, events, handler_id="handler-1"):
        self.events = events
        self.handler_id = handler_id
        self.calls = []
        self.handler_calls = []

    def create_context_handler(self, query, knowledge_bases, max_context_tokens=None):
        self.handler_calls.append({"query": query, "knowledge_bases": knowledge_bases,
                                   "max_context_tokens": max_context_tokens})
        return {"id": self.handler_id}

    def run_agent(self, agent_id, message, session_id=None, citations_enabled=True, context_handler_id=None):
        self.calls.append({"agent_id": agent_id, "message": message, "session_id": session_id,
                           "context_handler_id": context_handler_id})
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
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(RuntimeError, match="insufficient OpenRouter credits"):
        service.ask("hello")


def test_ask_raises_model_busy_on_resource_exhausted():
    # Real observed Nvidia/OpenRouter overload error (surfaced as an "error"
    # event carrying the raw provider text under the "error" key).
    client = FakeClient(
        events=[
            {
                "event": "error",
                "data": {
                    "error": (
                        "Stream iteration failed: litellm.MidStreamFallbackError: "
                        "OpenrouterException - Upstream error from Nvidia: "
                        "ResourceExhausted: Worker local total request limit reached "
                        "(100/32), Metadata: {'error_type': 'provider_unavailable'}"
                    )
                },
            }
        ]
    )
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(ModelBusyError):
        service.ask("hi")


def test_ask_raises_model_busy_on_rate_limit_in_complete_event():
    client = FakeClient(
        events=[
            {"event": "start", "data": {"session_id": "s"}},
            {
                "event": "complete",
                "data": {
                    "content": "",
                    "status": "failed",
                    "error": "litellm.RateLimitError: OpenrouterException code 429 rate_limit_exceeded",
                },
            },
        ]
    )
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(ModelBusyError):
        service.ask("hi")


def test_ask_non_throttle_error_uses_error_field_not_dict_repr():
    client = FakeClient(
        events=[{"event": "error", "data": {"error": "some unexpected failure"}}]
    )
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(RuntimeError) as exc:
        service.ask("hi")

    msg = str(exc.value)
    assert "some unexpected failure" in msg
    assert "'event'" not in msg  # not the raw dict repr


def test_ask_retrieves_and_injects_when_gate_true():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "grounded", "citations": [{"source_id": "s"}]}},
    ])
    service = ChatService(client, "agent-1", FakeGate(needs=True), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("what does the doc say?", session_id="ps-1")

    assert result["answer"] == "grounded"
    assert client.handler_calls[0]["knowledge_bases"] == [{"id": "kb-s", "top_k": 4}, {"id": "gkb-1", "top_k": 4}]
    assert client.calls[0]["context_handler_id"] == "handler-1"


def test_ask_skips_retrieval_when_gate_false():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "hi there", "citations": []}},
    ])
    service = ChatService(client, "agent-1", FakeGate(needs=False), ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("hello")

    assert result["answer"] == "hi there"
    assert client.handler_calls == []                      # no retrieval
    assert client.calls[0]["context_handler_id"] is None   # no injection
