"""Marketplace tools exposed to the SAUTI agent.

These are the only way the model learns about vendors and products. It never
invents them: it requests a tool, the tool reads the database, and the answer
is assembled from that result.
"""
from __future__ import annotations

import re

import logging

from ..config.settings import Settings
from ..tools.base import PermissionLevel, Tool, ToolResult
from .service import NO_VENDOR_MESSAGE, get_marketplace_service

logger = logging.getLogger(__name__)


class SearchMarketplaceTool(Tool):
    """Find vendors and products matching a natural-language request."""

    name = "search_marketplace"
    description = (
        "Search Sauti's own vendor and product database. Use this whenever the "
        "user wants to find a business, product, service or listing, or asks "
        "about a current price for something Sauti sells. This is the "
        "authoritative source for inventory, prices, availability, location and "
        "vendor verification. Never answer those questions without calling this."
    )
    permission = PermissionLevel.SAFE
    requires_network = False

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "maxLength": 300,
                "description": "The user's request in their own words.",
            },
            "category": {
                "type": "string",
                "maxLength": 60,
                "description": "Optional category filter, e.g. vehicles.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
                "description": "Maximum results to return.",
            },
        },
        "required": ["query"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "resultCount": {"type": "integer"},
            "results": {"type": "array"},
            "message": {"type": "string"},
        },
    }

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    async def run(self, arguments: dict) -> ToolResult:
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolResult(tool=self.name, ok=False, error="query is required")

        service = get_marketplace_service()
        result = service.search(
            query=query,
            limit=int(arguments.get("limit") or 10),
            category=arguments.get("category"),
            language=None,
            record=True,
        )

        if result["resultCount"] == 0:
            # An empty result is a valid, non-error outcome. The agent must
            # report it and must not substitute a guess.
            return ToolResult(
                tool=self.name,
                ok=True,
                data={
                    "resultCount": 0,
                    "results": [],
                    "message": NO_VENDOR_MESSAGE,
                    "parsed": result["parsed"],
                },
                metadata={"empty": True},
            )

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "resultCount": result["resultCount"],
                "results": result["results"],
                "filters": result["filters"],
                "parsed": result["parsed"],
            },
            metadata={"count": result["resultCount"]},
        )


class GetVendorProfileTool(Tool):
    """Read one vendor's full public profile."""

    name = "get_vendor_profile"
    description = (
        "Get a vendor's public profile: description, verification status, "
        "location, service areas, contact details and their listings."
    )
    permission = PermissionLevel.SAFE

    input_schema = {
        "type": "object",
        "properties": {
            "vendor_id": {"type": "string", "minLength": 2, "maxLength": 120},
        },
        "required": ["vendor_id"],
    }

    output_schema = {"type": "object", "properties": {"vendor": {"type": "object"}}}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    async def run(self, arguments: dict) -> ToolResult:
        vendor_id = (arguments.get("vendor_id") or "").strip()
        vendor = get_marketplace_service().get_vendor(vendor_id)
        if not vendor:
            return ToolResult(
                tool=self.name,
                ok=True,
                data={"found": False, "message": "No vendor with that identifier."},
            )
        return ToolResult(tool=self.name, ok=True, data={"found": True, "vendor": vendor})


class GetProductDetailsTool(Tool):
    """Read one product's specifications, price and availability."""

    name = "get_product_details"
    description = (
        "Get exact details for a listing: price, currency, availability, "
        "specifications, location and the vendor. Use after a search when the "
        "user wants specifics about one item."
    )
    permission = PermissionLevel.SAFE

    input_schema = {
        "type": "object",
        "properties": {"product_id": {"type": "string", "minLength": 2, "maxLength": 120}},
        "required": ["product_id"],
    }

    output_schema = {"type": "object", "properties": {"product": {"type": "object"}}}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    async def run(self, arguments: dict) -> ToolResult:
        product_id = (arguments.get("product_id") or "").strip()
        product = get_marketplace_service().get_product(product_id)
        if not product:
            return ToolResult(
                tool=self.name, ok=True, data={"found": False, "message": "Product not found."}
            )
        return ToolResult(tool=self.name, ok=True, data={"found": True, "product": product})


class SearchNewsTool(Tool):
    """Read news Sauti has published, from its own database."""

    name = "search_news"
    description = (
        "Search Sauti's own news database for published articles. Use for "
        "questions about stories Sauti has already published. For live breaking "
        "news, use web_search instead."
    )
    permission = PermissionLevel.SAFE

    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 200},
            "category": {"type": "string", "maxLength": 60},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6},
        },
        "required": [],
    }

    output_schema = {"type": "object", "properties": {"articles": {"type": "array"}}}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    async def run(self, arguments: dict) -> ToolResult:
        service = get_marketplace_service()
        articles = service.latest_news(
            category=arguments.get("category"),
            limit=int(arguments.get("limit") or 6),
        )

        # `query` may be a whole question, not keywords. Filter on meaningful
        # token overlap rather than substring-matching the entire sentence.
        query = (arguments.get("query") or "").lower()
        tokens = {t for t in re.findall(r"[\w'-]+", query) if len(t) > 3}
        if tokens and articles:
            filtered = [
                a for a in articles
                if any(
                    token in f"{a.get('title', '')} {a.get('summary', '')}".lower()
                    for token in tokens
                )
            ]
            # A question with no topical overlap should not hide every article.
            articles = filtered or articles

        return ToolResult(
            tool=self.name, ok=True, data={"articles": articles, "count": len(articles)}
        )
