from __future__ import annotations

import json

from app.services.router_agent import GATE_RESPONSE_FORMAT


class GateService:
    """Decides whether a chat message needs the knowledge base.

    Uses the shared router agent (a single cheap sync LLM call). Biased to
    retrieve: any error or unparseable output resolves to True.
    """

    def __init__(self, client, router_agent_id: str):
        self.client = client
        self.router_agent_id = router_agent_id

    def needs_kb(self, query: str, history: list | None = None) -> bool:
        message = self._build_message(query, history or [])
        try:
            response = self.client.run_agent_sync(
                self.router_agent_id, message, response_format=GATE_RESPONSE_FORMAT
            )
            return bool(json.loads(response["content"])["needs_kb"])
        except Exception:
            # Fail safe: when the gate can't decide, retrieve (grounded answer).
            return True

    @staticmethod
    def _build_message(query: str, history: list) -> str:
        lines = []
        if history:
            lines.append("Recent conversation:")
            for turn in history:
                lines.append(f"{turn.get('role', 'user')}: {turn.get('text', '')}")
            lines.append("")
        lines.append(f"Current user message: {query}")
        lines.append("Does answering the current message require the knowledge base?")
        return "\n".join(lines)
