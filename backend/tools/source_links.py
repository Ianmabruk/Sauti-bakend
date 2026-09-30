"""Build map and website links from verified place data.

The single rule: **a link is only ever built from a coordinate the place search
actually returned.** There is no geocoding guesswork and no hard-coded
latitude table, so a link can never point at the wrong building.

Three targets are supported:

- OpenStreetMap, which is the source of the coordinates in the first place.
- Google Maps, for users who want directions in a familiar app.
- Apple Maps, for iOS users.

Links for a place with no coordinates are simply omitted rather than
approximated.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..config.settings import Settings
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

#: OSM is the canonical viewer for OSM data.
OSM_URL = "https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"


def _fmt(value: float) -> str:
    """Format a coordinate for a URL without excess precision.

    Six decimal places is about 10 cm, which is more than enough to open a map
    on the right building, and keeps the URL short.
    """
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def build_links(latitude: float, longitude: float, *, name: str = "") -> dict[str, str]:
    """Build map links for one coordinate.

    Args:
        latitude: Verified latitude.
        longitude: Verified longitude.
        name: Optional place name, used as a label in some targets.

    Returns:
        A mapping of link kind to URL. Empty when the coordinate is unusable.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return {}

    # Guard against a null island or an obviously broken value, which would
    # otherwise produce a confident-looking link to the middle of the ocean.
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return {}
    if lat == 0.0 and lon == 0.0:
        return {}

    lat_s, lon_s = _fmt(lat), _fmt(lon)
    label = (name or "").strip()

    return {
        "openstreetmap": OSM_URL.format(lat=lat_s, lon=lon_s),
        "googleMaps": f"https://www.google.com/maps/search/?api=1&query={lat_s},{lon_s}",
        "appleMaps": f"https://maps.apple.com/?q={label or f'{lat_s},{lon_s}'}"
        f"&ll={lat_s},{lon_s}",
    }


def _place_payload(payload: dict, limit: int) -> list[dict]:
    """Extract place records from whichever tool produced them."""
    for key in ("places", "results", "vendors"):
        items = payload.get(key)
        if isinstance(items, list):
            return items[:limit]
    return []


class SourceLinksTool(Tool):
    """Attach map links to places that already have verified coordinates."""

    name = "source_links"
    description = (
        "Build map and directions links for places returned by place_search. "
        "Use this after a place search to give the user a way to navigate. "
        "It only works on places that have real coordinates; it never guesses "
        "a location."
    )
    permission = PermissionLevel.SAFE
    requires_network = False

    input_schema = {
        "type": "object",
        "properties": {
            "places": {
                "type": "array",
                "maxItems": 10,
                "description": (
                    "Place objects, each with latitude and longitude, exactly "
                    "as returned by place_search."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                    },
                    "required": ["latitude", "longitude"],
                },
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["places"],
    }

    output_schema = {
        "type": "object",
        "properties": {"linked": {"type": "array"}, "resultCount": {"type": "integer"}},
    }

    def __init__(self, settings: Optional[Settings] = None):
        from ..config.settings import get_settings

        self.settings = settings or get_settings()

    async def run(self, arguments: dict) -> ToolResult:
        """Attach links to each supplied place that has coordinates.

        Args:
            arguments: ``places`` (objects with latitude/longitude) and an
                optional ``limit``.

        Returns:
            A ToolResult whose ``data`` carries ``linked``. Places without
            usable coordinates are skipped and reported in ``skipped``.
        """
        places = arguments.get("places")
        if not isinstance(places, list) or not places:
            return ToolResult(
                tool=self.name, ok=False, error="places must be a non-empty array"
            )

        limit = int(arguments.get("limit") or 5)
        linked: list[dict] = []
        skipped = 0

        for place in places[:limit]:
            if not isinstance(place, dict):
                skipped += 1
                continue
            lat = place.get("latitude", place.get("lat"))
            lon = place.get("longitude", place.get("lon"))
            links = build_links(lat, lon, name=str(place.get("name") or ""))
            if not links:
                skipped += 1
                continue
            entry = dict(place)
            entry["links"] = links
            linked.append(entry)

        logger.info("source_links linked=%d skipped=%d", len(linked), skipped)
        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "linked": linked,
                "resultCount": len(linked),
                "skipped": skipped,
                "message": None
                if linked
                else "None of these places had verified coordinates.",
            },
        )
