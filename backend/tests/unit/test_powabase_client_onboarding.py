from app.clients.powabase_client import PowabaseClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        return FakeResponse(self.payload)


def build(payload):
    client = PowabaseClient.__new__(PowabaseClient)
    http = FakeHTTP(payload)
    client._client = http
    return client, http


def test_no_sessions_asks_nothing_at_all():
    """An account with no chats cannot have an answer. The account most likely
    to be staring at this panel is exactly that one, so the common case must
    not cost a round trip."""
    client, http = build([])

    assert client.has_specialist_answer([]) is False
    assert http.calls == []


def test_a_returned_row_means_a_specialist_answered():
    client, http = build([{"id": "m1"}])

    assert client.has_specialist_answer(["s1", "s2"]) is True


def test_no_rows_means_no_specialist_answer():
    client, _ = build([])

    assert client.has_specialist_answer(["s1"]) is False


def test_every_session_is_asked_for_in_one_request():
    """One `in.()` filter, not a request per session. A user with forty chats
    must not produce forty round trips on every dashboard load."""
    client, http = build([])

    client.has_specialist_answer(["s1", "s2", "s3"])

    assert len(http.calls) == 1
    path, params = http.calls[0]
    assert path == "/rest/v1/messages"
    assert params["session_id"] == "in.(s1,s2,s3)"


def test_the_query_filters_to_rows_a_specialist_answered():
    """The filter must be on the server. Fetching every message and checking in
    Python would work on the fixture and fall over on a real transcript."""
    client, http = build([])

    client.has_specialist_answer(["s1"])

    _, params = http.calls[0]
    assert params["answered_by_id"] == "not.is.null"


def test_only_one_row_is_ever_fetched():
    """The answer is a yes/no. Pulling a whole transcript to compute a boolean
    is the kind of thing that is invisible until someone has 5,000 messages."""
    client, http = build([])

    client.has_specialist_answer(["s1"])

    _, params = http.calls[0]
    assert params["limit"] == 1
