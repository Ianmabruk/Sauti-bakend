"""Place search backed by OpenStreetMap Nominatim.

Nominatim is free, needs no API key, and is the reference geocoder for
OpenStreetMap. It is the right source here because it returns real
coordinates, which the map-link tool then turns into directions.

Two rules are non-negotiable for this tool:

- **Never fabricate.** A place is only ever returned if Nominatim returned
  it. There is no fallback list of Kenyan cities or hospitals to fall back on.
- **Identify honestly.** Nominatim's usage policy requires a real
  ``User-Agent`` identifying the application and a way to contact the operator.
  Anonymous or spoofed agents get blocked.

The service also rate-limits to roughly one request per second, so calls are
serialised through a lock and a minimum interval.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from ..config.settings import Settings
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

#: Public Nominatim instance. Keyless; a self-hosted instance is preferable
#: in production.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: Identifies the application as Nominatim's policy requires.
USER_AGENT = "SautiPay-SAUTI/1.0 (https://github.com/sautipay; contact@sautipay.example)"

#: Public Nominatim allows about one request per second. Calls are spaced by
#: this much, and concurrent calls queue behind a lock.
_MIN_INTERVAL_SECONDS = 1.05

#: Serialises requests so the rate limit is respected per process.
_call_lock = asyncio.Lock()
_last_call = 0.0

#: Only these categories are useful for the kinds of place a user asks for.
#: Left unrestricted, Nominatim returns streets and house numbers, which are
#: not what "find me a hospital" means.
_POI_KEYS = (
    "amenity", "shop", "office", "tourism", "leisure", "healthcare",
    "historic", "building",
)


async def _throttle() -> None:
    """Space out Nominatim calls to respect its usage policy."""
    global _last_call
    async with _call_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


def _normalise_place(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert one Nominatim hit into a card-ready place.

    Fields absent from the upstream result are left out rather than invented,
    which is what lets the frontend omit them instead of rendering blanks.
    """
    lat = item.get("lat")
    lon = item.get("lon")
    if lat in (None, "") or lon in (None, ""):
        # Without coordinates there is nothing to build a map link from, and a
        # half-place is worse than no result.
        return None

    tags: dict[str, str] = {}
    raw_category = item.get("category") or item.get("type") or ""
    for key in _POI_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value:
            tags[key] = value

    address = item.get("address") or {}
    locality = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("suburb")
        or address.get("county")
        or address.get("state")
    )

    place: dict[str, Any] = {
        "name": item.get("name") or item.get("display_name", "").split(",")[0] or None,
        "displayName": item.get("display_name"),
        "category": raw_category or None,
        "type": item.get("type"),
        "latitude": float(lat),
        "longitude": float(lon),
        "locality": locality,
        "osmId": item.get("osm_id"),
        "osmType": item.get("osm_type"),
        "tags": tags or None,
    }

    # Optional extras, included only when the source actually supplied them.
    if tags:
        place["amenityType"] = tags.get("amenity") or tags.get("shop") or tags.get("office")
    if address.get("road"):
        place["street"] = address.get("road")
    if address.get("postcode"):
        place["postcode"] = address.get("postcode")
    if address.get("country"):
        place["country"] = address.get("country")

    if not place["name"]:
        return None
    return place


class PlaceSearchTool(Tool):
    """Find real-world places such as universities, hospitals and restaurants."""

    name = "place_search"
    description = (
        "Find real places (universities, hospitals, clinics, schools, "
        "restaurants, banks, shops, offices) using OpenStreetMap. Use this for "
        "'find a university', 'nearest hospital', 'restaurants near Westlands'. "
        "Returns real names, addresses and coordinates. Only call this for a "
        "physical venue. For a product or a vendor, use search_marketplace."
    )
    permission = PermissionLevel.SAFE
    requires_network = True

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "maxLength": 200,
                "description": "What to find and where, in plain words.",
            },
            "country": {
                "type": "string",
                "maxLength": 60,
                "description": "Country or region to bias the search, e.g. Kenya.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "Maximum places to return.",
            },
        },
        "required": ["query"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "places": {"type": "array"},
            "resultCount": {"type": "integer"},
        },
    }

    def __init__(self, settings: Optional[Settings] = None):
        from ..config.settings import get_settings

        self.settings = settings or get_settings()

    async def run(self, arguments: dict) -> ToolResult:
        """Search Nominatim for places matching the query.

        Args:
            arguments: ``query``, optional ``country`` and ``limit``.

        Returns:
            A ToolResult whose ``data`` carries ``places``. An empty result is
            reported as a successful search that found nothing, never padded
            with invented entries.
        """
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolResult(tool=self.name, ok=False, error="query is required")

        limit = int(arguments.get("limit") or 5)
        country = (arguments.get("country") or self.settings.user_country or "").strip()

        # Try the query as the user phrased it first. Nominatim resolves
        # countries on its own, and appending one unconditionally destroys
        # recall: "restaurants near Westlands" matches, while
        # "restaurants near Westlands, Nairobi, Kenya" matches nothing. The
        # country is only a fallback for a query that found nothing.
        attempts = [query]
        if country and country.lower() not in query.lower():
            attempts.append(f"{query}, {country}")

        places: list[dict] = []
        failure: Optional[str] = None
        for attempt_query in attempts:
            places, failure = await self._search(attempt_query, limit)
            if places:
                places = _attach_links(places)
                break

        # A failed lookup and a lookup that genuinely found nothing are
        # different answers. Conflating them would tell a user who was merely
        # rate limited that the place does not exist.
        if failure and not places:
            return ToolResult(tool=self.name, ok=False, error=failure)

        logger.info("place_search query=%r attempts=%d results=%d", query, len(attempts), len(places))

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "query": query,
                "resultCount": len(places),
                "places": places,
                "message": None if places else "No matching places were found.",
            },
            metadata={"provider": "openstreetmap-nominatim"},
        )

    async def _search(
        self, query: str, limit: int
    ) -> tuple[list[dict], Optional[str]]:
        """Run one Nominatim query.

        Returns:
            ``(places, failure)``. ``failure`` is a user-safe explanation when
            the lookup could not be performed, and None when it completed
            (whether or not anything matched).
        """
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": str(max(1, min(10, limit))),
            "addressdetails": "1",
            "extratags": "1",
            "namedetails": "1",
        }

        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
                )
            response.raise_for_status()
            items = response.json()
        except httpx.TimeoutException:
            logger.warning("place_search timed out query=%r", query)
            return [], "The place search timed out. Please try again."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("place_search HTTP %s query=%r", status, query)
            if status == 429:
                return [], "The place search is rate limited. Please try again shortly."
            return [], "The place search is unavailable right now."
        except httpx.HTTPError as exc:
            logger.warning("place_search transport error: %s", type(exc).__name__)
            return [], "Could not reach the place search service."
        except ValueError:
            logger.warning("place_search unreadable reply query=%r", query)
            return [], "The place search returned an unreadable reply."

        if not isinstance(items, list):
            return [], "The place search returned an unexpected reply."
        return [p for p in (_normalise_place(i) for i in items) if p], None


def _attach_links(places: list[dict]) -> list[dict]:
    """Add map links to each place, using the coordinates already in hand.

    Doing this here rather than waiting for the model to call ``source_links``
    is deliberate: the coordinates have just been retrieved, so building the
    links is free and deterministic. Relying on a second model round-trip made
    the Directions button appear only sometimes.
    """
    from .source_links import build_links

    for place in places:
        links = build_links(
            place.get("latitude"), place.get("longitude"), name=place.get("name") or ""
        )
        if links:
            place["links"] = links
    return places
