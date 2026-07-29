from typing import Any, Optional
import re

from pydantic import BaseModel, Field, field_validator


class IngestResponse(BaseModel):
    source_id: str
    status: str


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


class RegisterRequest(BaseModel):
    username: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def username_must_contain_alphanumeric(cls, v: str) -> str:
        """Ensure username contains at least one alphanumeric character."""
        if not re.search(r"[A-Za-z0-9]", v):
            raise ValueError("username must contain at least one alphanumeric character")
        return v


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str
