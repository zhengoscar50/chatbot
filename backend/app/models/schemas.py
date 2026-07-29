from typing import Any, Optional

from pydantic import BaseModel, Field


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
    user: str = Field(..., min_length=1)
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


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str
