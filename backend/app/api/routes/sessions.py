from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import (
    ChatMessage,
    MessagesResponse,
    SessionCreateRequest,
    SessionRenameRequest,
    SessionResponse,
    SessionSummary,
)
from app.services.message_store import MessageStore, get_message_store
from app.services.session_service import SessionService, get_session_service

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: SessionCreateRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        row = await run_in_threadpool(sessions.create_session, user["id"], req.name)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SessionResponse(id=row["id"], name=row["name"])


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def rename_session(
    session_id: str,
    req: SessionRenameRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
):
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        await run_in_threadpool(sessions.rename, session_id, req.name)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SessionResponse(id=session_id, name=req.name)


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
):
    try:
        return await run_in_threadpool(sessions.list, user["id"])
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
