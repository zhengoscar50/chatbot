from __future__ import annotations

from app.clients.powabase_client import PowabaseAPIError
from app.core.security import hash_password
from app.models.schemas import validate_username


class UsernameTakenError(Exception):
    pass


def list_users_with_counts(client) -> list:
    counts: dict = {}
    for s in client.list_all_sessions():
        owner = s.get("owner_id")
        if owner:
            counts[owner] = counts.get(owner, 0) + 1
    return [
        {
            "id": u["id"],
            "username": u["username"],
            "created_at": u.get("created_at"),
            "session_count": counts.get(u["id"], 0),
        }
        for u in client.list_users()
    ]


def delete_user(client, session_service, agent_service, user_id: str) -> bool:
    """Delete a user and everything they own: their chats, agents, and chatbots.

    Enumeration MUST be owner-scoped (list_sessions_by_owner /
    list_agent_rows_by_owner), not chatbot-scoped (list_sessions /
    agent_service.list) — the latter take a chatbot id, so passing a user id
    matches no chatbot and silently finds nothing. That is exactly the bug
    this function was already fixed for once: skipping the agents (as this
    did before agents became user-owned) stranded them with no owner —
    unreachable through the API, yet still holding their knowledge bases and
    Powabase agents indefinitely. Do not let it come back.

    All three cascades are best-effort — the user row delete is authoritative,
    and a stale remote resource must not leave the account half-removed.
    """
    if client.get_user(user_id) is None:
        return False

    for session in client.list_sessions_by_owner(user_id):
        try:
            session_service.delete(session["id"])   # scratch KB + row
        except PowabaseAPIError:
            pass

    for agent in client.list_agent_rows_by_owner(user_id):
        try:
            agent_service.delete(agent["id"])       # permanent KBs + remote agent + row
        except PowabaseAPIError:
            pass

    # Their chatbots are empty now; remove them so no rows outlive the account.
    #
    # Delete each chatbot's OWN knowledge bases first. Since phase 2 a chatbot
    # holds two tiers of its own, and dropping only the row leaves both alive in
    # Powabase with nothing referencing them — the same stranding described
    # above, in a newer form. Sources are never deleted, only the bases:
    # upload_source deduplicates identical content, so a Source may belong to
    # another user entirely.
    for chatbot in client.list_chatbot_rows(user_id):
        for kb_id in (chatbot.get("kb_id"), chatbot.get("kb_full_id")):
            if not kb_id:
                continue
            try:
                client.delete_knowledge_base(kb_id)
            except PowabaseAPIError:
                pass
        try:
            client.delete_chatbot_row(chatbot["id"])
        except PowabaseAPIError:
            pass

    client.delete_user(user_id)
    return True


def reset_password(client, user_id: str, new_password: str) -> bool:
    if client.get_user(user_id) is None:
        return False
    client.update_user(user_id, {"password_hash": hash_password(new_password)})
    return True


def rename_user(client, user_id: str, new_username: str) -> dict:
    uname = validate_username(new_username).lower()
    existing = client.get_user_by_username(uname)
    if existing is not None and existing["id"] != user_id:
        raise UsernameTakenError(uname)
    client.update_user(user_id, {"username": uname})
    return {"id": user_id, "username": uname}
