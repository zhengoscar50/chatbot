from app.services.gate_service import GateService


class FakeClient:
    def __init__(self, content=None, raises=None):
        self.content = content
        self.raises = raises
        self.calls = []

    def run_agent_sync(self, agent_id, message, response_format=None):
        self.calls.append({"agent_id": agent_id, "message": message, "response_format": response_format})
        if self.raises:
            raise self.raises
        return {"content": self.content}


def test_needs_kb_true_when_gate_says_true():
    client = FakeClient(content='{"needs_kb": true}')
    assert GateService(client, "router-1").needs_kb("what does the doc say?") is True
    assert client.calls[0]["agent_id"] == "router-1"
    assert client.calls[0]["response_format"] is not None


def test_needs_kb_false_when_gate_says_false():
    client = FakeClient(content='{"needs_kb": false}')
    assert GateService(client, "router-1").needs_kb("hello there") is False


def test_needs_kb_fails_safe_to_true_on_unparseable_content():
    client = FakeClient(content="not json")
    assert GateService(client, "router-1").needs_kb("hi") is True


def test_needs_kb_fails_safe_to_true_on_client_error():
    client = FakeClient(raises=RuntimeError("boom"))
    assert GateService(client, "router-1").needs_kb("hi") is True


def test_needs_kb_includes_history_in_message():
    client = FakeClient(content='{"needs_kb": true}')
    GateService(client, "router-1").needs_kb(
        "and the second one?",
        history=[{"role": "user", "text": "what is clause 1?"},
                 {"role": "assistant", "text": "Clause 1 says X."}],
    )
    msg = client.calls[0]["message"]
    assert "what is clause 1?" in msg
    assert "and the second one?" in msg
