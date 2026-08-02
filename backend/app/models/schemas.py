from typing import Any, Optional
import re

from pydantic import BaseModel, Field, field_validator


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def validate_username(value: str) -> str:
    v = value.strip()
    if not _USERNAME_RE.match(v) or not re.search(r"[A-Za-z0-9]", v):
        raise ValueError(
            "Username can only contain letters, numbers, dots, dashes, and "
            "underscores (3–32 characters)."
        )
    return v


class IngestResponse(BaseModel):
    source_id: str
    status: str


class IngestStatusResponse(BaseModel):
    source_id: str
    status: str
    detail: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str


class SessionRenameRequest(BaseModel):
    name: str = Field(..., min_length=1)


class SessionSummary(BaseModel):
    id: str
    name: str
    updated_at: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    text: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class MessagesResponse(BaseModel):
    messages: list[ChatMessage]


class AdminVerifyRequest(BaseModel):
    password: str = Field(..., min_length=1)


class AdminResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


class AdminRenameRequest(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return validate_username(v)


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return validate_username(v)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str


class ResearchRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)


class ResearchStartResponse(BaseModel):
    job_id: str
    status: str


class ResearchStatusResponse(BaseModel):
    status: str
    stage: Optional[str] = None
    report: Optional[str] = None
    citations: list = Field(default_factory=list)
    detail: Optional[str] = None
