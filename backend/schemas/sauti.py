"""Schemas for the Sauti AI endpoint."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

#: Sidebar modes the client may request.
SAUTI_MODES = {"internet", "education", "business"}


class SautiChatRequest(BaseModel):
    """POST /api/sauti/chat body."""

    message: str = Field(..., min_length=1, max_length=8000)
    language: str = Field("auto", max_length=10)
    conversation_id: Optional[str] = Field(None, max_length=64)
    user_id: Optional[str] = Field(None, max_length=64)
    use_tools: bool = True
    #: Sidebar mode. Applied as a soft bias on routing, never as a lock.
    mode: str = Field("internet", max_length=20)
    #: Optional explicit location, used for this turn only. Overrides the
    #: configured default city. It is never persisted.
    user_location: Optional[str] = Field(None, max_length=160)
    #: Optional client-supplied history, used when the caller has no
    #: conversation_id. Ignored when a conversation_id is present.
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=30)

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
