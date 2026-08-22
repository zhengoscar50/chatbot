"""The public face of a shared chatbot. No authentication reaches these.

Every handler answers 404 for anything it cannot serve — an unknown token, a
session that is not a visitor's, a chatbot that is not shared. A stranger must
not be able to tell "wrong token" from "no such chatbot", because that
difference is how a guessed token gets confirmed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import FRONTEND_DIR, get_settings
from app.models.schemas import AnsweredBy, ChatResponse, PublicChatbotInfo, PublicChatRequest
from app.services.agent_service import AgentService, get_agent_service
# FRONTEND_DIR comes from config, NOT from app.main: main.py imports this
# router, so importing back from it would be a circular import.
from app.services.chat_service import (
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.chat_turn import TurnDeps, answer_turn
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.general_assistant import get_general_assistant_id
from app.services.message_store import MessageStore, get_message_store
from app.services.orchestrator import get_orchestrator_agent_id
from app.services.scratch_kb import get_scratch_kb_id
from app.services.session_service import SessionService, get_session_service
from app.services.share_service import ShareService, get_share_service, redact_citations

router = APIRouter(prefix="/s", tags=["share"])

NOT_FOUND = "Not found"


def _chatbot_or_404(share: ShareService, token: str) -> dict:
    row = share.resolve(token)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


@router.get("/{token}", include_in_schema=False)
async def public_page(token: str):
    """The visitor's page. The token is not validated here on purpose — the
    page fetches /info and shows its own error, and serving the shell either
    way keeps a valid token from being distinguishable by response size."""
    return FileResponse(str(FRONTEND_DIR / "share.html"))


@router.get("/{token}/info", response_model=PublicChatbotInfo)
async def public_info(token: str, share: ShareService = Depends(get_share_service)):
    row = await run_in_threadpool(_chatbot_or_404, share, token)
    # Name and description ONLY. Never the owner, the agents, or the id.
    return PublicChatbotInfo(name=row["name"], description=row.get("description") or "")


@router.post("/{token}/session")
async def public_session(
    token: str,
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
):
    """A visitor's own conversation, so two visitors never share one."""
    row = await run_in_threadpool(_chatbot_or_404, share, token)
    created = await run_in_threadpool(
        sessions.create_session, row["owner_id"], row["id"], None, True,
    )
    return {"session_id": created["id"]}


@router.post("/{token}/chat", response_model=ChatResponse)
async def public_chat(
    token: str,
    req: PublicChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
    messages: MessageStore = Depends(get_message_store),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    scratch_kb_id: str = Depends(get_scratch_kb_id),
    orchestrator_agent_id: str = Depends(get_orchestrator_agent_id),
    general_assistant_id: str = Depends(get_general_assistant_id),
    settings=Depends(get_settings),
):
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)

    session_row = await run_in_threadpool(sessions.get, req.session_id)
    # BOTH conditions. Chatbot membership alone is not enough: the owner's own
    # chats live in this same chatbot, and without the `shared` check a visitor
    # could name one and read or inject into a private conversation.
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    if not await run_in_threadpool(share.consume, chatbot):
        raise HTTPException(
            status_code=429,
            detail="This demo has reached its limit for today — try again tomorrow.",
        )

    deps = TurnDeps(
        client=client, sessions=sessions, agents=agents, messages=messages,
        chatbot_kb=chatbot_kb, scratch_kb_id=scratch_kb_id,
        orchestrator_agent_id=orchestrator_agent_id,
        general_assistant_id=general_assistant_id, settings=settings,
    )
    try:
        result = await run_in_threadpool(
            answer_turn, deps, session_row, chatbot, req.query
        )
    except (ModelBusyError, InsufficientCreditsError, ProviderKeyError,
            PowabaseAPIError, RuntimeError):
        # Deliberately opaque. The authenticated route reports which upstream
        # failed and how; a stranger learns only that it did not work.
        raise HTTPException(status_code=503, detail="Sorry — that didn't work. Try again.")

    # Redact on the way out: markers and excerpts, never a filename, and never
    # the internal agent id.
    return ChatResponse(
        answer=result.answer,
        citations=redact_citations(result.citations),
        # The agent NAME is worth showing; its id is an internal identifier a
        # stranger has no use for and no business holding.
        answered_by=None if result.answered_by is None
        else AnsweredBy(id=None, name=result.answered_by.name),
    )
