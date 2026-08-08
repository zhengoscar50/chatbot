from __future__ import annotations

from fastapi import Depends

from app.clients.powabase_client import PowabaseClient, get_powabase_client

USER = "user"
ASSISTANT = "assistant"


class MessageStore:
    """The chat transcript, owned by this app rather than by Powabase.

    A Powabase thread is bound to exactly one agent, so it cannot hold a
    conversation that several agents take turns in. Keeping the transcript here
    also means chat history outlives any individual agent.
    """

    def __init__(self, client):
        self.client = client

    def add_user_turn(self, session_id: str, text: str) -> None:
        self.client.insert_message({
            "session_id": session_id, "role": USER, "content": text,
        })

    def add_assistant_turn(self, session_id: str, text: str, citations: list,
                           answered_by_id=None, answered_by_name=None) -> None:
        self.client.insert_message({
            "session_id": session_id,
            "role": ASSISTANT,
            "content": text,
            "citations": citations or [],
            "answered_by_id": answered_by_id,
            "answered_by_name": answered_by_name,
        })

    def transcript(self, session_id: str) -> list:
        return self.client.list_messages(session_id)

    def recent_turns(self, session_id: str, turns: int) -> list:
        """The last ``turns`` exchanges as {role, text}, oldest first.

        Feeds both the orchestrator's routing decision and the conversation
        context handed to the answering agent.
        """
        if turns <= 0:
            return []
        rows = self.client.list_messages(session_id)
        recent = rows[-(turns * 2):]
        return [{"role": r.get("role", USER), "text": r.get("content") or ""} for r in recent]


def get_message_store(
    client: PowabaseClient = Depends(get_powabase_client),
) -> "MessageStore":
    """FastAPI dependency returning a MessageStore over the shared client.

    Depends on the client rather than reaching into app.state, so overriding
    get_powabase_client in a test also swaps the store's backing client.
    """
    return MessageStore(client)
