from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.schemas import AuthResponse, LoginRequest, MeResponse, RegisterRequest
from app.services.auth_service import (
    AuthService, DuplicateUsernameError, InvalidCredentialsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: dict, settings) -> str:
    return create_access_token(user["id"], settings.auth_jwt_secret, settings.auth_token_ttl_hours)


@router.post("/register", response_model=AuthResponse)
def register(
    req: RegisterRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    try:
        user = AuthService(client).register(req.username, req.password)
    except DuplicateUsernameError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return AuthResponse(token=_token_for(user, settings), username=user["username"])


@router.post("/login", response_model=AuthResponse)
def login(
    req: LoginRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
):
    try:
        user = AuthService(client).authenticate(req.username, req.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return AuthResponse(token=_token_for(user, settings), username=user["username"])


@router.get("/me", response_model=MeResponse)
def me(user: dict = Depends(get_current_user)):
    return MeResponse(username=user["username"])
