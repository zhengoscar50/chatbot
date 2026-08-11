from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Report the models that actually decide something.

    Deliberately not POWABASE_AGENT_MODEL: nothing outside the optional
    bootstrap script reads it. Reporting it here let a deployment advertise
    claude-sonnet-5 while routing, new agents and the general assistant all
    ran gpt-4o-mini — invisible precisely because /health looked authoritative.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "models": {
            "orchestrator": settings.orchestrator_model,
            "default_agent": settings.default_agent_model,
            "general_assistant": settings.general_assistant_model,
        },
    }
