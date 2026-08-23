from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.services.onboarding import derive_steps

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding")
def get_onboarding(
    user: dict = Depends(get_current_user),
    client: PowabaseClient = Depends(get_powabase_client),
):
    """The dashboard's getting-started checklist for the calling user.

    Derived, never stored: delete your only agent and its step un-ticks. That
    is the point — a stored flag would keep claiming an agent exists at exactly
    the moment the panel needs to be honest.

    Every read is filtered by owner. The checklist is a progress report, and a
    progress report that counts someone else's agents is both a lie and a leak.
    """
    owner_id = user["id"]
    chatbots = client.list_chatbot_rows(owner_id)
    agents = client.list_agent_rows_by_owner(owner_id)
    sessions = client.list_sessions_by_owner(owner_id)
    # No sessions means no answer, and the client skips the round trip — the
    # common case for the empty account this panel exists to help.
    has_answer = client.has_specialist_answer([s["id"] for s in sessions])

    steps = derive_steps(chatbots, agents, has_answer)
    return {"steps": steps, "complete": all(s["done"] for s in steps)}
