from fastapi import APIRouter, Depends, HTTPException

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatService,
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.profile_service import ProfileService, get_profile_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    profiles: ProfileService = Depends(get_profile_service),
):
    try:
        resolved = profiles.resolve(req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    service = ChatService(client, resolved["agent_id"])
    try:
        result = service.ask(req.query, session_id=req.session_id)
        return ChatResponse(**result)
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
