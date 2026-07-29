from __future__ import annotations

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


def delete_user(client, session_service, user_id: str) -> bool:
    if client.get_user(user_id) is None:
        return False
    for s in client.list_sessions(user_id):
        session_service.delete(s["id"])  # cascades KB + agent + row (best-effort)
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
