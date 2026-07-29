import pytest
from app.core.security import (
    hash_password, verify_password, create_access_token, decode_access_token, TokenError,
)

def test_hash_and_verify_roundtrip():
    h = hash_password("hunter2pass")
    assert h != "hunter2pass"
    assert verify_password("hunter2pass", h) is True
    assert verify_password("wrong", h) is False

def test_token_roundtrip():
    tok = create_access_token("user-1", "sekret", 168)
    assert decode_access_token(tok, "sekret") == "user-1"

def test_token_bad_secret_raises():
    tok = create_access_token("user-1", "sekret", 168)
    with pytest.raises(TokenError):
        decode_access_token(tok, "other-secret")

def test_token_expired_raises():
    tok = create_access_token("user-1", "sekret", 0)  # exp = iat, already expired
    import time; time.sleep(1)
    with pytest.raises(TokenError):
        decode_access_token(tok, "sekret")

def test_token_garbage_raises():
    with pytest.raises(TokenError):
        decode_access_token("not.a.jwt", "sekret")
