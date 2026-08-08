from __future__ import annotations


def kb_ids_for(agent_row, session_row, general_kb_id) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    With a specialist: its permanent KBs (the curated tier), then this chat's
    scratch KB, then the shared general KB if the agent opted in.

    With ``agent_row=None`` the general assistant is answering: it sees this
    chat's scratch KB and the general KB, and never a specialist's permanent
    KBs — that would leak one agent's documents into an answer the UI
    attributes to another.

    Falsy ids are dropped, so an untrained agent with no uploads yields [] and
    answers from the model, which is correct rather than a failure.

    ``session_row`` and ``general_kb_id`` may be None.
    """
    ids: list = []
    if agent_row:
        ids.extend([agent_row.get("kb_id"), agent_row.get("kb_full_id")])
    if session_row:
        ids.append(session_row.get("kb_id"))
    if agent_row is None or agent_row.get("use_general_kb"):
        ids.append(general_kb_id)

    out: list = []
    for kb_id in ids:
        if kb_id and kb_id not in out:
            out.append(kb_id)
    return out
