"""The public face of a shared chatbot. No authentication reaches these.

Every handler answers 404 for anything it cannot serve — an unknown token, a
session that is not a visitor's, a chatbot that is not shared. A stranger must
not be able to tell "wrong token" from "no such chatbot", because that
difference is how a guessed token gets confirmed.
"""
from __future__ import annotations

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, UploadFile)
from fastapi.responses import FileResponse, JSONResponse
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
from app.services.uploads import read_upload_capped
from app.core.config import get_settings
from app.services.share_service import ShareService, get_share_service, redact_turn
from app.services.ingest_service import IngestService, source_status
from app.api.routes.ingest import _run_finish

router = APIRouter(prefix="/s", tags=["share"])

NOT_FOUND = "Not found"
CAP_REACHED = "This demo has reached its limit for today — try again tomorrow."


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
    if not await run_in_threadpool(share.has_room, row):
        raise HTTPException(status_code=429, detail=CAP_REACHED)
    created = await run_in_threadpool(
        sessions.create_session, row["owner_id"], row["id"], None, True,
    )
    return {"session_id": created["id"]}


@router.get("/{token}/session/{session_id}/messages")
async def public_transcript(
    token: str,
    session_id: str,
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    messages: MessageStore = Depends(get_message_store),
):
    """A visitor's own conversation, replayed so the widget can resume it.

    The same BOTH-conditions check public_chat makes, for the same reason: the
    owner's private chats live in this chatbot too, so membership alone would
    let a visitor name one and read it.

    404 on every failure, never 403 — a 403 confirms the session exists, which
    is the one fact an enumeration attempt is after.
    """
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)
    session_row = await run_in_threadpool(sessions.get, session_id)
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    rows = await run_in_threadpool(messages.transcript, session_id)
    out = []
    for row in rows:
        content = row.get("content") or ""
        citations = row.get("citations") or []
        # Stored rows are unredacted: share.py redacts the RESPONSE, after
        # answer_turn has already written the row. Replaying raw would hand
        # back the filenames the live answer stripped.
        if row.get("role") == "assistant":
            content, citations = redact_turn(content, citations)
        name = row.get("answered_by_name")
        out.append({
            "role": row.get("role"),
            "content": content,
            "citations": citations,
            # id is always None here, deliberately, matching the live path
            # above: it is an internal identifier a stranger has no business
            # holding, never an omission to fill in later.
            "answered_by": {"id": None, "name": name} if name else None,
        })
    return {"messages": out}


@router.post("/{token}/upload")
async def public_upload(
    token: str,
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    file: UploadFile = File(...),
    client: PowabaseClient = Depends(get_powabase_client),
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    scratch_kb_id: str = Depends(get_scratch_kb_id),
):
    """A document a visitor attaches to their own conversation, and only theirs.

    Same double check as public_chat, for the same reason: the owner's private
    chats live in this chatbot, and membership alone would let a visitor attach
    a file to one of them.

    It goes into the shared scratch KB recorded against this session, which is
    what scopes retrieval back to this conversation — exactly what the
    authenticated path does, reusing its finisher rather than a second copy.

    There is deliberately no way to promote it. In the account app that chip
    writes a file into the chatbot's permanent knowledge; a stranger on someone
    else's website must never be able to do that.

    An upload consumes one unit of the daily allowance. The cap exists to bound
    what a share link costs its owner, and extraction plus embedding is the
    expensive operation — capping messages while leaving this unlimited would
    guard the cheap half only.
    """
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)

    session_row = await run_in_threadpool(sessions.get, session_id)
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    settings = get_settings()
    # Size first, then the cap. Reading is what this route is protecting
    # against, so it has to be bounded before anything else — and a visitor
    # whose file is refused should not also lose one of the day's messages for
    # a document that was never accepted.
    content = await read_upload_capped(file, settings.max_upload_bytes)

    if not await run_in_threadpool(share.consume, chatbot):
        raise HTTPException(status_code=429, detail=CAP_REACHED)

    service = IngestService(
        client,
        poll_interval=settings.poll_interval_seconds,
        max_wait=settings.ingest_background_max_wait_seconds,
    )
    try:
        source_id = await run_in_threadpool(service.start, file.filename, content)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(
        _run_finish, service, sessions, session_row, source_id, scratch_kb_id
    )
    return JSONResponse(
        status_code=202, content={"source_id": source_id, "status": "processing"}
    )


@router.get("/{token}/upload/{source_id}")
async def public_upload_status(
    token: str,
    source_id: str,
    session_id: str,
    client: PowabaseClient = Depends(get_powabase_client),
    share: ShareService = Depends(get_share_service),
    sessions: SessionService = Depends(get_session_service),
    scratch_kb_id: str = Depends(get_scratch_kb_id),
):
    """How far along a visitor's own attachment is.

    Deliberately does NOT consume the daily allowance. This is a poll, not
    work: charging for it would let a slow extraction spend the owner's whole
    cap before the document it is waiting on ever became answerable.

    Same both-conditions check as the rest of this file, so a visitor cannot
    watch a source attached to somebody else's conversation.

    The last clause is the one that matters. Indexed in the shared scratch KB
    is not yet answerable HERE: the upload is indexed first and recorded on the
    chat second, and only the recording puts it in retrieval scope. Reporting
    "ready" in between would tell the visitor a document is usable while it
    still answers "I don't know" — and if the recording never happens, that is
    a permanent lie rather than a momentary one.
    """
    chatbot = await run_in_threadpool(_chatbot_or_404, share, token)

    session_row = await run_in_threadpool(sessions.get, session_id)
    if (session_row is None
            or session_row.get("chatbot_id") != chatbot["id"]
            or not session_row.get("shared")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    kb_ids = [scratch_kb_id, session_row.get("kb_id"), session_row.get("kb_full_id")]
    try:
        status, detail = await run_in_threadpool(
            source_status, client, source_id, kb_ids
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if status == "indexed" and source_id not in (session_row.get("source_ids") or []):
        status, detail = "processing", None

    return {"source_id": source_id, "status": status, "detail": detail}


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
        raise HTTPException(status_code=429, detail=CAP_REACHED)

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
    # the internal agent id. The answer text is redacted too — the prompts
    # tell every agent to cite its sources, so its prose can name a document
    # by itself; redact_turn closes that for a name the model just cited.
    redacted_answer, redacted_citations = redact_turn(result.answer, result.citations)
    return ChatResponse(
        answer=redacted_answer,
        citations=redacted_citations,
        # The agent NAME is worth showing; its id is an internal identifier a
        # stranger has no use for and no business holding.
        answered_by=None if result.answered_by is None
        else AnsweredBy(id=None, name=result.answered_by.name),
    )
