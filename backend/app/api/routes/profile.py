from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.models.schemas import ProfileRequest, ProfileResponse
from app.services.profile_service import ProfileService, get_profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("", response_model=ProfileResponse)
async def ensure_profile(
    req: ProfileRequest,
    profiles: ProfileService = Depends(get_profile_service),
):
    try:
        resolved = await run_in_threadpool(profiles.resolve, req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ProfileResponse(profile=req.profile, slug=resolved["slug"])
