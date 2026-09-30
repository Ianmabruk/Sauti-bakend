"""Marketplace service: the only interface the agent uses for inventory.

Every method here reads from the database. No method invents data, and every
"not found" case returns an explicit empty result rather than a guess.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from ..db import db
from .models import (
    MarketplaceCategory,
    NewsArticle,
    Product,
    Review,
    SavedItem,
    SearchEvent,
    Vendor,
)
from .query import is_discovery_request, parse_query
from .search import popular_searches, search_products

logger = logging.getLogger(__name__)

NO_VENDOR_MESSAGE = "I couldn't find a matching vendor in the current marketplace."


class MarketplaceService:
    """Read-side marketplace operations."""

    # -- discovery -----------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 20,
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        language: Optional[str] = None,
        record: bool = True,
    ) -> dict:
        """Search vendors/products and optionally log the search event."""
        parsed = parse_query(query)
        scored, parsed, diagnostics = search_products(
            query=parsed, limit=limit, category=category
        )
        items = [item.to_dict() for item in scored]

        if record:
            self.record_search(
                query=query,
                parsed=parsed,
                result_count=len(items),
                user_id=user_id,
                language=language,
            )

        return {
            "query": query,
            "parsed": parsed.to_dict(),
            "resultCount": len(items),
            "results": items,
            "diagnostics": diagnostics,
            "filters": self.suggested_filters(parsed, scored),
            "message": None if items else NO_VENDOR_MESSAGE,
        }

    def suggested_filters(self, parsed, scored) -> list[dict]:
        """Optional refinement chips, derived from the actual result set."""
        chips: list[dict] = [{"key": "all", "label": "All", "count": len(scored)}]

        if parsed.category:
            chips.append({"key": "category", "label": parsed.category.replace("_", " ").title()})

        seen: set[tuple[str, str]] = set()
        for item in scored[:20]:
            vendor = item.product.vendor
            if vendor and vendor.location:
                key = ("location", vendor.location)
                if key not in seen:
                    seen.add(key)
                    chips.append({"key": "location", "label": vendor.location})
            if vendor and vendor.is_verified:
                chips.append({"key": "verified", "label": "Verified"})
                break
        if any(item.product.is_available for item in scored):
            chips.append({"key": "available", "label": "Available"})
        if parsed.horsepower:
            chips.append({"key": "horsepower", "label": f"{parsed.horsepower} HP"})
        return chips

    def record_search(
        self,
        query: str,
        parsed,
        result_count: int,
        user_id: Optional[str] = None,
        language: Optional[str] = None,
        source: str = "search_bar",
        duration_ms: int = 0,
    ) -> None:
        event = SearchEvent(
            query_text=query,
            normalised_query=re.sub(r"\s+", " ", (query or "").strip().lower())[:400] or None,
            category=parsed.category,
            location=parsed.location,
            language=language,
            result_count=result_count,
            had_results=result_count > 0,
            user_id=user_id,
            source=source,
            duration_ms=duration_ms,
        )
        db.session.add(event)
        db.session.commit()

    def popular_searches(self, limit: int = 8) -> list[dict]:
        return popular_searches(limit=limit)

    # -- catalogue -----------------------------------------------------

    def get_vendor(self, vendor_id_or_slug: str) -> Optional[dict]:
        vendor = Vendor.query.filter(
            (Vendor.id == vendor_id_or_slug) | (Vendor.slug == vendor_id_or_slug)
        ).first()
        if not vendor:
            return None
        payload = vendor.to_dict()
        payload["products"] = [
            product.to_dict()
            for product in vendor.products
            if product.is_active
        ][:50]
        payload["reviews"] = [
            {"rating": r.rating, "body": r.body, "createdAt": r.created_at.isoformat() if r.created_at else None}
            for r in Review.query.filter_by(vendor_id=vendor.id, is_published=True)
            .order_by(Review.created_at.desc())
            .limit(10)
            .all()
        ]
        return payload

    def get_product(self, product_id: str) -> Optional[dict]:
        product = db.session.get(Product, product_id)
        if not product:
            return None
        return product.to_dict()

    def list_categories(self) -> list[dict]:
        rows = (
            MarketplaceCategory.query.filter_by(is_active=True)
            .order_by(MarketplaceCategory.sort_order)
            .all()
        )
        return [row.to_dict() for row in rows]

    def featured_vendors(self, limit: int = 6) -> list[dict]:
        vendors = (
            Vendor.query.filter_by(verification_status="APPROVED")
            .order_by(Vendor.rating.desc().nullslast(), Vendor.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for vendor in vendors:
            payload = vendor.to_dict()
            products = [p for p in vendor.products if p.is_active]
            payload["productCount"] = len(products)
            payload["sampleProduct"] = (
                products[0].display_price() if products else None
            )
            payload["image"] = vendor.cover_url or vendor.logo_url
            out.append(payload)
        return out

    def list_vendors(
        self, category: Optional[str] = None, verified_only: bool = True, limit: int = 50
    ) -> list[dict]:
        query = Vendor.query
        if category:
            query = query.filter(Vendor.category == category)
        if verified_only:
            query = query.filter(Vendor.verification_status == "APPROVED")
        rows = query.order_by(Vendor.created_at.desc()).limit(limit).all()
        return [v.to_dict(include_contact=False) for v in rows]

    # -- news ----------------------------------------------------------

    def latest_news(self, category: Optional[str] = None, limit: int = 6) -> list[dict]:
        query = NewsArticle.query.filter_by(is_published=True)
        if category:
            query = query.filter_by(category=category)
        rows = (
            query.order_by(NewsArticle.published_at.desc().nullslast(), NewsArticle.sort_order)
            .limit(limit)
            .all()
        )
        return [row.to_dict() for row in rows]

    # -- saved ---------------------------------------------------------

    def save_item(self, item_type: str, item_id: str, user_id: Optional[str]) -> dict:
        existing = SavedItem.query.filter_by(
            item_type=item_type, item_id=item_id, user_id=user_id
        ).first()
        if existing:
            return {"saved": True, "id": existing.id, "alreadySaved": True}
        item = SavedItem(item_type=item_type, item_id=item_id, user_id=user_id)
        db.session.add(item)
        db.session.commit()
        return {"saved": True, "id": item.id, "alreadySaved": False}

    def list_saved(self, user_id: Optional[str]) -> dict:
        rows = (
            SavedItem.query.filter_by(user_id=user_id)
            .order_by(SavedItem.created_at.desc())
            .all()
        )
        vendors, products = [], []
        for row in rows:
            if row.item_type == "vendor":
                vendor = db.session.get(Vendor, row.item_id)
                if vendor:
                    vendors.append(vendor.to_dict(include_contact=False))
            elif row.item_type == "product":
                product = db.session.get(Product, row.item_id)
                if product:
                    products.append(product.to_dict())
        return {"vendors": vendors, "products": products, "total": len(rows)}


_service: Optional[MarketplaceService] = None


def get_marketplace_service() -> MarketplaceService:
    global _service
    if _service is None:
        _service = MarketplaceService()
    return _service


def slugify_vendor_name(name: str) -> str:
    """URL-safe vendor slug, unique-ified if needed."""
    import re

    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "vendor"
    slug = base
    suffix = 2
    while Vendor.query.filter_by(slug=slug).first() is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug
