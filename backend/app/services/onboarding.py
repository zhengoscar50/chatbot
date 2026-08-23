"""The dashboard's getting-started checklist, derived from the account's data.

Nothing here is stored. Every step is recomputed from live rows on each
request, so deleting your only agent un-ticks its step rather than leaving a
tick for something that no longer exists — the panel is most useful exactly
when an account has gone backwards, which is when a stored flag would lie.

This module is pure on purpose: rows in, steps out. The route does the I/O.
"""
from __future__ import annotations

STEP_IDS = ("chatbot", "agent", "description", "knowledge", "answer")

# The server owns this copy so the panel is not a second place it can drift.
_COPY = {
    "chatbot": (
        "Create a chatbot",
        "A chatbot holds your agents, its knowledge, and its chats.",
    ),
    "agent": (
        "Add a specialist agent",
        "Agents are the specialists your questions get routed to.",
    ),
    "description": (
        "Give it a description",
        "Routing matches your question against each agent's description. "
        "An agent without one is never chosen.",
    ),
    "knowledge": (
        "Train it on a document",
        "Upload to an agent, or to the chatbot so every agent can read it.",
    ),
    "answer": (
        "Ask a question it can answer",
        "Ask something your document covers, and watch a specialist answer.",
    ),
}


def _text(row: dict, key: str) -> str:
    """A trimmed string for `key`, treating a missing or null column as empty."""
    return (row.get(key) or "").strip()


def _has_knowledge(row: dict) -> bool:
    """Either kind of knowledge base counts as trained.

    Chunked retrieval and full-document retrieval store separate ids, and an
    agent set up with only one of them is still trained.
    """
    return bool(_text(row, "kb_id") or _text(row, "kb_full_id"))


def derive_steps(chatbots: list, agents: list, has_answer: bool) -> list[dict]:
    """The five steps for one account, each ticked from the rows it owns.

    `has_answer` is passed in rather than derived from rows because it comes
    from a query the caller may legitimately skip: an account with no sessions
    cannot have an answer, and that is the account most likely to be looking
    at this panel.
    """
    done = {
        "chatbot": bool(chatbots),
        "agent": bool(agents),
        "description": any(_text(a, "description") for a in agents),
        "knowledge": (any(_has_knowledge(a) for a in agents)
                      or any(_has_knowledge(c) for c in chatbots)),
        "answer": bool(has_answer),
    }
    return [
        {"id": sid, "label": _COPY[sid][0], "hint": _COPY[sid][1], "done": done[sid]}
        for sid in STEP_IDS
    ]
