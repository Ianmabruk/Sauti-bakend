"""Speech-to-text via Groq Whisper.

Groq serves Whisper at the same OpenAI-compatible host as the chat model, so
one API key covers both. The endpoint accepts any container the browser or
ALSA produced, which is why the audio is passed through with its original MIME
type rather than being transcoded locally.

Reliability posture: transcription failing must never lose the user's turn. The
caller receives ``ok=False`` with a reason and decides whether to fall back to
text input.
"""
from __future__ import annotations

import asyncio
import time
import wave
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import Settings
from ..logsetup import get_logger
from ..net import HttpPool, get_pool
from ..retry import ProviderError, RetryPolicy, with_retry

logger = get_logger(__name__)

#: Groq's Whisper endpoint.
_TRANSCRIBE_PATH = "/audio/transcriptions"

#: Sample rates we normalise to before uploading. Whisper is happiest at 16k.
_TARGET_RATE = 16000


@dataclass
class Transcript:
    """The outcome of one transcription.

    Attributes:
        text: The recognised speech, or an empty string on failure.
        ok: False when nothing usable was recognised.
        language: Detected or hinted language code.
        duration_ms: Wall time spent transcribing.
        audio_seconds: Length of the submitted audio.
        reason: Explanation when ``ok`` is False.
    """

    text: str = ""
    ok: bool = True
    language: str = ""
    duration_ms: int = 0
    audio_seconds: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """A log-safe summary."""
        return {
            "ok": self.ok,
            "chars": len(self.text),
            "language": self.language,
            "duration_ms": self.duration_ms,
            "audio_seconds": round(self.audio_seconds, 2),
            "reason": self.reason,
        }


def wav_duration_seconds(audio: bytes) -> float:
    """Best-effort length of a WAV payload, in seconds.

    Returns 0.0 when the header cannot be parsed, which only affects logging.
    """
    try:
        with wave.open(__import__("io").BytesIO(audio), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or _TARGET_RATE
            return frames / float(rate)
    except Exception:  # noqa: BLE001 - diagnostics only
        return 0.0


class Transcriber:
    """Base class defining the transcription contract."""

    name: str = "base"

    @property
    def available(self) -> bool:
        """True when transcription could run right now."""
        return False

    async def transcribe(
        self, audio: bytes, *, mime_type: str = "audio/wav", language: Optional[str] = None
    ) -> Transcript:
        """Transcribe recorded audio.

        Args:
            audio: Raw encoded audio bytes.
            mime_type: Container of ``audio``, used as the part content type.
            language: Optional ISO language hint, e.g. ``"en"``.

        Returns:
            A :class:`Transcript`. Never raises.
        """
        raise NotImplementedError


class WhisperTranscriber(Transcriber):
    """Transcriber backed by Groq's Whisper deployment.

    Args:
        settings: Supplies the key, model and retry policy.
        pool: Optional shared HTTP pool.
    """

    name = "groq-whisper"

    def __init__(
        self, settings: Optional[Settings] = None, *, pool: Optional[HttpPool] = None
    ) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.pool = pool

    @property
    def available(self) -> bool:
        """True when a Groq key is configured."""
        return self.settings.groq_configured

    @property
    def policy(self) -> RetryPolicy:
        """A short retry policy: transcription is on the critical path."""
        return RetryPolicy(
            attempts=2,
            base=self.settings.llm_backoff_base,
            maximum=self.settings.llm_backoff_max,
        )

    def _client(self) -> HttpPool:
        return self.pool or get_pool(self.settings)

    async def _post_once(
        self, audio: bytes, mime_type: str, language: Optional[str]
    ) -> dict:
        """One upload attempt."""
        pool = self._client()
        data: dict[str, str] = {
            "model": self.settings.groq_stt_model,
            "response_format": "json",
            "temperature": "0",
        }
        if language:
            data["language"] = language

        # A filename extension helps the server infer the container.
        suffix = {
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/flac": "flac",
        }.get(mime_type.split(";")[0].strip(), "wav")

        response = await pool.client.post(
            f"{self.settings.groq_base_url}{_TRANSCRIBE_PATH}",
            files={"file": (f"clip.{suffix}", audio, mime_type)},
            data=data,
            headers={"Authorization": f"Bearer {self.settings.groq_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(self.settings.llm_timeout, connect=5.0),
        )
        response.raise_for_status()
        return response.json()

    async def transcribe(
        self, audio: bytes, *, mime_type: str = "audio/wav", language: Optional[str] = None
    ) -> Transcript:
        """Transcribe audio through Groq Whisper with retry and fallback.

        Args:
            audio: Raw encoded audio bytes.
            mime_type: Container of the audio.
            language: Optional ISO language hint.

        Returns:
            A :class:`Transcript`. Never raises.
        """
        started = time.perf_counter()
        seconds = wav_duration_seconds(audio) if "wav" in mime_type else 0.0

        if not audio:
            return Transcript(ok=False, reason="No audio was captured.")
        if not self.available:
            return Transcript(
                ok=False,
                reason="No speech-to-text key is configured.",
                duration_ms=0,
            )

        try:
            payload = await with_retry(
                lambda: self._post_once(audio, mime_type, language),
                policy=self.policy,
                label="groq.whisper",
                timeout=self.settings.llm_timeout,
            )
        except ProviderError as exc:
            logger.warning("transcription failed: %s", exc)
            return Transcript(
                ok=False,
                reason="I couldn't make out what you said.",
                duration_ms=int((time.perf_counter() - started) * 1000),
                audio_seconds=seconds,
            )

        text = str(payload.get("text", "")).strip()
        detected = str(payload.get("language", "") or language or "")
        duration = int((time.perf_counter() - started) * 1000)

        logger.info(
            "transcribed chars=%d audio_s=%.2f duration_ms=%d",
            len(text), seconds, duration,
        )
        if not text:
            return Transcript(
                ok=False,
                language=detected,
                reason="No speech was detected in that clip.",
                duration_ms=duration,
                audio_seconds=seconds,
            )
        return Transcript(
            text=text,
            language=detected,
            duration_ms=duration,
            audio_seconds=seconds,
        )
