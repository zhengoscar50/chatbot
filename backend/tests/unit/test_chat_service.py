import pytest

from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)


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

    def run_agent(self, agent_id, message, session_id=None, citations_enabled=True,
                  context_handler_id=None, runtime_knowledge_bases=None):
        self.calls.append({"agent_id": agent_id, "message": message, "session_id": session_id,
                           "context_handler_id": context_handler_id,
                           "runtime_knowledge_bases": runtime_knowledge_bases})
        return self.events


def test_ask_returns_the_answer():
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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("What is the answer?")

    assert result == {
        "answer": "42",
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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

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
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(ModelBusyError):
        service.ask("hi")


def test_ask_non_throttle_error_uses_error_field_not_dict_repr():
    client = FakeClient(
        events=[{"event": "error", "data": {"error": "some unexpected failure"}}]
    )
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    with pytest.raises(RuntimeError) as exc:
        service.ask("hi")

    msg = str(exc.value)
    assert "some unexpected failure" in msg
    assert "'event'" not in msg  # not the raw dict repr


def test_ask_retrieves_when_the_decision_says_to():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "grounded", "citations": [{"source_id": "s"}]}},
    ])
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("what does the doc say?", retrieve=True)

    assert result["answer"] == "grounded"
    # Attached to the run itself rather than pre-built into a context handler.
    assert client.calls[0]["runtime_knowledge_bases"] == [
        {"id": "kb-s", "top_k": 4}, {"id": "gkb-1", "top_k": 4}
    ]
    assert client.calls[0]["context_handler_id"] is None
    assert client.handler_calls == []


def test_ask_skips_retrieval_when_the_decision_says_not_to():
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "hi there", "citations": []}},
    ])
    service = ChatService(client, "agent-1", ["kb-s", "gkb-1"], top_k=4, max_context_tokens=2000)

    result = service.ask("hello", retrieve=False)

    assert result["answer"] == "hi there"
    assert client.handler_calls == []                          # no retrieval
    assert client.calls[0]["runtime_knowledge_bases"] is None  # no search tool


def test_ask_skips_retrieval_when_the_agent_has_no_knowledge_bases():
    # A brand-new agent: untrained, no chat uploads, general knowledge off.
    # Sending an empty knowledge_bases list makes Powabase 400 with
    # "knowledge_bases is required and must not be empty", so the agent must
    # answer from the model instead.
    client = FakeClient(events=[
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"content": "I'm an assistant.", "citations": []}},
    ])
    service = ChatService(client, "agent-1", [], top_k=4, max_context_tokens=2000)

    result = service.ask("what are you?", retrieve=True)

    assert result["answer"] == "I'm an assistant."
    assert client.handler_calls == []
    assert client.calls[0]["runtime_knowledge_bases"] is None


def test_ask_skips_retrieval_when_every_kb_id_is_falsy():
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "ok", "citations": []}},
    ])
    service = ChatService(client, "agent-1", [None, "", None], top_k=4, max_context_tokens=2000)

    service.ask("hello", retrieve=True)

    assert client.handler_calls == []


def test_retrieval_searches_the_question_not_the_conversation():
    # History is inlined into the agent's message since Powabase threads are
    # single-agent. If that whole blob is also used as the retrieval query, the
    # question gets drowned in transcript and retrieval degrades as the
    # conversation grows — which is exactly how a follow-up started missing
    # documents an identical fresh question found.
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "ok", "citations": []}},
    ])
    service = ChatService(client, "agent-1", ["kb-1"], top_k=4, max_context_tokens=2000)

    service.ask(
        "What is the mascot?",
        message="Recent conversation:\nuser: hi\nassistant: hello\n\nCurrent message: What is the mascot?",
        retrieve=True,
    )

    # There is no separate retrieval query any more. The agent gets a
    # knowledge_search tool and formulates its own search terms from the
    # message, so history is context it reads rather than a string blindly
    # searched. This is the property 29a62fc protected, now delegated to the
    # model instead of enforced here — see the live check in
    # scripts/check_history_retrieval.py.
    assert client.handler_calls == []
    assert client.calls[0]["runtime_knowledge_bases"] == [{"id": "kb-1", "top_k": 4}]
    # ...while the agent still sees the history it needs for a follow-up.
    assert "assistant: hello" in client.calls[0]["message"]


def test_message_defaults_to_the_query():
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "ok", "citations": []}},
    ])
    service = ChatService(client, "agent-1", ["kb-1"], top_k=4, max_context_tokens=2000)

    service.ask("plain question")

    assert client.calls[0]["message"] == "plain question"


def test_ask_scopes_a_kb_to_named_sources():
    """Per-chat scratch isolation rides on source_ids.

    With one shared scratch KB, isolation stops being structural (a KB per
    chat) and becomes a filter parameter. If a scope entry ever loses its
    source_ids, one chat's uploads become answerable in another — so the
    scoping must survive verbatim into the run.
    """
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "ok", "citations": []}},
    ])
    service = ChatService(
        client, "agent-1",
        [{"id": "scratch-shared", "source_ids": ["src-a"]}, "kb-perm"],
        top_k=4, max_context_tokens=2000,
    )

    service.ask("what does my upload say?", retrieve=True)

    assert client.calls[0]["runtime_knowledge_bases"] == [
        {"id": "scratch-shared", "top_k": 4, "source_ids": ["src-a"]},
        {"id": "kb-perm", "top_k": 4},
    ]


def test_ask_drops_a_scope_entry_with_no_sources():
    """An empty source list would widen scope to the WHOLE shared KB — every
    other chat's uploads. Drop the entry instead."""
    client = FakeClient(events=[
        {"event": "complete", "data": {"content": "ok", "citations": []}},
    ])
    service = ChatService(
        client, "agent-1",
        [{"id": "scratch-shared", "source_ids": []}, "kb-perm"],
        top_k=4, max_context_tokens=2000,
    )

    service.ask("anything?", retrieve=True)

    assert client.calls[0]["runtime_knowledge_bases"] == [{"id": "kb-perm", "top_k": 4}]
