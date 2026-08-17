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


class AnsweredBy(BaseModel):
    id: Optional[str] = None      # None when the general assistant answered
    name: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    answered_by: Optional[AnsweredBy] = None


class SessionCreateRequest(BaseModel):
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str
    excluded_agent_ids: list = []


class SessionUpdateRequest(BaseModel):
    """Rename a chat, narrow which agents may answer in it, or both.

    Both optional: the UI patches one or the other. Exclusions are validated
    against the caller's own roster before storage.
    """
    name: Optional[str] = Field(default=None, min_length=1)
    excluded_agent_ids: Optional[list] = None


class SessionSummary(BaseModel):
    id: str
    name: str
    updated_at: Optional[str] = None
    # Carried in the list so the topbar can show a chat's scope without a
    # second request per chat.
    excluded_agent_ids: list = []


class ChatMessage(BaseModel):
    role: str
    text: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    answered_by: Optional[str] = None   # which agent produced an assistant turn


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


class SignupPolicyResponse(BaseModel):
    invite_required: bool


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(..., min_length=8)
    invite_code: Optional[str] = None

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


# NOTE: this module has no `from __future__ import annotations` and must not get
# one (Pydantic resolves these at class-definition time). On Python 3.9 that
# makes PEP 604 unions (`str | None`) a TypeError at import — use Optional[X].
class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=8000)
    description: str = Field(default="", max_length=500)
    model: Optional[str] = Field(default=None)
    grounding: str = Field(default="strict")
    use_general_kb: bool = Field(default=False)
    # Clamped server-side to the model's ceiling; the bound here only rejects
    # obvious nonsense early.
    max_context_tokens: Optional[int] = Field(default=None, ge=0, le=2_000_000)

    @field_validator("grounding")
    @classmethod
    def _known_grounding(cls, v: str) -> str:
        if v not in ("strict", "open"):
            raise ValueError("grounding must be 'strict' or 'open'")
        return v


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    instructions: Optional[str] = Field(default=None, max_length=8000)
    description: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = None
    grounding: Optional[str] = None
    use_general_kb: Optional[bool] = None
    max_context_tokens: Optional[int] = Field(default=None, ge=0, le=2_000_000)

    @field_validator("grounding")
    @classmethod
    def _known_grounding(cls, v):
        if v is not None and v not in ("strict", "open"):
            raise ValueError("grounding must be 'strict' or 'open'")
        return v


class AgentResponse(BaseModel):
    id: str
    name: str
    instructions: str
    description: str = ""
    model: str
    grounding: str
    use_general_kb: bool
    trained: bool
    max_context_tokens: int = 0
    max_context_ceiling: int = 0


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    model: str
    trained: bool
    updated_at: Optional[str] = None


class AgentDocument(BaseModel):
    source_id: str
    filename: Optional[str] = None
    status: Optional[str] = None
