import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
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
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.chatbot_service import ChatbotService, get_chatbot_service
from app.services.ingest_service import (
    IndexingFailedError,
    IngestService,
    IngestTimeoutError,
)
from app.services.session_service import SessionService, get_session_service

router = APIRouter(tags=["sessions"])
logger = logging.getLogger(__name__)


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: SessionCreateRequest,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    if await run_in_threadpool(chatbots.get_owned, req.chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        row = await run_in_threadpool(
            sessions.create_session, user["id"], req.chatbot_id, req.name
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
    chatbot_id: str,
    shared: bool = Query(False),
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    if await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        return await run_in_threadpool(sessions.list, chatbot_id, shared=shared)
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


def _finish_promotion(service, chatbot_kb, chatbot_row, sessions, session_id,
                      source_id, full_document_max_chars) -> None:
    """Index one already-extracted document into chatbot knowledge.

    Backgrounded like every other indexing path: chunking a long document takes
    minutes. The source is only forgotten from the chat AFTER indexing
    succeeds, so a failure leaves the chat exactly as it was rather than
    dropping the document on the floor.
    """
    try:
        full_document = 0 < service.char_count(source_id) <= full_document_max_chars
        kb_id = chatbot_kb.ensure_kb(chatbot_row, full_document)
        # Re-uploading the same file yields the SAME source id, because
        # upload_source deduplicates identical content. Promoting it again must
        # not re-index: at best wasted work, at worst an upstream error.
        if not chatbot_kb.contains(kb_id, source_id):
            service.index_into(kb_id, source_id)
        sessions.forget_source(session_id, source_id)
    # No extraction errors are possible here: the source was already extracted
    # when it was ingested into the chat, so only indexing can fail.
    except IndexingFailedError as e:
        logger.warning("promote %s failed: %s", source_id, e.message)
    except IngestTimeoutError as e:
        logger.warning("promote %s timed out while %s", source_id, e.status)
    except PowabaseAPIError as e:
        logger.warning("promote %s: upstream %s", source_id, e.status_code)


@router.post("/sessions/{session_id}/documents/{source_id}/promote", status_code=202)
async def promote_document(
    session_id: str,
    source_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    sessions: SessionService = Depends(get_session_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    """Move one of this chat's uploads into its chatbot's knowledge.

    The chatbot is resolved from the SESSION ROW, never from the request — the
    same rule /chat follows for its roster.
    """
    row = await run_in_threadpool(sessions.get_owned_session, session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if source_id not in (row.get("source_ids") or []):
        raise HTTPException(status_code=404, detail="Document not found")
    chatbot = await run_in_threadpool(
        chatbots.get_owned, row.get("chatbot_id"), user["id"]
    )
    if chatbot is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    service = IngestService(
        client, None,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    background_tasks.add_task(
        _finish_promotion, service, chatbot_kb, chatbot, sessions, session_id,
        source_id, settings.full_document_max_chars,
    )
    return JSONResponse(status_code=202, content={"source_id": source_id,
                                                  "status": "processing"})


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
