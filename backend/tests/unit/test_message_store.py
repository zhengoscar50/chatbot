from app.services.conversation import conversation_message
from app.services.message_store import MessageStore


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserted = []

    def insert_message(self, row):
        self.inserted.append(row)
        self.rows.append(row)
        return row

    def list_messages(self, session_id):
        return [r for r in self.rows if r.get("session_id") == session_id]


def rows(*pairs):
    return [{"session_id": "s1", "role": r, "content": t} for r, t in pairs]


# --- store ------------------------------------------------------------------

def test_user_turn_is_recorded():
    c = FakeClient()
    MessageStore(c).add_user_turn("s1", "hello")
    assert c.inserted[0]["role"] == "user"
    assert c.inserted[0]["content"] == "hello"


def test_assistant_turn_records_citations_and_attribution():
    c = FakeClient()
    MessageStore(c).add_assistant_turn(
        "s1", "42", [{"key": "1"}], answered_by_id="ag-1", answered_by_name="Chem tutor"
    )
    row = c.inserted[0]
    assert row["role"] == "assistant"
    assert row["citations"] == [{"key": "1"}]
    assert row["answered_by_id"] == "ag-1"
    assert row["answered_by_name"] == "Chem tutor"


def test_general_assistant_turn_has_no_agent_id():
    c = FakeClient()
    MessageStore(c).add_assistant_turn("s1", "hi", [], answered_by_name="General assistant")
    assert c.inserted[0]["answered_by_id"] is None


def test_recent_turns_returns_the_last_exchanges_oldest_first():
    c = FakeClient(rows(("user", "one"), ("assistant", "two"),
                        ("user", "three"), ("assistant", "four")))
    assert MessageStore(c).recent_turns("s1", 1) == [
        {"role": "user", "text": "three"},
        {"role": "assistant", "text": "four"},
    ]


def test_recent_turns_of_an_empty_chat():
    assert MessageStore(FakeClient()).recent_turns("s1", 2) == []


def test_recent_turns_of_zero_skips_the_read():
    c = FakeClient(rows(("user", "one")))
    assert MessageStore(c).recent_turns("s1", 0) == []


def test_transcript_is_scoped_to_the_chat():
    c = FakeClient(rows(("user", "mine")) + [
        {"session_id": "other", "role": "user", "content": "theirs"},
    ])
    assert [m["content"] for m in MessageStore(c).transcript("s1")] == ["mine"]


# --- conversation composition ----------------------------------------------

def test_no_history_sends_the_query_alone():
    assert conversation_message([], "what is a mole?") == "what is a mole?"


def test_history_is_inlined_above_the_current_message():
    message = conversation_message(
        [{"role": "user", "text": "where is the eyewash?"},
         {"role": "assistant", "text": "Corridor Seven."}],
        "say that again",
    )
    assert "where is the eyewash?" in message
    assert "Corridor Seven." in message
    assert message.rstrip().endswith("Current message: say that again")
