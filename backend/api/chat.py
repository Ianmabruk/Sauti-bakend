"""Chat endpoint for SAUTI.

This is the agent's main entry point. It upgrades the original SautiPay chat
route (same URL, same legacy response fields) so existing clients keep working
while gaining tool use, sources and activity.

Flow: validate -> language -> memory -> plan -> tools -> grounded answer.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from flask import Blueprint, jsonify, request
from .limiter import limiter
from pydantic import ValidationError

from ..agent.orchestrator import SautiOrchestrator
from ..config.settings import Settings, get_settings
from ..db import db
from ..models import Conversation, Message
from ..schemas.chat import ChatRequest
from ..services.conversation import ConversationService
from ..services.intent import IntentEngine
from ..services.language import LanguageService
from ..tools.registry import get_registry

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__)

conversation_service = ConversationService()
intent_engine = IntentEngine()
language_service = LanguageService()

# Services are cheap to build but hold a tool registry, so build once per
# process and rebuild if settings change in tests.
_orchestrator: SautiOrchestrator | None = None
_orchestrator_key: tuple | None = None


def get_orchestrator() -> SautiOrchestrator:
    """Return a process-wide orchestrator bound to current settings."""
    global _orchestrator, _orchestrator_key
    settings = Settings()
    key = (settings.search_provider, settings.search_api_key != "", settings.llm_api_key != "")
    if _orchestrator is None or _orchestrator_key != key:
        _orchestrator = SautiOrchestrator(settings=settings, registry=get_registry(settings))
        _orchestrator_key = key
    return _orchestrator


def _load_history(
    conversation_id: str | None, limit: int = 8, char_budget: int = 3000
) -> list[dict]:
    """Recent turns as chat messages, oldest first.

    Bounded by *both* turn count and total characters. A turn-count limit alone
    is not enough: SAUTI answers can be long (a study card or business plan runs
    to thousands of characters), so eight of them can fill the provider's
    input-token allowance on their own and push every subsequent turn into a
    rate limit. Oldest turns are dropped first, and the newest is always kept
    even when it alone exceeds the budget, truncated so the turn still works.
    """
    if not conversation_id:
        return []
    try:
        messages = conversation_service.get_messages(conversation_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load history for %s: %s", conversation_id, exc)
        return []

    turns = [
        {"role": "assistant" if m.role == "assistant" else "user", "content": m.content}
        for m in messages
        if (m.content or "").strip()
    ]

    kept: list[dict] = []
    used = 0
    for turn in reversed(turns):
        length = len(turn["content"])
        if kept and used + length > char_budget:
            break
        kept.append(turn)
        used += length

    kept.reverse()
    if kept and len(kept[-1]["content"]) > char_budget:
        # A single oversized answer: keep the tail, which is the most recent
        # and most relevant part of it.
        kept[-1]["content"] = "…" + kept[-1]["content"][-char_budget:]
    if len(kept) < len(turns):
        logger.info(
            "history trimmed turns=%d->%d chars=%d", len(turns), len(kept), used
        )
    return kept


@chat_bp.route("/chat", methods=["POST"])
# The limiter's own key_func is get_remote_address; the limit value is
# read per call so configuration changes take effect without a restart.
@limiter.limit(lambda: get_settings().sauti_chat_rate_limit)
def chat():
    """Process a message through the SAUTI agent.

    Rate limited for the same reason as /api/sauti/chat: each call can reach a
    metered model provider, and the endpoint is unauthenticated.

    Request body:
        {
            "message": "...",
            "language": "auto",
            "conversation_id": "...",
            "user_id": "...",
            "use_tools": true
        }

    Returns the agent's answer plus used tools, sources and activity.
    """
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Accept the plain body, and also {"data": {...}} style wrappers.
    payload = data
    if isinstance(data.get("data"), dict):
        payload = data["data"]

    try:
        parsed = ChatRequest(
            **{k: v for k, v in payload.items() if k in ChatRequest.model_fields}
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    message = parsed.message
    logger.info("CHAT request_id=%s chars=%d", request_id, len(message))

    # Persist the user's message so history survives restarts.
    conversation = conversation_service.get_or_create(parsed.conversation_id)
    try:
        conversation_service.add_message(
            conversation_id=conversation.id,
            role="user",
            content=message,
        )
    except Exception as exc:  # noqa: BLE001 - storage must not break the reply
        logger.error("Could not persist user message: %s", exc)

    history = _load_history(conversation.id)
    # The just-stored user message is already the current turn.
    if history and history[-1].get("content") == message:
        history = history[:-1]

    orchestrator = get_orchestrator()

    try:
        turn = asyncio.run(
            orchestrator.handle(
                message=message,
                language_hint=parsed.language,
                conversation_history=history,
                user_id=parsed.user_id,
                conversation_id=conversation.id,
                use_tools=parsed.use_tools,
                mode=parsed.mode,
                user_location=parsed.user_location,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent turn failed")
        return jsonify({"error": "Internal server error", "request_id": request_id}), 500

    # Persist the assistant reply.
    try:
        conversation_service.add_message(
            conversation_id=conversation.id,
            role="assistant",
            content=turn.reply,
            language=turn.reply_language,
            intent=turn.intent,
            confidence=turn.confidence,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not persist assistant message: %s", exc)

    body = turn.to_dict()
    body["conversation_id"] = conversation.id
    body["request_id"] = request_id
    body["response"] = turn.reply
    # Mirrors /api/sauti/chat so both routes expose the same outcome fields.
    # Names the configured provider only; `engine_ok`/`degraded` say whether it
    # actually answered.
    body["engine"] = orchestrator.llm.provider_name()
    body["duration_ms"] = int((time.perf_counter() - started) * 1000)

    logger.info(
        "CHAT complete request_id=%s engine_ok=%s degraded=%s language=%s "
        "tools=%s sources=%d duration_ms=%d",
        request_id,
        body["engine_ok"],
        body["degraded"],
        turn.language,
        turn.used_tools,
        len(body.get("sources", [])),
        body["duration_ms"],
    )
    return jsonify(body)


@chat_bp.route("/chat/history/<conversation_id>", methods=["GET"])
def chat_history(conversation_id: str):
    """Return stored turns for a conversation."""
    conversation = conversation_service.get_conversation(conversation_id)
    if not conversation:
        return jsonify({"error": "Conversation not found"}), 404
    messages = conversation_service.get_messages(conversation_id, limit=100)
    return jsonify({
        "conversation_id": conversation.id,
        "language": conversation.language,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "language": m.language,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    })
