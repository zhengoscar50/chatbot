from fastapi import APIRouter, Depends, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.session_service import DEFAULT_NAME, SessionService, get_session_service

router = APIRouter(prefix="/chat", tags=["chat"])


def _title_from(query: str) -> str:
    title = query.strip()
    return title if len(title) <= 60 else title[:60].rstrip() + "…"


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
):
    row = sessions.get(req.session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = ChatService(client, row["agent_id"])
    powabase_session_id = row.get("powabase_session_id")
    try:
        result = service.ask(req.query, session_id=powabase_session_id)
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

    updates: dict = {}
    if not powabase_session_id and result.get("session_id"):
        updates["powabase_session_id"] = result["session_id"]
        if row.get("name") == DEFAULT_NAME:
            updates["name"] = _title_from(req.query)
    sessions.touch(req.session_id, **updates)

    return ChatResponse(answer=result["answer"], citations=result["citations"])
