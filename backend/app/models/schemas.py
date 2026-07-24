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


class ProfileRequest(BaseModel):
    profile: str = Field(..., min_length=1)


class ProfileResponse(BaseModel):
    profile: str
    slug: str


class SessionCreateRequest(BaseModel):
    user: str = Field(..., min_length=1)
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str


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
