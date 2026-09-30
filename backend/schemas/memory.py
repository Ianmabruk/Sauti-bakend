"""Memory schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    """POST /api/memory body."""

    content: str = Field(..., min_length=1, max_length=2000)
    category: str = Field("context", max_length=32)
    key: Optional[str] = Field(None, max_length=160)
    user_id: Optional[str] = Field(None, max_length=64)
    conversation_id: Optional[str] = Field(None, max_length=64)
    language: Optional[str] = Field(None, max_length=10)
    importance: float = Field(0.5, ge=0.0, le=1.0)


class MemoryForgetRequest(BaseModel):
    """DELETE /api/memory body."""

    memory_id: Optional[str] = Field(None, max_length=64)
    key: Optional[str] = Field(None, max_length=160)
    user_id: Optional[str] = Field(None, max_length=64)


class MemoryResponse(BaseModel):
    id: str
    category: str
    key: Optional[str] = None
    content: str
    language: Optional[str] = None
    importance: float
    created_at: Optional[str] = None
    access_count: int = 0


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int
