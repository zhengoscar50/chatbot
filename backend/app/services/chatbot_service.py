from __future__ import annotations

from fastapi import Request

from app.clients.powabase_client import PowabaseAPIError

DEFAULT_CHATBOT_NAME = "My chatbot"


class LastChatbotError(Exception):
    """Deleting a user's only chatbot would leave their agents homeless."""


class ChatbotService:
    """A chatbot: a named group of agents and the chats that use them.

    The layer between a user and their agents. A user owns chatbots; a chatbot
    owns agents and chats. Routing considers only the agents inside the chat's
    chatbot.
    """

    def __init__(self, client):
        self.client = client

    def create(self, owner_id: str, name: str, description: str = "") -> dict:
        return self.client.insert_chatbot_row({
            "owner_id": owner_id,
            "name": name,
            "description": description,
        })

    def list(self, owner_id: str) -> list:
        return self.client.list_chatbot_rows(owner_id)

    def get_owned(self, chatbot_id: str, owner_id: str):
        row = self.client.get_chatbot_row(chatbot_id)
        if row is None or row.get("owner_id") != owner_id:
            return None
        return row

    def rename(self, chatbot_id: str, name: str) -> None:
        self.client.update_chatbot_row(chatbot_id, {"name": name})

    def delete(self, chatbot_id: str, owner_id: str, agents, sessions) -> bool:
        """Delete a chatbot and everything inside it.

        Deletes its agents, its chats and its own knowledge bases. Sources are
        never deleted — only unlinked — because identical content is
        deduplicated and may belong to another chatbot or user.

        Returns False if it does not exist or is not yours — the caller turns
        that into a 404. Raises LastChatbotError rather than leaving a user
        with nowhere to put an agent.
        """
        row = self.get_owned(chatbot_id, owner_id)
        if row is None:
            return False
        if len(self.list(owner_id)) <= 1:
            raise LastChatbotError()
        for agent in agents.list(chatbot_id):
            agents.delete(agent["id"])
        for session in sessions.list(chatbot_id):
            sessions.delete(session["id"])
        # Best-effort, like the agent and chat cleanup above: a stale knowledge
        # base must not block the delete. The row deletion is authoritative.
        for kb_id in (row.get("kb_id"), row.get("kb_full_id")):
            if not kb_id:
                continue
            try:
                self.client.delete_knowledge_base(kb_id)
            except PowabaseAPIError:
                pass
        self.client.delete_chatbot_row(chatbot_id)
        return True


def get_chatbot_service(request: Request) -> "ChatbotService":
    """FastAPI dependency returning the shared ChatbotService."""
    return request.app.state.chatbot_service
