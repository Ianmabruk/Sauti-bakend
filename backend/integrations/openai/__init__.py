"""OpenAI integration (Responses API)."""
from .responses import (  # noqa: F401
    OpenAIResponsesError,
    SautiOpenAIClient,
    build_tool_definitions,
    extract_text,
    extract_tool_calls,
    serialise_tool_results,
)

__all__ = [
    "SautiOpenAIClient",
    "OpenAIResponsesError",
    "build_tool_definitions",
    "extract_text",
    "extract_tool_calls",
    "serialise_tool_results",
]
