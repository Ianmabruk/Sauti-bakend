"""Memory API: create, inspect and delete long-term memories.

Nothing is stored automatically. A client must explicitly ask SAUTI to
remember something, and the service refuses content that looks sensitive.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from ..config.settings import Settings
from ..memory.service import MemoryRejected, MemoryService
from ..schemas.memory import MemoryCreateRequest, MemoryForgetRequest

logger = logging.getLogger(__name__)

memory_bp = Blueprint("memory", __name__)


def _service() -> MemoryService:
    return MemoryService(Settings())


@memory_bp.route("/memory", methods=["POST"])
def create_memory():
    """Store a memory.

    Body: {"content": "My preferred language is Kiswahili", "category": "preference",
           "key": "language", "user_id": null, "importance": 0.8}
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    try:
        parsed = MemoryCreateRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    service = _service()
    try:
        if parsed.key:
            item = service.remember_preference(
                parsed.key, parsed.content, user_id=parsed.user_id
            )
        else:
            item = service.remember(
                content=parsed.content,
                category=parsed.category,
                user_id=parsed.user_id,
                conversation_id=parsed.conversation_id,
                key=parsed.key,
                language=parsed.language,
                importance=parsed.importance,
            )
    except MemoryRejected as exc:
        return jsonify({"error": str(exc), "rejected": True}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    logger.info("MEMORY stored id=%s category=%s", item.id, item.category)
    return jsonify(item.to_dict()), 201


@memory_bp.route("/memory", methods=["GET"])
def list_memory():
    """List stored memories.

    Query: ?user_id=...&category=...&limit=...
    """
    service = _service()
    items = service.list_all(
        user_id=request.args.get("user_id"),
        category=request.args.get("category"),
    )
    limit = request.args.get("limit", type=int) or 100
    return jsonify({
        "items": [item.to_dict() for item in items[:limit]],
        "total": len(items),
    })


@memory_bp.route("/memory/recall", methods=["POST", "GET"])
def recall_memory():
    """Recall memories relevant to a query.

    Body or ?q=... plus optional user_id.
    """
    data = request.get_json(silent=True) or {}
    query = data.get("query") or request.args.get("q") or ""
    user_id = data.get("user_id") or request.args.get("user_id")
    if not query.strip():
        return jsonify({"error": "query is required"}), 400

    service = _service()
    items = service.recall(query, user_id=user_id)
    return jsonify({
        "query": query,
        "items": [item.to_dict() for item in items],
        "total": len(items),
    })


@memory_bp.route("/memory", methods=["DELETE"])
def forget_memory():
    """Delete one memory by id, or all matching a key."""
    data = request.get_json(silent=True) or {}
    if not data:
        data = {
            "memory_id": request.args.get("memory_id"),
            "key": request.args.get("key"),
            "user_id": request.args.get("user_id"),
        }

    try:
        parsed = MemoryForgetRequest(**{
            k: v for k, v in data.items() if k in MemoryForgetRequest.model_fields
        })
    except ValidationError as exc:
        first = exc.errors()[0]
        return jsonify({"error": first.get("msg")}), 400

    if not parsed.memory_id and not parsed.key:
        return jsonify({"error": "memory_id or key is required"}), 400

    service = _service()
    if parsed.memory_id:
        deleted = service.forget(parsed.memory_id)
        return jsonify({"deleted": 1 if deleted else 0, "memory_id": parsed.memory_id})

    count = service.forget_matching(parsed.key, user_id=parsed.user_id)
    return jsonify({"deleted": count, "key": parsed.key})
