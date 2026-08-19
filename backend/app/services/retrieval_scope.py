from __future__ import annotations


def kb_ids_for(agent_row, session_row, scratch_kb_id=None) -> list:
    """Knowledge bases in scope for one question, in retrieval order.

    Entries are either a bare KB id (search all of it) or a dict
    ``{"id", "source_ids"}`` (search only those documents). The dict form is
    how ONE shared scratch KB serves every chat: a chat sees its own uploads
    and no others, because retrieval is restricted to the source ids recorded
    on that chat's row.

    With a specialist: its permanent KBs, then this chat's scratch documents.

    With ``agent_row=None`` the general assistant is answering: it sees this
    chat's scratch documents and never a specialist's permanent KBs — that
    would leak one agent's documents into an answer the UI attributes to
    another.

    A chat that has uploaded nothing contributes NO scratch entry. Emitting
    the shared KB without source_ids would make every other chat's uploads
    answerable here, which is the one failure this design must not have.

    ``session_row.kb_id`` is the legacy per-chat KB. Chats created before the
    shared KB keep theirs and are searched as a bare id; both forms coexist so
    live user data never needed migrating.

    Falsy ids are dropped, so an untrained agent with no uploads yields [] and
    answers from the model, which is correct rather than a failure.

    ``session_row`` and ``scratch_kb_id`` may be None.
    """
    ids: list = []
    if agent_row:
        ids.extend([agent_row.get("kb_id"), agent_row.get("kb_full_id")])
    if session_row:
        ids.append(session_row.get("kb_id"))          # legacy per-chat KB
        source_ids = session_row.get("source_ids") or []
        if scratch_kb_id and source_ids:
            ids.append({"id": scratch_kb_id, "source_ids": list(source_ids)})

    out: list = []
    for entry in ids:
        if not entry:
            continue
        if isinstance(entry, dict):
            out.append(entry)
        elif entry not in out:
            out.append(entry)
    return out
