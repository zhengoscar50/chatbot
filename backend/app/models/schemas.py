from typing import Any, Optional

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    source_id: str
    status: str


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: Optional[str] = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
