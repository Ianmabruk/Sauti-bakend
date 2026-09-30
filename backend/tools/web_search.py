"""web_search tool: current information from the internet."""
from __future__ import annotations

import logging

from ..config.settings import Settings, get_settings
from ..services.research.pipeline import ResearchPipeline
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchTool(Tool):
    """Search the web and return structured, citable results."""

    name = "web_search"
    description = (
        "Search the internet for current or recent information. Use this for "
        "prices, news, weather, exchange rates, laws, product or vehicle "
        "availability, and anything that changes over time."
    )
    permission = PermissionLevel.SAFE
    requires_network = True

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "maxLength": 300,
                "description": "A focused web search phrase.",
            },
            "read_pages": {
                "type": "boolean",
                "default": True,
                "description": "Whether to fetch full page text for the best results.",
            },
        },
        "required": ["query"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "sources": {"type": "array"},
            "extractions": {"type": "array"},
            "conflicts": {"type": "array"},
            "error": {"type": "string"},
        },
    }

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def run(self, arguments: dict) -> ToolResult:
        query = (arguments.get("query") or "").strip()
        read_pages = arguments.get("read_pages", True)
        if not query:
            return ToolResult(tool=self.name, ok=False, error="query is required")

        result = await ResearchPipeline(self.settings).run(
            query, read_pages=bool(read_pages)
        )

        if not result.ok:
            # Honest failure: no fabricated data.
            return ToolResult(
                tool=self.name,
                ok=False,
                error=result.error or "Web research failed.",
                duration_ms=result.duration_ms,
                metadata={
                    "provider": result.provider,
                    "activity": result.activity,
                },
            )

        if not result.sources:
            return ToolResult(
                tool=self.name,
                ok=True,
                data={"sources": [], "message": "No sources were found."},
                duration_ms=result.duration_ms,
                metadata={"provider": result.provider},
            )

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "query": result.query,
                "search_query": result.search_query,
                "sources": result.to_dict()["sources"],
                "extractions": result.extractions,
                "observations": result.observations,
                "conflicts": result.conflicts,
            },
            sources=result.sources,
            duration_ms=result.duration_ms,
            metadata={
                "provider": result.provider,
                "searched_count": result.searched_count,
                "read_count": result.read_count,
                "activity": result.activity,
            },
        )
