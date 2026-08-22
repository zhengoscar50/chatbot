"""Chatbots: a user's separate assistants, each owning its own agents and chats."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError
from app.models.schemas import (
    ChatbotCreateRequest,
    ChatbotResponse,
    ChatbotUpdateRequest,
    ShareResponse,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.chatbot_service import (
    ChatbotService,
    LastChatbotError,
    get_chatbot_service,
)
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
    base = str(request.base_url).rstrip("/")
    url = f"{base}/s/{token}" if token else None
    embed = (
        f'<iframe src="{url}" width="420" height="640" style="border:0"></iframe>'
        if token else None
    )
    # A count from an earlier date is stale, not zero-but-forgotten: only
    # `consume` applies today's reset when it writes, so a read-only response
    # must apply the same reset itself or report yesterday's total as today's.
    used_today = int(row.get("share_used_today") or 0)
    if str(row.get("share_used_date") or "") != date.today().isoformat():
        used_today = 0
    return ShareResponse(
        token=token, url=url, embed=embed,
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
