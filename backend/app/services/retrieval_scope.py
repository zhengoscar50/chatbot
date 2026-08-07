from __future__ import annotations


def kb_ids_for(agent_row: dict, session_row, general_kb_id) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    The agent's permanent KBs first (the curated tier), then this chat's scratch
    KB, then the shared general KB if the agent opted in. Falsy ids are dropped,
    so an untrained agent with no uploads yields [] — it answers from the model,
    which is correct rather than a failure.

    ``session_row`` and ``general_kb_id`` may be None.
    """
    ids = [agent_row.get("kb_id"), agent_row.get("kb_full_id")]
    if session_row:
        ids.append(session_row.get("kb_id"))
    if agent_row.get("use_general_kb"):
        ids.append(general_kb_id)

    out: list = []
    for kb_id in ids:
        if kb_id and kb_id not in out:
            out.append(kb_id)
    return out
