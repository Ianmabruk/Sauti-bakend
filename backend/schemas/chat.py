"""Chat and source schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..schemas.sauti import SAUTI_MODES


class ChatRequest(BaseModel):
    """POST /api/chat body."""

    message: str = Field(..., min_length=1, max_length=5000)
    language: str = Field("auto", max_length=10)
    conversation_id: Optional[str] = Field(None, max_length=64)
    user_id: Optional[str] = Field(None, max_length=64)
    use_tools: bool = True
    #: Sidebar mode. Validated here rather than silently coerced downstream, so
    #: a client that sends a stale or misspelled mode hears about it instead of
    #: quietly getting different behaviour than it asked for.
    mode: str = Field("internet", max_length=20)
    #: Optional explicit location for this turn. Never persisted.
    user_location: Optional[str] = Field(None, max_length=160)

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value.lower() not in SAUTI_MODES:
            raise ValueError(f"mode must be one of {sorted(SAUTI_MODES)}")
        return value.lower()

    @field_validator("user_location")
    @classmethod
    def _clean_location(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = " ".join(value.split())[:160]
        return text or None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("message must not be blank")
        return text

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        allowed = {
            "auto", "en", "sw", "fr", "luo", "kikuyu", "kamba",
            "kisii", "meru", "samburu",
        }
        if value.lower() not in allowed:
            raise ValueError(f"language must be one of {sorted(allowed)}")
        return value.lower()


class SourceModel(BaseModel):
    """A citation returned to the client."""

    title: str
    url: str
    domain: str = ""
    date: Optional[str] = None
    accessed_at: Optional[str] = None
    source_type: str = "unknown"
    relevance: float = 0.0


class ChatResponse(BaseModel):
    """POST /api/chat response.

    `response` and the other legacy fields are preserved so existing SautiPay
    clients keep working. `message`, `used_tools` and `activity` are the
    SAUTI-native fields.
    """

    message: str
    response: str
    language: str
    is_mixed: bool = False
    reply_language: Optional[str] = None
    intent: str = "general_question"
    confidence: float = 0.0
    used_tools: list[str] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    sources: list[SourceModel] = Field(default_factory=list)
    research_performed: bool = False
    research_available: bool = True
    activity: list[str] = Field(default_factory=list)
    memory_used: list[str] = Field(default_factory=list)
    conversation_id: Optional[str] = None
    request_id: str = ""
    # Outcome signals. `engine` names the *configured* provider, so it says
    # nothing about whether that provider answered; read `engine_ok` (or the
    # single combined `degraded` flag) to decide if the reply is a real answer.
    engine: str = ""
    engine_ok: bool = True
    engine_error: str = ""
    degraded: bool = False


class ConversationSummary(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    language: Optional[str] = None
    message_count: int = 0
    created_at: Optional[str] = None
