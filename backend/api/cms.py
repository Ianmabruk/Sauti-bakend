"""Content management API for SautiPay frontend content.

Public read endpoints return published, active and in-schedule content.
Admin write endpoints require a valid admin token.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import (
    Category,
    FeaturedContent,
    HeroSlide,
    QuickAction,
    Story,
)
from ..security import require_admin

cms_bp = Blueprint("cms", __name__)

MODELS = {
    "hero": HeroSlide,
    "category": Category,
    "story": Story,
    "action": QuickAction,
    "featured": FeaturedContent,
}

VALID_SOURCE_TYPES = {"MANUAL", "LIVE", "API", "SYSTEM"}
VALID_ACTION_TYPES = {
    "OPEN_PAGE",
    "OPEN_CHAT",
    "OPEN_EXTERNAL_URL",
    "OPEN_COMMERCE",
    "START_MPESA_FLOW",
    "START_SEARCH",
}
VALID_TRANSITIONS = {"fade", "slide", "none"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean(value: Any, limit: int = 5000) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _media_payload(media) -> Optional[dict]:
    if not media:
        return None
    return {
        "id": media.id,
        "url": f"/api/media/{media.id}",
        "altText": media.alt_text,
        "focalX": media.focal_x,
        "focalY": media.focal_y,
    }


def _schedule_active(model, now: datetime) -> bool:
    starts_at = model.starts_at
    ends_at = model.ends_at
    if starts_at is not None:
        current = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc)
        if current > now:
            return False
    if ends_at is not None:
        current = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
        if current < now:
            return False
    return True


def _visible(model, now: datetime) -> bool:
    if not model.is_published or not model.is_active:
        return False
    return _schedule_active(model, now)


def _hero_payload(slide: HeroSlide) -> dict:
    return {
        "id": slide.id,
        "eyebrow": slide.eyebrow,
        "title": slide.title,
        "subtitle": slide.subtitle,
        "description": slide.description,
        "imageId": slide.image_id,
        "mobileImageId": slide.mobile_image_id,
        "image": _media_payload(slide.image),
        "mobileImage": _media_payload(slide.mobile_image),
        "focalPoint": {"x": slide.focal_x, "y": slide.focal_y},
        "ctaLabel": slide.cta_label,
        "ctaTarget": slide.cta_target,
        "displayDuration": slide.display_duration,
        "transitionType": slide.transition_type,
        "startsAt": slide.starts_at.isoformat() if slide.starts_at else None,
        "endsAt": slide.ends_at.isoformat() if slide.ends_at else None,
        "isPublished": slide.is_published,
        "isActive": slide.is_active,
        "sortOrder": slide.sort_order,
        "createdAt": slide.created_at.isoformat() if slide.created_at else None,
        "updatedAt": slide.updated_at.isoformat() if slide.updated_at else None,
    }


def _category_payload(category: Category) -> dict:
    return {
        "id": category.id,
        "title": category.title,
        "description": category.description,
        "icon": category.icon,
        "imageId": category.image_id,
        "image": _media_payload(category.image),
        "accent": category.accent,
        "target": category.target,
        "startsAt": category.starts_at.isoformat() if category.starts_at else None,
        "endsAt": category.ends_at.isoformat() if category.ends_at else None,
        "isPublished": category.is_published,
        "isActive": category.is_active,
        "sortOrder": category.sort_order,
        "createdAt": category.created_at.isoformat() if category.created_at else None,
        "updatedAt": category.updated_at.isoformat() if category.updated_at else None,
    }


def _story_payload(story: Story) -> dict:
    return {
        "id": story.id,
        "title": story.title,
        "description": story.description,
        "imageId": story.image_id,
        "image": _media_payload(story.image),
        "category": story.category,
        "source": story.source,
        "sourceUrl": story.source_url,
        "publishedAt": story.published_at.isoformat() if story.published_at else None,
        "sourceType": story.source_type,
        "isFeatured": story.is_featured,
        "isVisible": story.is_visible,
        "isPublished": story.is_published,
        "isActive": story.is_active,
        "sortOrder": story.sort_order,
        "startsAt": story.starts_at.isoformat() if story.starts_at else None,
        "endsAt": story.ends_at.isoformat() if story.ends_at else None,
        "createdAt": story.created_at.isoformat() if story.created_at else None,
        "updatedAt": story.updated_at.isoformat() if story.updated_at else None,
    }


def _action_payload(action: QuickAction) -> dict:
    return {
        "id": action.id,
        "title": action.title,
        "description": action.description,
        "icon": action.icon,
        "actionType": action.action_type,
        "actionTarget": action.action_target,
        "startsAt": action.starts_at.isoformat() if action.starts_at else None,
        "endsAt": action.ends_at.isoformat() if action.ends_at else None,
        "isPublished": action.is_published,
        "isActive": action.is_active,
        "sortOrder": action.sort_order,
        "createdAt": action.created_at.isoformat() if action.created_at else None,
        "updatedAt": action.updated_at.isoformat() if action.updated_at else None,
    }


def _featured_payload(item: FeaturedContent) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "imageId": item.image_id,
        "image": _media_payload(item.image),
        "ctaLabel": item.cta_label,
        "ctaTarget": item.cta_target,
        "startsAt": item.starts_at.isoformat() if item.starts_at else None,
        "endsAt": item.ends_at.isoformat() if item.ends_at else None,
        "isPublished": item.is_published,
        "isActive": item.is_active,
        "sortOrder": item.sort_order,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


PAYLOADS = {
    "hero": _hero_payload,
    "category": _category_payload,
    "story": _story_payload,
    "action": _action_payload,
    "featured": _featured_payload,
}


@cms_bp.route("/content", methods=["GET"])
def get_content():
    """Return all published frontend content."""
    now = _now()

    heroes = (
        HeroSlide.query.filter_by(is_published=True, is_active=True)
        .order_by(HeroSlide.sort_order)
        .all()
    )
    categories = (
        Category.query.filter_by(is_published=True, is_active=True)
        .order_by(Category.sort_order)
        .all()
    )
    stories = (
        Story.query.filter_by(is_published=True, is_active=True, is_visible=True)
        .order_by(Story.sort_order)
        .all()
    )
    actions = (
        QuickAction.query.filter_by(is_published=True, is_active=True)
        .order_by(QuickAction.sort_order)
        .all()
    )
    featured = (
        FeaturedContent.query.filter_by(is_published=True, is_active=True)
        .order_by(FeaturedContent.sort_order)
        .all()
    )

    return jsonify({
        "hero": [_hero_payload(s) for s in heroes if _schedule_active(s, now)],
        "categories": [_category_payload(c) for c in categories if _schedule_active(c, now)],
        "stories": [_story_payload(s) for s in stories if _schedule_active(s, now)],
        "quickActions": [_action_payload(a) for a in actions if _schedule_active(a, now)],
        "featured": [_featured_payload(f) for f in featured if _schedule_active(f, now)],
    })


@cms_bp.route("/admin/content/<content_type>", methods=["GET"])
@require_admin
def list_content(content_type: str):
    """List all content items including drafts."""
    model = MODELS.get(content_type)
    payload = PAYLOADS.get(content_type)
    if not model or not payload:
        return jsonify({"error": "Unknown content type"}), 404

    items = model.query.order_by(model.sort_order).all()
    return jsonify({"items": [payload(item) for item in items]})


@cms_bp.route("/admin/content/<content_type>", methods=["POST"])
@require_admin
def create_content(content_type: str):
    """Create a content item."""
    model = MODELS.get(content_type)
    if not model:
        return jsonify({"error": "Unknown content type"}), 404

    data = request.get_json(silent=True) or {}
    item, error = _apply_payload(model(), data, creating=True)
    if error:
        return jsonify({"error": error}), 400

    db.session.add(item)
    db.session.commit()
    return jsonify(PAYLOADS[content_type](item)), 201


@cms_bp.route("/admin/content/<content_type>/<item_id>", methods=["GET", "PATCH", "PUT", "DELETE"])
@require_admin
def manage_content(content_type: str, item_id: str):
    """Read, update or delete a single content item."""
    model = MODELS.get(content_type)
    payload = PAYLOADS.get(content_type)
    if not model or not payload:
        return jsonify({"error": "Unknown content type"}), 404

    item = db.session.get(model, item_id)
    if not item:
        return jsonify({"error": "Not found"}), 404

    if request.method == "GET":
        return jsonify(payload(item))

    if request.method == "DELETE":
        db.session.delete(item)
        db.session.commit()
        return jsonify({"message": "Deleted"})

    data = request.get_json(silent=True) or {}
    item, error = _apply_payload(item, data, creating=False)
    if error:
        return jsonify({"error": error}), 400

    db.session.commit()
    return jsonify(payload(item))


@cms_bp.route("/admin/content/<content_type>/reorder", methods=["POST"])
@require_admin
def reorder_content(content_type: str):
    """Persist a new order for a content collection."""
    model = MODELS.get(content_type)
    if not model:
        return jsonify({"error": "Unknown content type"}), 404

    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list):
        return jsonify({"error": "ids must be a list"}), 400

    for index, item_id in enumerate(ids):
        item = db.session.get(model, item_id)
        if item:
            item.sort_order = index

    db.session.commit()
    items = model.query.order_by(model.sort_order).all()
    return jsonify({"items": [PAYLOADS[content_type](item) for item in items]})


def _apply_payload(item, data: dict, creating: bool):
    """Apply a request payload onto a model instance."""
    if item.__class__ is HeroSlide:
        if "eyebrow" in data:
            item.eyebrow = _clean(data["eyebrow"], 120)
        if "title" in data:
            title = _clean(data["title"], 255)
            if not title:
                return item, "Title is required"
            item.title = title
        if "subtitle" in data:
            item.subtitle = _clean(data["subtitle"], 255)
        if "description" in data:
            item.description = _clean(data["description"], 5000)
        if "imageId" in data:
            item.image_id = data["imageId"] or None
        if "mobileImageId" in data:
            item.mobile_image_id = data["mobileImageId"] or None
        if "focalPoint" in data and isinstance(data["focalPoint"], dict):
            point = data["focalPoint"]
            item.focal_x = max(0.0, min(100.0, float(point.get("x", 50))))
            item.focal_y = max(0.0, min(100.0, float(point.get("y", 50))))
        if "ctaLabel" in data:
            item.cta_label = _clean(data["ctaLabel"], 120)
        if "ctaTarget" in data:
            item.cta_target = _clean(data["ctaTarget"], 500)
        if "displayDuration" in data:
            item.display_duration = max(1000, _parse_int(data["displayDuration"], 6000))
        if "transitionType" in data:
            value = str(data["transitionType"] or "fade")
            if value not in VALID_TRANSITIONS:
                return item, "Invalid transition type"
            item.transition_type = value

    elif item.__class__ is Category:
        if "title" in data:
            title = _clean(data["title"], 120)
            if not title:
                return item, "Title is required"
            item.title = title
        if "description" in data:
            item.description = _clean(data["description"], 5000)
        if "icon" in data:
            item.icon = _clean(data["icon"], 120)
        if "imageId" in data:
            item.image_id = data["imageId"] or None
        if "accent" in data:
            item.accent = _clean(data["accent"], 24) or "#e86f3a"
        if "target" in data:
            item.target = _clean(data["target"], 500)

    elif item.__class__ is Story:
        if "title" in data:
            title = _clean(data["title"], 300)
            if not title:
                return item, "Title is required"
            item.title = title
        if "description" in data:
            item.description = _clean(data["description"], 5000)
        if "imageId" in data:
            item.image_id = data["imageId"] or None
        if "category" in data:
            item.category = _clean(data["category"], 120)
        if "source" in data:
            item.source = _clean(data["source"], 255)
        if "sourceUrl" in data:
            item.source_url = _clean(data["sourceUrl"], 2000)
        if "publishedAt" in data:
            item.published_at = _parse_datetime(data["publishedAt"])
        if "sourceType" in data:
            value = str(data["sourceType"] or "MANUAL").upper()
            if value not in VALID_SOURCE_TYPES:
                return item, "Invalid source type"
            item.source_type = value
        if "isFeatured" in data:
            item.is_featured = _parse_bool(data["isFeatured"])
        if "isVisible" in data:
            item.is_visible = _parse_bool(data["isVisible"])

    elif item.__class__ is QuickAction:
        if "title" in data:
            title = _clean(data["title"], 160)
            if not title:
                return item, "Title is required"
            item.title = title
        if "description" in data:
            item.description = _clean(data["description"], 5000)
        if "icon" in data:
            item.icon = _clean(data["icon"], 120)
        if "actionType" in data:
            value = str(data["actionType"] or "OPEN_PAGE").upper()
            if value not in VALID_ACTION_TYPES:
                return item, "Invalid action type"
            item.action_type = value
        if "actionTarget" in data:
            item.action_target = _clean(data["actionTarget"], 500)

    elif item.__class__ is FeaturedContent:
        if "title" in data:
            title = _clean(data["title"], 255)
            if not title:
                return item, "Title is required"
            item.title = title
        if "description" in data:
            item.description = _clean(data["description"], 5000)
        if "imageId" in data:
            item.image_id = data["imageId"] or None
        if "ctaLabel" in data:
            item.cta_label = _clean(data["ctaLabel"], 120)
        if "ctaTarget" in data:
            item.cta_target = _clean(data["ctaTarget"], 500)

    else:
        return item, "Unknown content type"

    if "startsAt" in data:
        item.starts_at = _parse_datetime(data["startsAt"])
    if "endsAt" in data:
        item.ends_at = _parse_datetime(data["endsAt"])
    if "isPublished" in data:
        item.is_published = _parse_bool(data["isPublished"])
    if "isActive" in data:
        item.is_active = _parse_bool(data["isActive"], True)
    if "sortOrder" in data:
        item.sort_order = _parse_int(data["sortOrder"], 0)

    if creating and getattr(item, "sort_order", 0) == 0 and "sortOrder" not in data:
        item.sort_order = model_max_order(item)

    return item, None


def model_max_order(item) -> int:
    model = item.__class__
    highest = db.session.query(db.func.max(model.sort_order)).scalar()
    return (highest or 0) + 1
