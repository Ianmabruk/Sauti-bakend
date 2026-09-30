"""SAUTI tool schemas."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    """POST /api/tools/execute body.

    Exposed for debugging and for future UI tool panels. The agent never calls
    this endpoint directly; it uses the registry in-process.
    """

    name: str = Field(..., min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResponse(BaseModel):
    tool: str
    ok: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: int = 0
    metadata: dict = Field(default_factory=dict)


class ToolDescriptor(BaseModel):
    name: str
    description: str
    permission: str
    input_schema: dict
    output_schema: dict = Field(default_factory=dict)
    requires_network: bool = False
