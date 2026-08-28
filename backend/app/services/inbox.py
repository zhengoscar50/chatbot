"""Folding a chatbot's shared sessions into a readable list.

Every visitor session is created with the same DEFAULT_NAME, so a list built
from session rows alone is a column of identical names separated by a
timestamp. What makes a row identifiable is the visitor's first question, and
that lives in the messages table — so the two are fetched separately and
folded here.

Kept pure, and separate from the client that fetches its inputs: the
interesting cases (nobody typed, questions out of order, two sessions
interleaved in one batched response) are then exercised without a fake
database standing in the way.
"""

# Long enough to recognise a question, short enough that one rambling visitor
# does not push the rest of the list off the screen.
PREVIEW_CHARS = 160


def _preview(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS].rstrip() + "…"


def conversations(session_rows: list, message_rows: list) -> list:
    """Shared sessions as inbox rows, newest activity first.

    `message_rows` is the flat result of ONE batched query covering every
    session in `session_rows`, so grouping happens here rather than in the
    database. Ordering within a session is recomputed from `created_at` rather
    than trusted from the query: a preview that changes when PostgREST returns
    rows in a different order is a bug that only appears once a conversation
    has a second question in it.
    """
    grouped: dict = {}
    for m in message_rows or []:
        grouped.setdefault(m.get("session_id"), []).append(m)

    rows = []
    for s in session_rows or []:
        mine = sorted(grouped.get(s["id"], []), key=lambda m: m.get("created_at") or "")
        first_question = next(
            (m for m in mine if m.get("role") == "user"), None
        )
        # A session with no messages still belongs here: the widget creates one
        # when it opens, before anyone types, so these rows are every visitor
        # who looked and left. Falling back to the session's own timestamp
        # gives them something to sort by.
        last_at = mine[-1].get("created_at") if mine else s.get("updated_at")
        rows.append({
            "id": s["id"],
            "preview": _preview(first_question.get("text")) if first_question else "",
            "message_count": len(mine),
            "last_message_at": last_at,
        })

    rows.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    return rows
