from __future__ import annotations

from app.clients.powabase_client import PowabaseAPIError
from app.core.security import hash_password, verify_password
from app.services.chatbot_service import DEFAULT_CHATBOT_NAME

# Precomputed so an unknown-user login still pays the argon2 cost (defeats a
# timing side-channel that would otherwise reveal whether a username exists).
_DUMMY_HASH = hash_password("dummy-password-for-constant-time-auth")


class DuplicateUsernameError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, client, chatbots=None):
        self.client = client
        # Optional so existing call sites and tests that never register keep
        # working unchanged.
        self.chatbots = chatbots

    def register(self, username: str, password: str) -> dict:
        uname = username.strip().lower()
        if self.client.get_user_by_username(uname) is not None:
            raise DuplicateUsernameError(uname)
        try:
            user = self.client.insert_user(
                {"username": uname, "password_hash": hash_password(password)}
            )
        except PowabaseAPIError as e:
            # Unique-index race: two concurrent registers of the same name.
            if getattr(e, "status_code", None) == 409:
                raise DuplicateUsernameError(uname)
            raise
        if self.chatbots is not None:
            # Only after the user row commits — a failed insert (including
            # the duplicate-username race) must not leave an orphan chatbot.
            self.chatbots.create(user["id"], DEFAULT_CHATBOT_NAME)
        return user

    def authenticate(self, username: str, password: str) -> dict:
        uname = username.strip().lower()
        user = self.client.get_user_by_username(uname)
        password_hash = user["password_hash"] if user else _DUMMY_HASH
        if not verify_password(password, password_hash) or user is None:
            raise InvalidCredentialsError()
        return user
