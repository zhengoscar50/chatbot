from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import AnsweredBy, ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.conversation import conversation_message
from app.services.general_assistant import get_general_assistant_id
from app.services.message_store import MessageStore, get_message_store
from app.services.general_kb import get_general_kb_id
from app.services.orchestrator import OrchestratorService, get_orchestrator_agent_id
from app.services.retrieval_scope import kb_ids_for
from app.services.session_service import DEFAULT_NAME, SessionService, get_session_service

router = APIRouter(prefix="/chat", tags=["chat"])


def _title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


def _recent_turns(raw, turns: int) -> list:
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    history = [
        {"role": m.get("role", "user"), "text": m.get("content") or m.get("text") or ""}
        for m in items
    ]
    return history[-(turns * 2):] if turns > 0 else []


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
    messages: MessageStore = Depends(get_message_store),
    general_kb_id: str = Depends(get_general_kb_id),
    orchestrator_agent_id: str = Depends(get_orchestrator_agent_id),
    general_assistant_id: str = Depends(get_general_assistant_id),
    settings=Depends(get_settings),
):
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        history = messages.recent_turns(req.session_id, settings.history_turns)
    except PowabaseAPIError:
        history = []

    # One call decides both which agent answers and whether to retrieve.
    roster = agents.list(user["id"])
    decision = OrchestratorService(client, orchestrator_agent_id).route(
        req.query, roster, history
    )

    agent_row = next((a for a in roster if a["id"] == decision.agent_id), None)
    if agent_row is not None:
        answering_agent_id = agent_row["powabase_agent_id"]
        answered_by = AnsweredBy(id=agent_row["id"], name=agent_row["name"])
    else:
        answering_agent_id = general_assistant_id
        answered_by = AnsweredBy(id=None, name="General assistant")

    service = ChatService(
        client, answering_agent_id,
        kb_ids_for(agent_row, row, general_kb_id),
        settings.retrieval_top_k, settings.retrieval_max_context_tokens,
    )
    # Agents run statelessly: a Powabase thread is bound to exactly one agent,
    # so a chat several agents take turns in cannot use one. History travels in
    # the message instead.
    try:
        result = service.ask(conversation_message(history, req.query))
    except ModelBusyError as e:
        raise HTTPException(status_code=503, detail=e.message)
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)
    except ProviderKeyError as e:
        raise HTTPException(
            status_code=424,
            detail=f"{e.message} (configure a provider key in Powabase Studio -> Settings -> LLM Provider Keys)",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Persist the turns and the chat's recency best-effort: the answer is
    # already computed (and paid for), so a write failure must not fail the
    # request. (Downside: that exchange is missing when the chat is reopened —
    # acceptable vs. losing the answer with a 500.)
    updates: dict = {}
    if row.get("name") == DEFAULT_NAME:
        updates["name"] = _title_from(req.query)
    try:
        messages.add_user_turn(req.session_id, req.query)
        messages.add_assistant_turn(
            req.session_id, result["answer"], result["citations"],
            answered_by_id=answered_by.id, answered_by_name=answered_by.name,
        )
        sessions.touch(req.session_id, **updates)
    except (PowabaseAPIError, RuntimeError):
        pass

    return ChatResponse(
        answer=result["answer"], citations=result["citations"],
        answered_by=answered_by,
    )
