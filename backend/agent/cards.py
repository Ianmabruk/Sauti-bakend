"""Typed result cards.

The spec requires four visually distinct card types rather than one generic
result blob. A card is a small, self-describing object the frontend can render
without knowing anything about the tool that produced it:

    {"type": "place"|"product"|"study"|"business", "data": {...}}

Cards are derived strictly from tool output. Nothing is filled in from the
model's prose and nothing is invented: a place card only exists if the place
search returned that place with real coordinates, and a field the source did
not supply is simply absent so the frontend can omit it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from ..tools.base import ToolResult

logger = logging.getLogger(__name__)

#: Cap on how many cards of one kind are returned, so a broad search cannot
#: produce a wall of identical cards.
MAX_CARDS = 8


def _as_dict(value: Any) -> dict:
    """Coerce a tool result payload into a dict, or return an empty one."""
    return value if isinstance(value, dict) else {}


def _string(payload: dict, *keys: str, limit: int = 400) -> Optional[str]:
    """First non-empty string among ``keys``, trimmed to ``limit``."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text[:limit]
    return None


def _number(payload: dict, *keys: str) -> Optional[float]:
    """First numeric value among ``keys``, or None."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _place_card(place: dict) -> Optional[dict]:
    """Build a place card, or None when the place is unusable.

    Coordinates are required. A place card without them cannot offer
    directions, and a card the user cannot act on is worse than none.
    """
    latitude = place.get("latitude", place.get("lat"))
    longitude = place.get("longitude", place.get("lon"))
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None

    name = _string(place, "name", "displayName", "title")
    if not name:
        return None

    data: dict[str, Any] = {
        "name": name,
        "latitude": float(latitude),
        "longitude": float(longitude),
    }

    optional = {
        "locality": _string(place, "locality", "city", "town", "village", "location"),
        "region": _string(place, "region", "state", "county"),
        "country": _string(place, "country"),
        "category": _string(place, "category", "type", "amenityType"),
        "street": _string(place, "street", "road", "address", limit=200),
        "fullAddress": _string(place, "displayName", limit=300),
        "phone": _string(place, "phone", "contact:phone", "telephone"),
        "website": _string(place, "website", "contact:website", "url"),
        "openingHours": _string(place, "openingHours", "opening_hours"),
    }
    # Only include what the source actually provided.
    data.update({k: v for k, v in optional.items() if v})

    links = place.get("links")
    if isinstance(links, dict) and links:
        data["links"] = {k: v for k, v in links.items() if isinstance(v, str) and v}

    return {"type": "place", "data": data}


def _product_card(item: dict) -> Optional[dict]:
    """Build a product card from a marketplace result.

    The marketplace returns camelCase keys and nests the vendor as an object
    under ``vendor``; both are flattened here so the card shape stays stable
    regardless of which tool produced the payload.
    """
    title = _string(item, "name", "title", "productName")
    if not title:
        return None

    # The vendor can arrive flattened (vendorName) or nested (vendor object).
    vendor_obj = item.get("vendor") if isinstance(item.get("vendor"), dict) else {}

    data: dict[str, Any] = {"title": title}
    optional = {
        "vendor": _string(item, "vendorName") or _text(vendor_obj.get("businessName"), limit=160),
        "vendorSlug": _text(vendor_obj.get("slug"), limit=160)
        or _string(item, "vendorSlug", "vendor_slug"),
        "vendorPhone": _text(vendor_obj.get("phone"), limit=40)
        or _string(item, "vendorPhone", "phone"),
        "vendorWebsite": _text(vendor_obj.get("website"), limit=300),
        "location": _string(item, "location", "area", "city")
        or _text(vendor_obj.get("location"), limit=160),
        "priceLabel": _string(item, "price", "priceLabel", "price_label", limit=60),
        "category": _string(item, "category"),
        "subcategory": _string(item, "subcategory"),
        "availability": _string(item, "availability"),
    }
    data.update({k: v for k, v in optional.items() if v is not None and v != ""})

    # Images arrive as a list; take the first usable entry only.
    images = item.get("images")
    if isinstance(images, list) and images:
        first = _text(images[0], limit=500)
        if first:
            data["image"] = first
    elif _string(item, "image", "logo", "logoUrl", limit=500):
        data["image"] = _string(item, "image", "logo", "logoUrl", limit=500)

    # Verification is reported as isVendorVerified on the product.
    verified = item.get("isVendorVerified", item.get("verified"))
    if isinstance(verified, bool):
        data["verified"] = verified

    rating = _number(item, "rating") or _number(vendor_obj, "rating")
    if rating is not None:
        data["rating"] = rating

    accepts = item.get("acceptsMpesa", vendor_obj.get("acceptsMpesa"))
    if isinstance(accepts, bool):
        data["acceptsMpesa"] = accepts

    if item.get("id"):
        data["id"] = str(item["id"])

    # The raw amount is passed through for programmatic consumers. Note it is
    # NOT minor units: the marketplace stores whole shillings, and a client that
    # divides by 100 would turn KSh 14,500 into KSh 145. `priceLabel` is the
    # field to display.
    price_amount = item.get("priceMinor", item.get("price_minor"))
    if isinstance(price_amount, (int, float)) and not isinstance(price_amount, bool):
        data["priceMinor"] = int(price_amount)
        data["currency"] = item.get("currency", "KES")

    return {"type": "product", "data": data}


def _study_card(material: dict) -> Optional[dict]:
    """Build a study card from generated material."""
    topic = _string(material, "topic", "title")
    if not topic:
        return None

    data: dict[str, Any] = {
        "topic": topic,
        "level": _string(material, "level") or "secondary",
    }
    overview = _string(material, "overview", limit=900)
    if overview:
        data["overview"] = overview

    concepts = [
        {
            "term": _string(c, "term", "name", "title", limit=120),
            "explanation": _string(c, "explanation", "definition", "detail", limit=400),
        }
        for c in (material.get("keyConcepts") or [])
        if isinstance(c, dict)
    ]
    data["keyConcepts"] = [c for c in concepts if c["term"]][:8]

    notes = _text_list(material.get("notes"), limit=800, cap=6)
    data["notes"] = notes

    questions = [
        {
            "question": _string(q, "question", limit=400),
            "answer": _string(q, "answer", limit=600),
            "explanation": _string(q, "explanation", limit=500),
        }
        for q in (material.get("practiceQuestions") or [])
        if isinstance(q, dict)
    ]
    data["practiceQuestions"] = [q for q in questions if q["question"]][:20]

    reading = _text_list(material.get("furtherReading"), limit=200, cap=6)
    data["furtherReading"] = reading

    return {"type": "study", "data": data}


def _business_card(plan: dict) -> Optional[dict]:
    """Build a business card from a generated plan."""
    business = _string(plan, "business", "name", "title")
    if not business:
        return None

    data: dict[str, Any] = {"business": business}
    optional = {
        "location": _string(plan, "location", limit=200),
        "goal": _string(plan, "goal", limit=300),
        "stage": _string(plan, "stage"),
    }
    data.update({k: v for k, v in optional.items() if v})

    for key, cap in (
        ("targetCustomers", 8), ("marketing", 8),
        ("pricing", 6), ("thirtyDayPlan", 8),
    ):
        data[key] = _text_list(plan.get(key), limit=300, cap=cap)

    week = []
    for day in (plan.get("sevenDayPlan") or [])[:7]:
        if isinstance(day, dict):
            label = _string(day, "day", limit=40)
            action = _string(day, "action", limit=300)
            if action:
                week.append({"day": label or f"Day {len(week) + 1}", "action": action})
        elif isinstance(day, str):
            week.append({"day": f"Day {len(week) + 1}", "action": day[:300]})
    data["sevenDayPlan"] = week

    return {"type": "business", "data": data}


#: Tool name to the card builder that understands its payload.
_CARD_BUILDERS = {
    "place_search": ("place", _place_card),
    "source_links": ("place", _place_card),
    "search_marketplace": ("product", _product_card),
    "get_product_details": ("product", _product_card),
    "study_generator": ("study", _study_card),
    "business_advisor": ("business", _business_card),
}


def _text(value: Any, *, limit: int = 400) -> Optional[str]:
    """Normalise a plain string value, dropping blanks and over-long text.

    Distinct from :func:`_string`, which looks a key up *inside* a dict.
    Passing a bare string to :func:`_string` finds no keys and silently yields
    None, which is how a whole section of generated material once vanished
    from the card.
    """
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] if collapsed else None


def _text_list(values: Any, *, limit: int = 400, cap: int = 8) -> list[str]:
    """Normalise a list of plain strings, dropping unusable entries."""
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _text(value, limit=limit)
        if text:
            out.append(text)
        if len(out) >= cap:
            break
    return out


def _card_key(card: dict) -> str:
    """A content-based identity for a card, used to suppress duplicates."""
    data = card.get("data", {})
    return "|".join(
        str(data.get(k))
        for k in ("name", "title", "topic", "business", "latitude", "longitude")
    )


def cards_from_results(results: Sequence[ToolResult]) -> list[dict]:
    """Build typed cards from a turn's tool results.

    Args:
        results: The tools that ran.

    Returns:
        A list of card dicts, one per usable item, de-duplicated by a
        content-based key so the same place is not shown twice when both
        ``place_search`` and ``source_links`` produced it.
    """
    cards: list[dict] = []
    seen: set[str] = set()

    for result in results:
        if not result.ok:
            continue
        entry = _CARD_BUILDERS.get(result.tool)
        if entry is None:
            continue
        _, builder = entry
        payload = _as_dict(result.data)

        # These tools wrap their items in a list; link results can also carry
        # vendor-shaped entries, which are not places.
        if result.tool == "source_links":
            items = payload.get("linked") or []
            if not items:
                continue
            for item in items[:MAX_CARDS]:
                if not isinstance(item, dict):
                    continue
                card = builder(item)
                if not card:
                    continue
                # A linked place is usually the same place place_search
                # already produced. Skipping it would silently drop the map
                # links, so the links are merged into the existing card.
                existing = next(
                    (
                        c
                        for c in cards
                        if c["type"] == "place" and c["data"].get("name") == card["data"].get("name")
                    ),
                    None,
                )
                if existing is not None:
                    links = card["data"].get("links")
                    if links:
                        existing["data"]["links"] = {
                            **(existing["data"].get("links") or {}), **links
                        }
                    seen.add(_card_key(existing))
                    continue

                key = _card_key(card)
                if key in seen:
                    # Already emitted by an earlier tool in the same turn.
                    prior = next(
                        (
                            c
                            for c in cards
                            if c["type"] == card["type"] and _card_key(c) == key
                        ),
                        None,
                    )
                    if prior is not None and card["data"].get("links"):
                        prior["data"]["links"] = {
                            **(prior["data"].get("links") or {}), **card["data"]["links"]
                        }
                    continue
                seen.add(key)
                cards.append(card)
            continue

        if result.tool in {"search_marketplace", "get_product_details"}:
            items = payload.get("results") or (
                [payload] if payload.get("name") else []
            )
        else:
            items = (
                payload.get("places")
                or payload.get("linked")
                or [payload]
            )

        for item in items[:MAX_CARDS]:
            if not isinstance(item, dict):
                continue
            card = builder(item)
            if not card:
                continue
            key = _card_key(card)
            if key in seen:
                continue
            seen.add(key)
            cards.append(card)

    if len(cards) > MAX_CARDS:
        cards = cards[:MAX_CARDS]
    logger.info("built %d card(s) from %d tool result(s)", len(cards), len(results))
    return cards
