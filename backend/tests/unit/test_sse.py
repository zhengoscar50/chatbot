from app.clients.sse import parse_sse


def test_parse_sse_multiple_events():
    text = (
        "event: start\n"
        'data: {"session_id": "sess-1"}\n'
        "\n"
        "event: complete\n"
        'data: {"answer": "hi"}\n'
        "\n"
    )

    events = parse_sse(text)

    assert events == [
        {"event": "start", "data": {"session_id": "sess-1"}},
        {"event": "complete", "data": {"answer": "hi"}},
    ]


def test_parse_sse_defaults_event_name_to_message():
    text = 'data: {"value": 1}\n\n'

    events = parse_sse(text)

    assert events == [{"event": "message", "data": {"value": 1}}]


def test_parse_sse_non_json_payload_falls_back_to_raw():
    text = "event: ping\ndata: not-json\n\n"

    events = parse_sse(text)

    assert events == [{"event": "ping", "data": {"raw": "not-json"}}]
