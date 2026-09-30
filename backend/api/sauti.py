"""SAUTI chat endpoint.

    POST /api/sauti/chat
    POST /api/sauti/transcribe
    GET  /api/sauti/health

This is the canonical Sauti AI endpoint. The browser calls only this backend;
the Groq key never leaves the server.

The existing `POST /api/chat` route shares the same orchestrator and stays
available for backwards compatibility.
"""
from __future__ import annotations

import array
import asyncio
import io
import logging
import time
import uuid
import wave

from flask import Blueprint, jsonify, request
from .limiter import limiter
from pydantic import ValidationError

from ..agent.orchestrator import SautiOrchestrator
from ..config.settings import Settings, get_settings
from ..integrations.gemini import GeminiClient, GeminiError
from ..integrations.groq import GroqClient, GroqError
from ..schemas.sauti import SautiChatRequest
from ..services.conversation import ConversationService
from .chat import _load_history

logger = logging.getLogger(__name__)

#: RMS below which a WAV clip is treated as silence. A real speaking voice sits
#: far above this; a quiet room or a muted microphone sits well below.
_SILENCE_RMS = 200

sauti_bp = Blueprint("sauti", __name__)
conversation_service = ConversationService()

_orchestrator: SautiOrchestrator | None = None
_orchestrator_key: tuple | None = None


def _fingerprint(settings: Settings) -> tuple:
    """Detect settings changes without leaking any credential value."""
    return (
        settings.groq_model,
        bool(settings.groq_api_key),
        settings.gemini_model,
        bool(settings.gemini_api_key),
        settings.search_provider,
        bool(settings.search_api_key),
        settings.memory_enabled,
    )


def get_orchestrator() -> SautiOrchestrator:
    """Process-wide orchestrator, rebuilt when settings change."""
    global _orchestrator, _orchestrator_key
    settings = get_settings()
    key = _fingerprint(settings)
    if _orchestrator is None or _orchestrator_key != key:
        from ..tools.registry import get_registry

        _orchestrator = SautiOrchestrator(
            settings=settings, registry=get_registry(settings)
        )
        _orchestrator_key = key
    return _orchestrator


@sauti_bp.route("/sauti/chat", methods=["POST"])
# The limiter's own key_func is get_remote_address; the limit value is
# read per call so configuration changes take effect without a restart.
@limiter.limit(lambda: get_settings().sauti_chat_rate_limit)
def sauti_chat():
    """Handle a Sauti message through the configured AI engine.

    Rate limited because every call can spend real money and burn a
    rate-limited provider budget. The limit is configurable via
    ``SAUTI_CHAT_RATE_LIMIT``.
    """
    started = time.perf_counter()
    request_id = uuid.uuid4().hex

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    try:
        parsed = SautiChatRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    logger.info("SAUTI CHAT RECEIVED id=%s chars=%d", request_id, len(parsed.message))

    # Conversation storage is best-effort. A managed database can be
    # suspended, unreachable or out of connections, and none of that should
    # cost the caller their answer: the turn still works, it just cannot
    # remember previous messages. Only the orchestrator call below is
    # genuinely fatal, and that reports 502.
    try:
        conversation = conversation_service.get_or_create(parsed.conversation_id)
    except Exception as exc:  # noqa: BLE001 - storage must not break the reply
        logger.error("Conversation store unavailable, continuing stateless: %s", exc)
        conversation = None

    conversation_id_for_turn = conversation.id if conversation is not None else None

    if conversation is not None:
        try:
            conversation_service.add_message(
                conversation_id=conversation.id, role="user", content=parsed.message
            )
        except Exception as exc:  # noqa: BLE001 - storage must not break the reply
            logger.error("Could not persist user message: %s", exc)

    # Build history from the stored conversation so follow-ups actually
    # resolve. This route previously passed None, which made every turn
    # stateless even though the messages were being saved. Client-supplied
    # history is only used when there is no stored conversation to read.
    history: list = []
    if conversation is not None:
        try:
            history = _load_history(conversation.id)
        except Exception as exc:  # noqa: BLE001 - storage must not break the reply
            logger.error("Could not read conversation history: %s", exc)

    orchestrator = get_orchestrator()

    try:
        turn = asyncio.run(
            orchestrator.handle(
                message=parsed.message,
                language_hint=parsed.language,
                conversation_history=history or None,
                user_id=parsed.user_id,
                conversation_id=conversation_id_for_turn,
                use_tools=parsed.use_tools,
                mode=parsed.mode,
                user_location=parsed.user_location,
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("Sauti turn failed")
        # Never leak internals: no traceback, no tool names, no key.
        return (
            jsonify(
                {
                    "error": "Sauti could not complete that request right now.",
                    "request_id": request_id,
                }
            ),
            502,
        )

    try:
        if conversation is None:
            raise RuntimeError("conversation store unavailable")
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
    body["conversation_id"] = conversation_id_for_turn
    body["request_id"] = request_id
    # Which provider was configured for this turn. It does not imply the
    # provider answered: when the call fails, `engine_ok` is False and
    # `degraded` is True while this still reports the intended engine.
    body["engine"] = orchestrator.llm.provider_name()
    body["duration_ms"] = int((time.perf_counter() - started) * 1000)

    logger.info(
        "SAUTI CHAT COMPLETE id=%s engine=%s engine_ok=%s degraded=%s "
        "language=%s tools=%s duration_ms=%d",
        request_id,
        body["engine"],
        body["engine_ok"],
        body["degraded"],
        turn.reply_language,
        turn.used_tools,
        body["duration_ms"],
    )
    return jsonify(body)


#: Audio types browsers actually produce via MediaRecorder.
_ALLOWED_AUDIO = {
    "audio/webm": ".webm",
    "audio/webm;codecs=opus": ".webm",
    "audio/ogg": ".ogg",
    "audio/ogg;codecs=opus": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
}


#: Cached provider probe. Keyed by the configured model so a model change
#: invalidates it immediately rather than reporting a stale verdict.
_PROBE_CACHE: dict[str, tuple[float, dict]] = {}
_PROBE_TTL_SECONDS = 30.0


def _cached_groq_health(settings) -> dict:
    """Return the provider probe, reusing a recent result.

    The probe is a network call. A status endpoint that the UI polls should not
    pay for one on every render, but a permanently cached answer would hide an
    outage, so the result is held only briefly.
    """
    key = settings.groq_model
    now = time.monotonic()
    cached = _PROBE_CACHE.get(key)
    if cached and (now - cached[0]) < _PROBE_TTL_SECONDS:
        return cached[1]

    try:
        result = asyncio.run(GroqClient(settings).health())
    except Exception as exc:  # noqa: BLE001 - a status page must never 500
        logger.warning("health probe failed: %s", exc)
        result = {"available": False, "reason": "probe failed"}

    _PROBE_CACHE[key] = (now, result)
    return result


def _is_silent_wav(audio: bytes) -> bool:
    """Detect effectively-silent WAV audio before spending an API call.

    Whisper is trained on speech and will happily hallucinate a short phrase
    ("Thank you.") out of digital silence. On a rate-limited free tier that is
    both a wasted request and a confusing reply, so near-silent clips are
    rejected locally.

    Args:
        audio: Raw WAV bytes.

    Returns:
        True when the clip is quiet enough that no speech is present.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                return False  # only handle plain 16-bit mono
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, ValueError):
        return False  # not a WAV we can measure; let the provider decide

    if not frames:
        return True

    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    if not samples:
        return True
    total = sum(value * value for value in samples)
    rms = int((total / len(samples)) ** 0.5)
    return rms < _SILENCE_RMS


@sauti_bp.route("/sauti/transcribe", methods=["POST"])
def transcribe():
    """Transcribe recorded audio with Groq Whisper.

    The browser sends a short clip recorded by MediaRecorder. Transcription
    happens server-side, which is what makes voice work in browsers without
    `SpeechRecognition` (Firefox) or where Google's speech endpoint is
    unreachable.

    Groq is the primary transcription engine. Gemini is retained as a fallback
    so a deployment that only has a Gemini key keeps working.

    Body: multipart/form-data with `audio` and an optional `language`.
    """
    settings = get_settings()

    # Validate the request before checking configuration, so a malformed
    # request always reports as malformed regardless of server state.
    upload = request.files.get("audio")
    if upload is None or not upload.filename:
        return jsonify({"error": "No audio file was uploaded."}), 400

    mime = (upload.mimetype or "").lower().split(";")[0].strip()
    if mime not in _ALLOWED_AUDIO:
        return (
            jsonify(
                {
                    "error": f"Unsupported audio type '{mime or 'unknown'}'.",
                    "supported": sorted(_ALLOWED_AUDIO),
                }
            ),
            415,
        )

    audio = upload.read()
    limit = max(settings.groq_max_audio_bytes, settings.gemini_max_audio_bytes)
    if len(audio) > limit:
        return (
            jsonify({"error": f"Recording is too large. Keep it under {limit // 1_048_576} MB."}),
            413,
        )
    if not audio:
        return jsonify({"error": "The recording was empty."}), 400

    # Reject silence locally. Whisper hallucinates a short phrase out of a
    # silent clip, which would both confuse the user and waste a request on a
    # rate-limited tier.
    if mime in {"audio/wav", "audio/x-wav"} and _is_silent_wav(audio):
        logger.info("TRANSCRIPTION skipped: silent clip bytes=%d", len(audio))
        return (
            jsonify({"error": "I didn't catch any speech.", "text": "", "engine": "local"}),
            200,
        )

    if not (settings.groq_configured or settings.gemini_configured):
        return (
            jsonify(
                {
                    "error": "Voice transcription is not configured on this server.",
                    "configured": False,
                }
            ),
            503,
        )

    language = (request.form.get("language") or "").strip().lower() or None

    started = time.perf_counter()
    text = ""
    engine = ""
    errors: list[str] = []

    # Primary: Groq Whisper.
    if settings.groq_configured:
        try:
            text = asyncio.run(
                GroqClient(settings).transcribe(
                    audio_bytes=audio, mime_type=mime, language=language
                )
            )
            engine = "groq"
        except GroqError as exc:
            logger.warning("Groq transcription failed: %s status=%s", exc.message, exc.status)
            errors.append(exc.user_message)

    # Fallback: Gemini, for deployments without a Groq key or on Groq failure.
    if not text.strip() and settings.gemini_configured:
        try:
            text = asyncio.run(
                GeminiClient(settings).transcribe(
                    audio_bytes=audio, mime_type=mime, language=language
                )
            )
            engine = "gemini"
        except GeminiError as exc:
            logger.warning("Gemini transcription failed: %s", exc.status)
            errors.append(exc.user_message)

    if not engine:
        # Every engine failed. Only a user-safe message leaves the server.
        return (
            jsonify({"error": errors[-1] if errors else "Voice input failed.", "configured": True}),
            502,
        )

    text = (text or "").strip()
    logger.info(
        "TRANSCRIPTION ok engine=%s bytes=%d language=%s duration_ms=%d",
        engine,
        len(audio),
        language,
        int((time.perf_counter() - started) * 1000),
    )
    if not text:
        return jsonify({"error": "I couldn't make out any speech.", "text": ""}), 200
    return jsonify({"text": text, "language": language, "engine": engine})


@sauti_bp.route("/sauti/health", methods=["GET"])
def sauti_health():
    """Report engine readiness. Never returns any credential value.

    The provider probe is cached briefly. Without it this endpoint costs a
    network round trip to Groq on every call, which made a status endpoint the
    slowest route in the app while the chat UI polls it.
    """
    settings = get_settings()
    engine = settings.primary_engine
    payload = {
        "engine": engine,
        "groqConfigured": settings.groq_configured,
        "model": settings.groq_model if settings.groq_configured else None,
        "sttModel": settings.groq_stt_model if settings.groq_configured else None,
        "geminiConfigured": settings.gemini_configured,
        "searchConfigured": settings.search_configured,
        "memoryEnabled": settings.memory_enabled,
    }

    if settings.groq_configured:
        probe = _cached_groq_health(settings)
        payload["groqReachable"] = probe.get("available", False)
        if not probe.get("available"):
            payload["groqIssue"] = probe.get("reason")
        elif probe.get("reason"):
            # Reachable, but flag a configured model this account cannot use.
            payload["modelWarning"] = probe.get("reason")

    return jsonify(payload)
