from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseAPIError, PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    InsufficientCreditsError,
    ModelBusyError,
    ProviderKeyError,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.chatbot_service import ChatbotService, get_chatbot_service
from app.services.chat_turn import TurnDeps, answer_turn
from app.services.general_assistant import get_general_assistant_id
from app.services.message_store import MessageStore, get_message_store
# OrchestratorService itself is not called here — answer_turn constructs it —
# but the import must stay: it is patched at the class level by tests
# (chat_route.OrchestratorService.route), and that only reaches answer_turn's
# own construction because it mutates the shared class rather than rebinding
# a name.
from app.services.orchestrator import OrchestratorService, get_orchestrator_agent_id
from app.services.scratch_kb import get_scratch_kb_id
from app.services.chatbot_kb import ChatbotKbService, get_chatbot_kb_service
from app.services.session_service import SessionService, get_session_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
    sessions: SessionService = Depends(get_session_service),
    agents: AgentService = Depends(get_agent_service),
    messages: MessageStore = Depends(get_message_store),
    scratch_kb_id: str = Depends(get_scratch_kb_id),
    chatbot_kb: ChatbotKbService = Depends(get_chatbot_kb_service),
    chatbots: ChatbotService = Depends(get_chatbot_service),
    orchestrator_agent_id: str = Depends(get_orchestrator_agent_id),
    general_assistant_id: str = Depends(get_general_assistant_id),
    settings=Depends(get_settings),
):
    row = sessions.get_owned_session(req.session_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # The chatbot comes off the CHAT ROW, never the request body: a client must
    # not be able to aim one chatbot's question at another chatbot's knowledge
    # or roster.
    chatbot_id = row.get("chatbot_id")
    chatbot = chatbots.get_owned(chatbot_id, user["id"]) if chatbot_id else None

    deps = TurnDeps(
        client=client, sessions=sessions, agents=agents, messages=messages,
        chatbot_kb=chatbot_kb, scratch_kb_id=scratch_kb_id,
        orchestrator_agent_id=orchestrator_agent_id,
        general_assistant_id=general_assistant_id, settings=settings,
    )
    try:
        return answer_turn(deps, row, chatbot, req.query)
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
