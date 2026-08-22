"""One conversational turn: route it, retrieve for it, answer it, record it.

Extracted from the /chat handler so the public share route runs the SAME
orchestration rather than a second copy of it. Two copies would drift, and the
copy that drifted would be the one strangers can reach.

This function knows nothing about authentication. Every caller is responsible
for proving the session is theirs to use BEFORE calling — /chat by ownership,
the share route by token plus the `shared` flag.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.clients.powabase_client import PowabaseAPIError
from app.models.schemas import AnsweredBy, ChatResponse
from app.services.agent_scope import roster_for
from app.services.chat_service import ChatService
from app.services.context_budget import clamp_context_tokens
from app.services.conversation import conversation_message
from app.services.orchestrator import OrchestratorService
from app.services.retrieval_scope import kb_ids_for
from app.services.session_service import DEFAULT_NAME


@dataclass(frozen=True)
class TurnDeps:
    """Everything a turn needs that is not the turn itself.

    A frozen dataclass rather than positional arguments: the handler had eleven
    dependencies, and a caller that silently swapped two of them would be very
    hard to see in review.
    """
    client: object
    sessions: object
    agents: object
    messages: object
    chatbot_kb: object
    scratch_kb_id: str | None
    orchestrator_agent_id: str
    general_assistant_id: str
    settings: object


def title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


def recent_turns(raw, turns: int) -> list:
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    history = [
        {"role": m.get("role", "user"), "text": m.get("content") or m.get("text") or ""}
        for m in items
    ]
    return history[-(turns * 2):] if turns > 0 else []


def answer_turn(deps: TurnDeps, session_row: dict, chatbot_row: dict | None,
                query: str) -> ChatResponse:
    session_id = session_row["id"]
    try:
        history = deps.messages.recent_turns(session_id, deps.settings.history_turns)
    except PowabaseAPIError:
        history = []

    # Every chat starts with the whole roster; this chat may exclude some.
    roster = roster_for(
        deps.agents.list(session_row.get("chatbot_id")),
        session_row.get("excluded_agent_ids"),
    )
    decision = OrchestratorService(deps.client, deps.orchestrator_agent_id).route(
        query, roster, history
    )

    agent_row = next((a for a in roster if a["id"] == decision.agent_id), None)
    if agent_row is not None:
        answering_agent_id = agent_row["powabase_agent_id"]
        answered_by = AnsweredBy(id=agent_row["id"], name=agent_row["name"])
    else:
        answering_agent_id = deps.general_assistant_id
        answered_by = AnsweredBy(id=None, name="General assistant")

    service = ChatService(
        deps.client, answering_agent_id,
        kb_ids_for(agent_row, session_row, deps.chatbot_kb.kb_ids(chatbot_row),
                   deps.scratch_kb_id),
        None,
        clamp_context_tokens(
            (agent_row or {}).get("max_context_tokens"),
            (agent_row or {}).get("model"),
        ),
    )
    # Agents run statelessly: a Powabase thread is bound to exactly one agent,
    # so a chat several agents take turns in cannot use one. History travels in
    # the message instead.
    result = service.ask(query, message=conversation_message(history, query))

    # Persist best-effort: the answer is already computed (and paid for), so a
    # write failure must not fail the request.
    updates: dict = {}
    if session_row.get("name") == DEFAULT_NAME:
        updates["name"] = title_from(query)
    try:
        deps.messages.add_user_turn(session_id, query)
        deps.messages.add_assistant_turn(
            session_id, result["answer"], result["citations"],
            answered_by_id=answered_by.id, answered_by_name=answered_by.name,
        )
        deps.sessions.touch(session_id, **updates)
    except (PowabaseAPIError, RuntimeError):
        pass

    return ChatResponse(
        answer=result["answer"], citations=result["citations"],
        answered_by=answered_by,
    )
