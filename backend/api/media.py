"""Media upload and management API for SautiPay."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from flask import Blueprint, jsonify, request, current_app, send_file
from werkzeug.utils import secure_filename
from ..db import db
from ..models import MediaAsset, User
from ..security import require_admin


media_bp = Blueprint("media", __name__, url_prefix="/api/media")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "pdf", "mp4", "webm", "mov"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_media_root() -> Path:
    root = current_app.config.get("MEDIA_ROOT", "/app/media")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_stored_filename(original: str, checksum: str) -> str:
    ext = original.rsplit(".", 1)[1].lower() if "." in original else ""
    return f"{checksum[:16]}.{ext}" if ext else checksum[:16]


@media_bp.post("/upload")
@require_admin

def upload_media():
    """Upload a media file."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type"}), 400
    # Read content and compute checksum
    content = file.read()
    if len(content) > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 50MB)"}), 413
    checksum = hashlib.sha256(content).hexdigest()
    # Check for duplicate
    existing = MediaAsset.query.filter_by(checksum=checksum).first()
    if existing:
        return jsonify({"id": existing.id, "message": "Duplicate file (already exists)"}), 200
    # Save to disk
    stored_name = generate_stored_filename(file.filename, checksum)
    media_root = get_media_root()
    storage_path = media_root / stored_name
    with open(storage_path, "wb") as f:
        f.write(content)
    # Get uploaded_by from auth context if available
    uploaded_by_id = None
    # In production, get from JWT/session
    # For now, look for user_id in form or header
    if request.form.get("user_id"):
        user = db.session.get(User, request.form.get("user_id"))
        if user:
            uploaded_by_id = user.id
    # Create media asset record
    media = MediaAsset(
        original_filename=secure_filename(file.filename),
        stored_filename=stored_name,
        mime_type=file.mimetype or "application/octet-stream",
        file_size=len(content),
        storage_path=str(storage_path.relative_to(media_root)),
        checksum=checksum,
        uploaded_by_id=uploaded_by_id,
        is_public=True,
    )
    db.session.add(media)
    db.session.commit()
    return jsonify({
        "id": media.id,
        "url": f"/api/media/{media.id}",
        "original_filename": media.original_filename,
        "mime_type": media.mime_type,
        "file_size": media.file_size,
        "checksum": media.checksum,
    }), 201

@media_bp.get("/<media_id>")

def serve_media(media_id: str):
    """Serve a media file by ID."""
    media = db.get_or_404(MediaAsset, media_id)
    if not media.is_public:
        # In production, check auth
        return jsonify({"error": "Forbidden"}), 403
    media_root = get_media_root()
    file_path = media_root / media.storage_path
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404
    return send_file(file_path, mimetype=media.mime_type)

@media_bp.get("/")
@require_admin

def list_media():
    """List media assets (admin)."""
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    public_only = request.args.get("public_only", "false").lower() == "true"
    query = MediaAsset.query
    if public_only:
        query = query.filter_by(is_public=True)
    query = query.order_by(MediaAsset.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "items": [
            {
                "id": m.id,
                "original_filename": m.original_filename,
                "mime_type": m.mime_type,
                "file_size": m.file_size,
                "checksum": m.checksum,
                "alt_text": m.alt_text,
                "is_public": m.is_public,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "url": f"/api/media/{m.id}",
            }
            for m in pagination.items
        ],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "per_page": pagination.per_page,
    })

@media_bp.patch("/<media_id>")
@require_admin

def update_media(media_id: str):
    """Update media metadata (alt text, focal point, visibility)."""
    media = db.get_or_404(MediaAsset, media_id)
    data = request.get_json(silent=True) or {}
    if "alt_text" in data:
        media.alt_text = data["alt_text"]
    if "focal_x" in data:
        media.focal_x = max(0, min(100, float(data["focal_x"])))
    if "focal_y" in data:
        media.focal_y = max(0, min(100, float(data["focal_y"])))
    if "is_public" in data:
        media.is_public = bool(data["is_public"])
    if "extra_metadata" in data:
        media.extra_metadata = data["extra_metadata"]
    media.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({
        "id": media.id,
        "alt_text": media.alt_text,
        "focal_x": media.focal_x,
        "focal_y": media.focal_y,
        "is_public": media.is_public,
    })

@media_bp.delete("/<media_id>")
@require_admin

def delete_media(media_id: str):
    """Delete a media asset (admin)."""
    media = db.get_or_404(MediaAsset, media_id)
    # Check if referenced
    from ..models import HeroSlide, Category, Story, FeaturedContent
    references = []
    if HeroSlide.query.filter((HeroSlide.image_id == media_id) | (HeroSlide.mobile_image_id == media_id)).first():
        references.append("hero_slides")
    if Category.query.filter_by(image_id=media_id).first():
        references.append("categories")
    if Story.query.filter_by(image_id=media_id).first():
        references.append("stories")
    if FeaturedContent.query.filter_by(image_id=media_id).first():
        references.append("featured_content")
    if references:
        return jsonify({
            "error": "Cannot delete: referenced by",
            "references": references,
        }), 409
    # Delete file from disk
    media_root = get_media_root()
    file_path = media_root / media.storage_path
    if file_path.exists():
        file_path.unlink(missing_ok=True)
    db.session.delete(media)
    db.session.commit()
    return jsonify({"message": "Deleted"}), 200