from __future__ import annotations

from fastapi import Request

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
        self.client.delete_chatbot_row(chatbot_id)
        return True


def get_chatbot_service(request: Request) -> "ChatbotService":
    """FastAPI dependency returning the shared ChatbotService."""
    return request.app.state.chatbot_service
