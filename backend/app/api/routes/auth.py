import hmac

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.schemas import (
    AuthResponse, LoginRequest, MeResponse, RegisterRequest, SignupPolicyResponse,
)
from app.services.auth_service import (
    AuthService, DuplicateUsernameError, InvalidCredentialsError,
)
from app.services.chatbot_service import ChatbotService, get_chatbot_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: dict, settings) -> str:
    return create_access_token(user["id"], settings.auth_jwt_secret, settings.auth_token_ttl_hours)


@router.get("/signup-policy", response_model=SignupPolicyResponse)
def signup_policy(settings=Depends(get_settings)):
    """Whether registration needs an invite code, so the form can adapt.

    Deliberately does not reveal the code itself.
    """
    return SignupPolicyResponse(invite_required=bool(settings.signup_invite_code))


@router.post("/register", response_model=AuthResponse)
def register(
    req: RegisterRequest,
    client: PowabaseClient = Depends(get_powabase_client),
    settings=Depends(get_settings),
    chatbots: ChatbotService = Depends(get_chatbot_service),
):
    # Gates registration only. Login is untouched, so rotating the code can
    # never lock out an existing user.
    required = settings.signup_invite_code
    if required and not hmac.compare_digest(req.invite_code or "", required):
        raise HTTPException(
            status_code=403,
            detail="This demo needs an invite code to register. Ask whoever shared the link.",
        )
    try:
        user = AuthService(client, chatbots=chatbots).register(req.username, req.password)
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
