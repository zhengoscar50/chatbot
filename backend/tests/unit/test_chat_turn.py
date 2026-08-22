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
