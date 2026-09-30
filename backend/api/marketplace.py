"""Marketplace API.

Public read endpoints for discovery, vendors, products, news and analytics.
The agent uses the same service in-process; these routes let the frontend and
third parties read the same authoritative data.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from ..agent.language import SautiLanguageDetector
from ..security import require_admin
from ..marketplace.service import get_marketplace_service
from ..schemas.marketplace import (
    SearchRequest,
    VendorCreateRequest,
    VendorUpdateRequest,
    NewsCreateRequest,
    NewsUpdateRequest,
)

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint("marketplace", __name__)
detector = SautiLanguageDetector()


def _service():
    return get_marketplace_service()


@marketplace_bp.route("/marketplace/search", methods=["POST"])
def search():
    """Hybrid search across vendors and products.

    Body: {"query": "...", "category": null, "limit": 20, "record": true}
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400
    try:
        parsed = SearchRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    service = _service()
    language = detector.detect(parsed.query)
    result = service.search(
        query=parsed.query,
        limit=parsed.limit,
        category=parsed.category,
        user_id=parsed.user_id,
        language=language,
        record=parsed.record,
    )
    result["language"] = language
    return jsonify(result)


@marketplace_bp.route("/marketplace/categories", methods=["GET"])
def categories():
    return jsonify({"categories": _service().list_categories()})


@marketplace_bp.route("/marketplace/vendors", methods=["GET"])
def vendors():
    return jsonify({
        "vendors": _service().list_vendors(
            category=request.args.get("category"),
            verified_only=request.args.get("unverified", "false").lower() != "true",
            limit=request.args.get("limit", default=50, type=int),
        )
    })


@marketplace_bp.route("/marketplace/vendors/featured", methods=["GET"])
def featured_vendors():
    return jsonify({
        "vendors": _service().featured_vendors(
            limit=request.args.get("limit", default=6, type=int)
        )
    })


@marketplace_bp.route("/marketplace/vendors/<vendor_id>", methods=["GET"])
def vendor_detail(vendor_id: str):
    vendor = _service().get_vendor(vendor_id)
    if not vendor:
        return jsonify({"error": "Vendor not found"}), 404
    return jsonify({"vendor": vendor})


@marketplace_bp.route("/marketplace/products/<product_id>", methods=["GET"])
def product_detail(product_id: str):
    product = _service().get_product(product_id)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    return jsonify({"product": product})


@marketplace_bp.route("/marketplace/popular", methods=["GET"])
def popular():
    """Popular searches, computed from recorded search events only."""
    return jsonify({
        "searches": _service().popular_searches(
            limit=request.args.get("limit", default=8, type=int)
        )
    })


@marketplace_bp.route("/marketplace/news", methods=["GET"])
def news():
    return jsonify({
        "articles": _service().latest_news(
            category=request.args.get("category"),
            limit=request.args.get("limit", default=6, type=int),
        )
    })


@marketplace_bp.route("/marketplace/saved", methods=["POST", "GET"])
def saved():
    service = _service()
    user_id = request.args.get("user_id") or (request.get_json(silent=True) or {}).get("user_id")
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not data.get("item_id") or not data.get("item_type"):
            return jsonify({"error": "item_type and item_id are required"}), 400
        return jsonify(
            service.save_item(data["item_type"], data["item_id"], data.get("user_id"))
        ), 201
    return jsonify(service.list_saved(user_id))


# ---------------------------------------------------------------------------
# Vendor onboarding
# ---------------------------------------------------------------------------

@marketplace_bp.route("/marketplace/vendors", methods=["POST"])
@require_admin
def create_vendor():
    """Register a new vendor. Starts as PENDING and is searchable as unverified.

    Administrator-gated. This was previously open to anyone, which let a
    third party insert rows that SAUTI would then quote back as real
    businesses. There is no public vendor sign-up flow in this application, so
    gating it removes an injection route without removing a feature.
    """
    from ..marketplace.service import slugify_vendor_name

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400
    try:
        parsed = VendorCreateRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    from ..db import db
    from ..marketplace.models import Vendor

    service = _service()
    if service.get_vendor(slugify_vendor_name(parsed.business_name)):
        return jsonify({"error": "A vendor with that name already exists"}), 409

    vendor = Vendor(
        business_name=parsed.business_name,
        slug=slugify_vendor_name(parsed.business_name),
        description=parsed.description,
        category=parsed.category,
        subcategories=parsed.subcategories or [],
        location=parsed.location,
        latitude=parsed.latitude,
        longitude=parsed.longitude,
        phone=parsed.phone,
        email=parsed.email,
        website=parsed.website,
        verification_status="PENDING",
        service_areas=parsed.service_areas or [],
        delivery_available=bool(parsed.delivery_available),
        accepts_mpesa=bool(parsed.accepts_mpesa),
        owner_user_id=parsed.user_id,
    )
    db.session.add(vendor)
    db.session.commit()
    logger.info("VENDOR registered id=%s slug=%s", vendor.id, vendor.slug)
    return jsonify({"vendor": vendor.to_dict(), "pendingVerification": True}), 201


# ---------------------------------------------------------------------------
# News management (admin handled separately; this is the public read surface)
# ---------------------------------------------------------------------------

@marketplace_bp.route("/marketplace/news/<slug>", methods=["GET"])
def news_detail(slug: str):
    from ..db import db
    from ..marketplace.models import NewsArticle

    article = NewsArticle.query.filter_by(slug=slug, is_published=True).first()
    if not article:
        return jsonify({"error": "Article not found"}), 404
    payload = article.to_dict()
    payload["content"] = article.content
    return jsonify({"article": payload})
