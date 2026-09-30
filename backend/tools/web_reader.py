"""web_reader tool: read a specific known URL."""
from __future__ import annotations

import logging

from ..config.settings import Settings, get_settings
from ..services.research.reader import ReadError, WebReader
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)


class WebReaderTool(Tool):
    """Fetch one page and extract its readable text."""

    name = "web_reader"
    description = (
        "Read a specific web page and extract its main content. Use this only "
        "when you already know the exact URL, for example to verify a figure "
        "on a specific source."
    )
    permission = PermissionLevel.SAFE
    requires_network = True

    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "maxLength": 1000,
                "description": "Absolute http(s) URL to read.",
            },
        },
        "required": ["url"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "published_at": {"type": "string"},
        },
    }

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def run(self, arguments: dict) -> ToolResult:
        url = (arguments.get("url") or "").strip()
        if not url:
            return ToolResult(tool=self.name, ok=False, error="url is required")

        try:
            source = await WebReader(self.settings).read(url)
        except ReadError as exc:
            return ToolResult(
                tool=self.name, ok=False, error=str(exc), metadata={"url": url}
            )

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "url": source.url,
                "title": source.title,
                "content": source.content[:8000],
                "published_at": source.published_at,
                "source_type": source.source_type,
            },
            sources=[source],
            metadata={"url": source.url},
        )
