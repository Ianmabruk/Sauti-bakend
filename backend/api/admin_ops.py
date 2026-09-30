"""Administrative operations API.

The content CMS in :mod:`backend.api.cms` manages marketing copy. This module
covers what the running system is *made of*: the marketplace inventory SAUTI
actually sells, what the agent has remembered, and whether the AI engine is
healthy. None of that had any surface, so an operator could edit the homepage
but could not see why an answer was wrong or fix a mispriced product.

Every route here is behind :func:`require_admin`, matching the CMS.

Read routes never return secrets: engine status reports whether a key is
configured, never its value.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from ..config.settings import get_settings
from ..db import db
from ..marketplace.models import Product, Vendor
from ..memory.models import MemoryItem
from ..security import require_admin

logger = logging.getLogger(__name__)

admin_ops_bp = Blueprint("admin_ops", __name__)

#: How many marketplace rows one page may return.
_MAX_PAGE_SIZE = 100


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce a JSON value to a bool, tolerating strings from a form."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default if value is None else bool(value)


# ----------------------------------------------------------------------
# Marketplace
# ----------------------------------------------------------------------


@admin_ops_bp.route("/admin/vendors", methods=["GET"])
@require_admin
def list_vendors():
    """List every vendor with its product count and verification state."""
    page = max(1, request.args.get("page", 1, type=int))
    size = min(_MAX_PAGE_SIZE, max(1, request.args.get("size", 25, type=int)))
    search = (request.args.get("q") or "").strip()

    query = Vendor.query
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            db_or(
                Vendor.business_name.ilike(pattern),
                Vendor.category.ilike(pattern),
                Vendor.location.ilike(pattern),
            )
        )
    total = query.count()
    vendors = (
        query.order_by(Vendor.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    return jsonify(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": v.id,
                    "businessName": v.business_name,
                    "slug": v.slug,
                    "category": v.category,
                    "location": v.location,
                    "phone": v.phone,
                    "verificationStatus": v.verification_status,
                    "rating": v.rating,
                    "acceptsMpesa": v.accepts_mpesa,
                    "deliveryAvailable": v.delivery_available,
                    "productCount": Product.query.filter_by(vendor_id=v.id).count(),
                    "createdAt": v.created_at.isoformat() if v.created_at else None,
                }
                for v in vendors
            ],
        }
    )


@admin_ops_bp.route("/admin/vendors/<vendor_id>", methods=["PATCH"])
@require_admin
def update_vendor(vendor_id: str):
    """Update the editable fields of a vendor.

    Deliberately narrow: identity fields (slug, verification) are not editable
    through this endpoint, because changing a slug breaks shared vendor links
    and verification is an evidence process, not a text field.
    """
    vendor = Vendor.query.get(vendor_id)
    if vendor is None:
        return jsonify({"error": "Vendor not found"}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    editable = {
        "businessName": ("business_name", str),
        "description": ("description", str),
        "category": ("category", str),
        "location": ("location", str),
        "phone": ("phone", str),
        "email": ("email", str),
        "website": ("website", str),
        "openingHours": ("opening_hours", str),
    }
    changed: list[str] = []
    for field, (column, caster) in editable.items():
        if field in data:
            value = data[field]
            if value is not None and not isinstance(value, str):
                value = caster(value)
            setattr(vendor, column, (value or "").strip() or None)
            changed.append(field)

    for field in ("acceptsMpesa", "deliveryAvailable"):
        if field in data:
            setattr(vendor, _snake(field), _as_bool(data[field]))
            changed.append(field)

    if not changed:
        return jsonify({"error": "No editable fields supplied"}), 400

    db.session.commit()
    logger.info("ADMIN vendor updated id=%s fields=%s", vendor_id, ",".join(changed))
    return jsonify({"ok": True, "id": vendor_id, "updated": changed})


@admin_ops_bp.route("/admin/products", methods=["GET"])
@require_admin
def list_products():
    """List products, with the owning vendor joined in for display."""
    page = max(1, request.args.get("page", 1, type=int))
    size = min(_MAX_PAGE_SIZE, max(1, request.args.get("size", 25, type=int)))
    search = (request.args.get("q") or "").strip()

    query = Product.query
    if search:
        query = query.filter(Product.name.ilike(f"%{search}%"))
    total = query.count()
    products = (
        query.order_by(Product.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    vendor_names = {
        v.id: v.business_name for v in Vendor.query.filter(
            Vendor.id.in_([p.vendor_id for p in products])
        ).all()
    } if products else {}

    return jsonify(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": p.id,
                    "name": p.name,
                    "vendorId": p.vendor_id,
                    "vendorName": vendor_names.get(p.vendor_id),
                    "category": p.category,
                    "priceMinor": p.price_minor,
                    "currency": p.currency,
                    "availability": p.availability,
                    "isActive": p.is_active,
                    "createdAt": p.created_at.isoformat() if p.created_at else None,
                }
                for p in products
            ],
        }
    )


@admin_ops_bp.route("/admin/products/<product_id>", methods=["PATCH"])
@require_admin
def update_product(product_id: str):
    """Update a product's name, category, price, availability and state."""
    product = Product.query.get(product_id)
    if product is None:
        return jsonify({"error": "Product not found"}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    changed: list[str] = []
    for field, column in (
        ("name", "name"),
        ("description", "description"),
        ("category", "category"),
        ("subcategory", "subcategory"),
        ("availability", "availability"),
    ):
        if field in data and data[field] is not None:
            setattr(product, column, str(data[field]).strip())
            changed.append(field)

    if "priceMinor" in data and data["priceMinor"] is not None:
        try:
            product.price_minor = int(data["priceMinor"])
            changed.append("priceMinor")
        except (TypeError, ValueError):
            return jsonify({"error": "priceMinor must be a whole number"}), 400

    if "currency" in data and data["currency"]:
        product.currency = str(data["currency"]).strip()[:8]
        changed.append("currency")

    for field in ("isActive", "isFeatured"):
        if field in data:
            setattr(product, _snake(field), _as_bool(data[field]))
            changed.append(field)

    if not changed:
        return jsonify({"error": "No editable fields supplied"}), 400

    db.session.commit()
    logger.info("ADMIN product updated id=%s fields=%s", product_id, ",".join(changed))
    return jsonify({"ok": True, "id": product_id, "updated": changed})


# ----------------------------------------------------------------------
# Agent memory
# ----------------------------------------------------------------------


@admin_ops_bp.route("/admin/memory", methods=["GET"])
@require_admin
def list_memory():
    """List everything the agent has remembered, newest first.

    Generated artifacts are marked so an operator can tell them apart from
    ordinary user memories.
    """
    page = max(1, request.args.get("page", 1, type=int))
    size = min(_MAX_PAGE_SIZE, max(1, request.args.get("size", 50, type=int)))
    category = (request.args.get("category") or "").strip()

    query = MemoryItem.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    total = query.count()
    items = (
        query.order_by(MemoryItem.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    return jsonify(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": item.id,
                    "category": item.category,
                    "language": item.language,
                    "summary": (item.extra_metadata or {}).get("summary")
                    or (item.content or "")[:160],
                    "artifact": (item.extra_metadata or {}).get("artifact"),
                    "accessCount": item.access_count,
                    "createdAt": item.created_at.isoformat() if item.created_at else None,
                }
                for item in items
            ],
        }
    )


@admin_ops_bp.route("/admin/memory/<item_id>", methods=["DELETE"])
@require_admin
def forget_memory(item_id: str):
    """Delete a memory. Used to honour a request that the agent forget something."""
    item = MemoryItem.query.get(item_id)
    if item is None:
        return jsonify({"error": "Memory not found"}), 404
    db.session.delete(item)
    db.session.commit()
    logger.info("ADMIN memory deleted id=%s category=%s", item_id, item.category)
    return jsonify({"ok": True, "id": item_id})


# ----------------------------------------------------------------------
# Engine status
# ----------------------------------------------------------------------


@admin_ops_bp.route("/admin/engine", methods=["GET"])
@require_admin
def engine_status():
    """Report whether the AI stack is usable.

    Reports configuration presence only. Credential values are never included,
    in any code path, including on error.
    """
    settings = get_settings()
    payload = {
        "engine": settings.primary_engine,
        "model": settings.groq_model if settings.groq_configured else None,
        "sttModel": settings.groq_stt_model if settings.groq_configured else None,
        "fastModel": settings.groq_fast_model if settings.groq_configured else None,
        "groqConfigured": settings.groq_configured,
        "geminiConfigured": settings.gemini_configured,
        "searchProvider": settings.search_provider,
        "searchConfigured": settings.search_configured,
        "memoryEnabled": settings.memory_enabled,
        "userCity": settings.user_city,
        "userTimezone": settings.user_timezone,
    }

    if settings.groq_configured:
        import asyncio

        from ..integrations.groq import GroqClient

        # Flask routes here are synchronous, matching the rest of the API
        # layer, so the async probe is driven explicitly rather than left as
        # an un-awaited coroutine.
        try:
            probe = asyncio.run(GroqClient(settings).health())
        except Exception as exc:  # noqa: BLE001 - never fail the status page
            logger.warning("engine probe failed: %s", exc)
            payload["groqReachable"] = False
            payload["groqIssue"] = "probe failed"
        else:
            payload["groqReachable"] = bool(probe.get("available"))
            if not probe.get("available"):
                payload["groqIssue"] = probe.get("reason")
            elif probe.get("reason"):
                payload["modelWarning"] = probe.get("reason")

    return jsonify(payload)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _snake(name: str) -> str:
    """isActive -> is_active"""
    out = []
    for char in name:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def db_or(*clauses):
    """OR across SQLAlchemy filters, imported lazily to keep the module light."""
    from sqlalchemy import or_

    return or_(*clauses)
