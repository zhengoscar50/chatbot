"""Chatbots: a user's separate assistants, each owning its own agents and chats."""
from datetime import date
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError
from app.models.schemas import (
    ChatbotCreateRequest,
    ChatbotResponse,
    ChatbotUpdateRequest,
    InboxConversation,
    ShareResponse,
)
from app.services.agent_service import AgentService, get_agent_service
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.services.chatbot_service import (
    ChatbotService,
    LastChatbotError,
    get_chatbot_service,
)
from app.services.inbox import conversations
from app.services.session_service import SessionService, get_session_service
from app.services.share_service import ShareService, get_share_service

router = APIRouter(prefix="/chatbots", tags=["chatbots"])


def _to_response(row: dict) -> ChatbotResponse:
    return ChatbotResponse(
        id=row["id"], name=row["name"], description=row.get("description") or ""
    )


@router.post("", response_model=ChatbotResponse, status_code=201)
async def create_chatbot(
    req: ChatbotCreateRequest,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        row = await run_in_threadpool(
            chatbots.create, user["id"], req.name, req.description
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _to_response(row)


@router.get("", response_model=list)
async def list_chatbots(
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    try:
        rows = await run_in_threadpool(chatbots.list, user["id"])
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [_to_response(r) for r in rows]


@router.patch("/{chatbot_id}", response_model=ChatbotResponse)
async def rename_chatbot(
    chatbot_id: str,
    req: ChatbotUpdateRequest,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        await run_in_threadpool(chatbots.rename, chatbot_id, req.name)
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _to_response(dict(row, name=req.name))


@router.delete("/{chatbot_id}", status_code=204)
async def delete_chatbot(
    chatbot_id: str,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    agents: AgentService = Depends(get_agent_service),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        removed = await run_in_threadpool(
            chatbots.delete, chatbot_id, user["id"], agents, sessions
        )
    except LastChatbotError:
        raise HTTPException(
            status_code=400,
            detail="This is your last chatbot. Create another before deleting it.",
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return None


def _share_response(request: Request, row: dict) -> ShareResponse:
    token = row.get("share_token")
    # Escaped because both land inside HTML attributes in the snippets below.
    # base_url derives from the Host header, which is the client's to set.
    base = escape(str(request.base_url).rstrip("/"), quote=True)
    token = escape(token, quote=True) if token else token
    url = f"{base}/s/{token}" if token else None
    embed = (
        f'<iframe src="{url}" width="420" height="640" style="border:0"></iframe>'
        if token else None
    )
    # The other way to embed: a tab on the edge of the page instead of a
    # rectangle in the middle of it. Same token, same public page underneath.
    #
    # The onerror is the only thing that can speak when this host stops
    # resolving. If the script itself does not load, none of the widget's code
    # runs — there is nothing on the page left to notice or report it — so the
    # message has to live in the snippet on the embedding site. It goes to the
    # console rather than the page: a broken chatbot must not put a banner in
    # front of somebody else's visitors.
    widget = (
        f'<script src="{base}/widget.js" data-token="{token}" async '
        f'''onerror="console.error(\'Chat widget: could not load from {base} . '''
        f'''If that address has changed, re-copy the embed snippet from your '''
        f'''dashboard.\')"></script>'''
        if token else None
    )
    # A count from an earlier date is stale, not zero-but-forgotten: only
    # `consume` applies today's reset when it writes, so a read-only response
    # must apply the same reset itself or report yesterday's total as today's.
    used_today = int(row.get("share_used_today") or 0)
    if str(row.get("share_used_date") or "") != date.today().isoformat():
        used_today = 0
    return ShareResponse(
        token=token, url=url, embed=embed, widget=widget,
        daily_limit=int(row.get("share_daily_limit") or 0),
        used_today=used_today,
    )


@router.post("/{chatbot_id}/share", response_model=ShareResponse)
async def start_sharing(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    share: ShareService = Depends(get_share_service),
):
    """Create or regenerate this chatbot's unlisted link.

    Regenerating is how revocation-and-reissue works: the previous link stops
    resolving immediately.
    """
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    token = await run_in_threadpool(share.enable, chatbot_id)
    return _share_response(request, dict(row, share_token=token))


@router.delete("/{chatbot_id}/share", response_model=ShareResponse)
async def stop_sharing(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    share: ShareService = Depends(get_share_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    await run_in_threadpool(share.disable, chatbot_id)
    return _share_response(request, dict(row, share_token=None))


@router.get("/{chatbot_id}/share", response_model=ShareResponse)
async def share_state(
    chatbot_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    row = await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return _share_response(request, row)


# How many conversations the inbox shows. The messages behind them are fetched
# in one batched query, so this is what bounds that query's size.
INBOX_LIMIT = 50


@router.get("/{chatbot_id}/inbox", response_model=list[InboxConversation])
async def inbox(
    chatbot_id: str,
    user: dict = Depends(get_current_user),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    sessions: SessionService = Depends(get_session_service),
    client: PowabaseClient = Depends(get_powabase_client),
):
    """Every conversation visitors have had with this chatbot's share link.

    `shared=True` is the whole access story on the listing side. Visitor
    sessions and the owner's own chats live in one table and are told apart
    only by that flag, so passing it is what keeps the owner's private
    conversations out of a view whose entire purpose is conversations they did
    not have. Reading one of these transcripts reuses
    GET /sessions/{id}/messages, which is already owner-scoped: visitor
    sessions carry the owner's own owner_id.
    """
    if await run_in_threadpool(chatbots.get_owned, chatbot_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    try:
        rows = await run_in_threadpool(sessions.list, chatbot_id, shared=True)
        rows = rows[:INBOX_LIMIT]
        messages = await run_in_threadpool(
            client.messages_for_sessions, [r["id"] for r in rows]
        )
    except PowabaseAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return conversations(rows, messages)
