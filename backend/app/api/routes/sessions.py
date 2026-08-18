from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import (
    ChatMessage,
    MessagesResponse,
    SessionCreateRequest,
    SessionUpdateRequest,
    SessionResponse,
    SessionSummary,
)
from app.services.message_store import MessageStore, get_message_store
from app.services.agent_scope import sanitise_exclusions
from app.services.agent_service import AgentService, get_agent_service
from app.services.chatbot_service import ChatbotService, get_chatbot_service
from app.services.session_service import SessionService, get_session_service

router = APIRouter(tags=["sessions"])


def _default_chatbot_id(chatbots: ChatbotService, owner_id: str) -> str:
    # This route has no chatbot_id parameter yet — a later task adds one and
    # scopes it properly. Every account has at least one chatbot (created at
    # registration), so the oldest one stands in until then.
    return chatbots.list(owner_id)[0]["id"]


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: SessionCreateRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        chatbot_id = await run_in_threadpool(_default_chatbot_id, chatbots, user["id"])
        row = await run_in_threadpool(
            sessions.create_session, user["id"], chatbot_id, req.name
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SessionResponse(id=row["id"], name=row["name"])


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    req: SessionUpdateRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    excluded = row.get("excluded_agent_ids") or []
    try:
        if req.name is not None:
            await run_in_threadpool(sessions.rename, session_id, req.name)
        if req.excluded_agent_ids is not None:
            # Intersected with the caller's own roster, never trusted: a client
            # can post any id, including another user's agent. The chatbot
            # comes off the chat's own row — never the request — so a client
            # can't ask one chatbot's chat to exclude another chatbot's agent.
            roster = await run_in_threadpool(agents.list, row.get("chatbot_id"))
            excluded = sanitise_exclusions(req.excluded_agent_ids, roster)
            await run_in_threadpool(
                sessions.set_excluded_agents, session_id, excluded
            )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return SessionResponse(
        id=session_id,
        name=req.name if req.name is not None else row.get("name", ""),
        excluded_agent_ids=excluded,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        await run_in_threadpool(sessions.delete, session_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(status_code=204)


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        chatbot_id = await run_in_threadpool(_default_chatbot_id, chatbots, user["id"])
        return await run_in_threadpool(sessions.list, chatbot_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def session_messages(
    session_id: str,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    messages: MessageStore = Depends(get_message_store),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        raw = await run_in_threadpool(messages.transcript, session_id)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return MessagesResponse(messages=_format_messages(raw))


def _format_messages(raw) -> list:
    """Rows from our own messages table into the API shape.

    Still tolerates a {"messages": [...]} wrapper so old callers and fixtures
    keep working.
    """
    items = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    formatted = []
    for item in items:
        formatted.append(ChatMessage(
            role=item.get("role", "assistant"),
            text=item.get("content") or item.get("text") or "",
            citations=item.get("citations") or [],
            answered_by=item.get("answered_by_name"),
        ))
    return formatted
