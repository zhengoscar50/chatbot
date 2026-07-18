from __future__ import annotations


class InsufficientCreditsError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ProviderKeyError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ChatService:
    def __init__(self, client, agent_id: str):
        self.client = client
        self.agent_id = agent_id

    def ask(self, query: str, session_id: str | None = None) -> dict:
        events = self.client.run_agent(
            self.agent_id, query, session_id=session_id, citations_enabled=True
        )
        answer = None
        citations: list = []
        result_session_id = session_id

        for event in events:
            name = event["event"]
            data = event["data"]
            if name == "start":
                result_session_id = data.get("session_id", result_session_id)
            elif name == "error":
                self._raise_for_error(data)
            elif name == "complete":
                answer = data.get("answer")
                citations = data.get("citations", [])

        if answer is None:
            raise RuntimeError("Agent run completed without a final answer")

        return {"answer": answer, "session_id": result_session_id, "citations": citations}

    def _raise_for_error(self, data: dict) -> None:
        code = data.get("error", "")
        message = data.get("message", str(data))
        if code == "insufficient_credits":
            raise InsufficientCreditsError(message)
        if code == "provider_key_decrypt_failed":
            raise ProviderKeyError(message)
        raise RuntimeError(message)
