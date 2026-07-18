from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "kb_id": settings.powabase_kb_id,
        "agent_id": settings.powabase_agent_id,
        "model": settings.powabase_agent_model,
    }
