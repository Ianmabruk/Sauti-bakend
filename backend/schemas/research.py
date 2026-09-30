"""Research schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .chat import SourceModel


class ResearchRequest(BaseModel):
    """POST /api/research body."""

    query: str = Field(..., min_length=2, max_length=1000)
    language: str = Field("auto", max_length=10)
    read_pages: bool = True
    max_results: Optional[int] = Field(None, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("query must not be blank")
        return text


class ResearchResponse(BaseModel):
    """POST /api/research response."""

    query: str
    search_query: str
    ok: bool
    answer_available: bool = False
    provider: Optional[str] = None
    sources: list[SourceModel] = Field(default_factory=list)
    extractions: list[dict] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0


class SearchProbeResponse(BaseModel):
    """GET /api/research/status response."""

    available: bool
    provider: Optional[str] = None
    requires_key: bool = False
    live_probe: Optional[str] = None
    probe_ms: Optional[int] = None
    error: Optional[str] = None
    llm_configured: bool = False
    model_provider: str = "mock"
