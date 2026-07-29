from __future__ import annotations

import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, tampered, or expired."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: str, secret: str, ttl_hours: int) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + ttl_hours * 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> str:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise TokenError(str(e))
    return payload["sub"]
