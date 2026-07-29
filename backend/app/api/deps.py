from fastapi import Depends, Header, HTTPException

from app.clients.powabase_client import PowabaseClient, get_powabase_client
from app.core.config import Settings, get_settings
from app.core.security import TokenError, decode_access_token


def get_current_user(
    authorization: str = Header(None),
    client: PowabaseClient = Depends(get_powabase_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[len("Bearer "):]
    try:
        user_id = decode_access_token(token, settings.auth_jwt_secret)
    except TokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = client.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user
