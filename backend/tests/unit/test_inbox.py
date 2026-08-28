from app.services.inbox import conversations


def session(sid, updated="2026-08-27T10:00:00Z"):
    return {"id": sid, "name": "New chat", "updated_at": updated}


def msg(sid, role, text, at):
    return {"session_id": sid, "role": role, "text": text, "created_at": at}


def test_the_preview_is_the_visitors_first_question():
    """Every visitor session is created with the same DEFAULT_NAME, so the
    preview is the only thing distinguishing one row from another. It has to
    come from the first USER turn: a greeting the bot opens with would be
    identical on every row and reintroduce exactly the problem."""
    rows = conversations(
        [session("s1")],
        [msg("s1", "assistant", "Hi! How can I help?", "2026-08-27T10:00:00Z"),
         msg("s1", "user", "do you ship to canada", "2026-08-27T10:00:01Z"),
         msg("s1", "user", "and how long does it take", "2026-08-27T10:00:09Z")],
    )

    assert rows[0]["preview"] == "do you ship to canada"


def test_a_session_where_nobody_typed_is_still_listed():
    """The widget creates its session when it OPENS, before anyone types. Those
    rows are the common case, not an edge case — dropping them would silently
    hide every visitor who looked and left, which is a real thing an owner
    wants to see."""
    rows = conversations([session("s1")], [])

    assert [r["id"] for r in rows] == ["s1"]
    assert rows[0]["preview"] == ""
    assert rows[0]["message_count"] == 0


def test_the_count_covers_both_sides_of_the_conversation():
    rows = conversations(
        [session("s1")],
        [msg("s1", "user", "hello", "2026-08-27T10:00:00Z"),
         msg("s1", "assistant", "hi", "2026-08-27T10:00:02Z")],
    )

    assert rows[0]["message_count"] == 2


def test_messages_are_grouped_to_the_session_they_belong_to():
    """One batched query returns every session's messages interleaved, so the
    fold has to key on session_id. Getting this wrong shows one visitor's
    question on another visitor's row."""
    rows = conversations(
        [session("s1"), session("s2")],
        [msg("s2", "user", "question from two", "2026-08-27T10:00:00Z"),
         msg("s1", "user", "question from one", "2026-08-27T10:00:01Z")],
    )

    by_id = {r["id"]: r for r in rows}
    assert by_id["s1"]["preview"] == "question from one"
    assert by_id["s2"]["preview"] == "question from two"


def test_the_first_question_wins_even_when_rows_arrive_out_of_order():
    """PostgREST is asked for created_at.asc, but the fold must not depend on
    it — a preview that changes with row order is a bug that only shows up
    once a conversation has two questions in it."""
    rows = conversations(
        [session("s1")],
        [msg("s1", "user", "second", "2026-08-27T10:05:00Z"),
         msg("s1", "user", "first", "2026-08-27T10:00:00Z")],
    )

    assert rows[0]["preview"] == "first"


def test_last_activity_is_the_newest_message_not_the_session_row():
    rows = conversations(
        [session("s1", updated="2026-08-27T09:00:00Z")],
        [msg("s1", "user", "hello", "2026-08-27T10:00:00Z"),
         msg("s1", "assistant", "hi", "2026-08-27T10:30:00Z")],
    )

    assert rows[0]["last_message_at"] == "2026-08-27T10:30:00Z"


def test_a_silent_session_falls_back_to_when_it_was_created():
    """With no messages there is no newest message, but the row still needs a
    time to sort and display by."""
    rows = conversations([session("s1", updated="2026-08-27T09:00:00Z")], [])

    assert rows[0]["last_message_at"] == "2026-08-27T09:00:00Z"


def test_conversations_are_ordered_by_most_recent_activity():
    """A session whose row is stale but whose messages are fresh must sort
    above one that has been quiet, so ordering follows the folded time rather
    than the order the session rows arrived in."""
    rows = conversations(
        [session("quiet", updated="2026-08-27T09:00:00Z"),
         session("busy", updated="2026-08-27T08:00:00Z")],
        [msg("busy", "user", "recent", "2026-08-27T11:00:00Z")],
    )

    assert [r["id"] for r in rows] == ["busy", "quiet"]


def test_a_long_question_is_trimmed_for_the_list():
    rows = conversations(
        [session("s1")],
        [msg("s1", "user", "x" * 400, "2026-08-27T10:00:00Z")],
    )

    assert len(rows[0]["preview"]) < 200


def test_no_sessions_means_no_rows():
    assert conversations([], []) == []
