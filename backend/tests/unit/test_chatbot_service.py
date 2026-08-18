from app.services.chatbot_service import ChatbotService, LastChatbotError


# Module-level list to track deletion order across fakes
_deletion_order = []


class FakeClient:
    def __init__(self, rows=None, deletion_order=None):
        self.rows = list(rows or [])
        self.updated = []
        self.deleted = []
        self._n = 0
        self.deletion_order = deletion_order if deletion_order is not None else _deletion_order

    def insert_chatbot_row(self, row):
        self._n += 1
        row = dict(row, id="cb-%d" % self._n)
        self.rows.append(row)
        return row

    def list_chatbot_rows(self, owner_id):
        return [r for r in self.rows if r["owner_id"] == owner_id]

    def get_chatbot_row(self, chatbot_id):
        return next((r for r in self.rows if r["id"] == chatbot_id), None)

    def update_chatbot_row(self, chatbot_id, fields):
        self.updated.append((chatbot_id, fields))

    def delete_chatbot_row(self, chatbot_id):
        self.deleted.append(chatbot_id)
        self.deletion_order.append(("chatbot", chatbot_id))
        self.rows = [r for r in self.rows if r["id"] != chatbot_id]


class FakeAgents:
    def __init__(self, ids=(), deletion_order=None):
        self.ids = list(ids)
        self.deleted = []
        self.deletion_order = deletion_order if deletion_order is not None else _deletion_order

    def list(self, chatbot_id):
        return [{"id": i} for i in self.ids]

    def delete(self, agent_id):
        self.deleted.append(agent_id)
        self.deletion_order.append(("agent", agent_id))
        return True


class FakeSessions:
    def __init__(self, ids=(), deletion_order=None):
        self.ids = list(ids)
        self.deleted = []
        self.deletion_order = deletion_order if deletion_order is not None else _deletion_order

    def list(self, chatbot_id):
        return [{"id": i} for i in self.ids]

    def delete(self, session_id):
        self.deleted.append(session_id)
        self.deletion_order.append(("session", session_id))
        return True


def test_create_returns_the_new_chatbot():
    c = FakeClient()
    row = ChatbotService(c).create("o1", "Work", "work things")
    assert row["owner_id"] == "o1"
    assert row["name"] == "Work"
    assert row["description"] == "work things"


def test_get_owned_refuses_another_users_chatbot():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "someone-else", "name": "X"}])
    assert ChatbotService(c).get_owned("cb-1", "o1") is None


def test_get_owned_returns_your_own():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "X"}])
    assert ChatbotService(c).get_owned("cb-1", "o1")["id"] == "cb-1"


def test_delete_removes_its_agents_and_chats():
    """Agents and sessions are deleted before the chatbot row."""
    deletion_order = []
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "A"},
                         {"id": "cb-2", "owner_id": "o1", "name": "B"}],
                   deletion_order=deletion_order)
    agents = FakeAgents(["ag-1"], deletion_order=deletion_order)
    sessions = FakeSessions(["s-1"], deletion_order=deletion_order)

    assert ChatbotService(c).delete("cb-1", "o1", agents, sessions) is True

    assert agents.deleted == ["ag-1"]
    assert sessions.deleted == ["s-1"]
    assert c.deleted == ["cb-1"]
    # Verify chatbot is deleted last: agents and sessions come before it
    assert deletion_order.count(("agent", "ag-1")) == 1
    assert deletion_order.count(("session", "s-1")) == 1
    assert deletion_order.count(("chatbot", "cb-1")) == 1
    chatbot_idx = deletion_order.index(("chatbot", "cb-1"))
    agent_idx = deletion_order.index(("agent", "ag-1"))
    session_idx = deletion_order.index(("session", "s-1"))
    assert agent_idx < chatbot_idx
    assert session_idx < chatbot_idx


def test_the_last_chatbot_cannot_be_deleted():
    """Otherwise agents have nowhere to live and the next login shows nothing."""
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "Only"}])
    try:
        ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions())
        assert False, "expected LastChatbotError"
    except LastChatbotError:
        pass
    assert c.deleted == []


def test_deleting_someone_elses_chatbot_reports_not_found():
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "other", "name": "A"}])
    assert ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions()) is False


def test_last_chatbot_count_is_owner_scoped_allows_delete():
    """Deleting succeeds when owner has 2, even though another owner exists."""
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "First"},
                         {"id": "cb-2", "owner_id": "o1", "name": "Second"},
                         {"id": "cb-3", "owner_id": "other", "name": "OtherOwner"}])
    # o1 has 2 chatbots, so deleting one should succeed
    assert ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions()) is True
    assert c.deleted == ["cb-1"]


def test_last_chatbot_count_is_owner_scoped_prevents_delete():
    """LastChatbotError fires when owner has 1, even though other owners exist."""
    c = FakeClient(rows=[{"id": "cb-1", "owner_id": "o1", "name": "OnlyOne"},
                         {"id": "cb-2", "owner_id": "other", "name": "A"},
                         {"id": "cb-3", "owner_id": "other", "name": "B"}])
    # o1 has 1 chatbot, so deleting it should raise LastChatbotError
    try:
        ChatbotService(c).delete("cb-1", "o1", FakeAgents(), FakeSessions())
        assert False, "expected LastChatbotError"
    except LastChatbotError:
        pass
    assert c.deleted == []
