"""Voice activity detection: stop the microphone the moment speech ends.

The point of this module is to remove the fixed "record for five seconds"
timer. It consumes a stream of 20 ms PCM frames and emits a complete utterance
as soon as the speaker has been quiet for ``vad_silence_ms``.

Two engines, chosen at import time:

- **webrtcvad** when installed. Google's algorithm, far more reliable in noise
  and against keyboard clicks than a pure energy gate.
- **energy fallback** otherwise, using RMS of the signed 16-bit samples. It
  needs nothing beyond the standard library, so VAD never simply disappears.

Both engines implement the same interface, so the rest of the agent does not
care which is active. The active engine is logged once at startup.
"""
from __future__ import annotations

import array
import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Awaitable, Callable, Optional

from ..config import Settings
from ..logsetup import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - depends on the host
    import webrtcvad

    _WEBRTC_AVAILABLE = True
except ImportError:  # pragma: no cover
    webrtcvad = None  # type: ignore[assignment]
    _WEBRTC_AVAILABLE = False

#: Bytes per 16-bit mono sample.
_SAMPLE_WIDTH = 2


class SpeechState(Enum):
    """Where the segmenter believes the speaker currently is."""

    SILENCE = "silence"
    SPEECH = "speech"


@dataclass
class Segment:
    """One detected utterance.

    Attributes:
        pcm: Raw 16 kHz mono signed 16-bit samples.
        started_at: Monotonic time speech was first detected.
        ended_at: Monotonic time the utterance was closed.
        speech_ms: Total time classified as speech.
        engine: Which VAD engine produced this segment.
    """

    pcm: bytes
    started_at: float
    ended_at: float
    speech_ms: int
    engine: str

    @property
    def seconds(self) -> float:
        """Length of the segment in seconds."""
        return len(self.pcm) / float(_SAMPLE_WIDTH * 16000)

    def to_wav(self, rate: int = 16000) -> bytes:
        """Wrap the PCM in a WAV container for the speech-to-text API."""
        import io
        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(_SAMPLE_WIDTH)
            handle.setframerate(rate)
            handle.writeframes(self.pcm)
        return buffer.getvalue()


class VoiceActivityDetector:
    """Detects speech boundaries in a stream of PCM frames.

    Args:
        settings: Supplies frame size, aggressiveness and thresholds.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self._vad = None
        self.engine = "energy"

        if _WEBRTC_AVAILABLE:
            try:
                self._vad = webrtcvad.Vad(self.settings.vad_aggressiveness)
                self.engine = "webrtcvad"
            except Exception as exc:  # noqa: BLE001 - fall back rather than fail
                logger.warning("webrtcvad init failed (%s), using energy VAD", exc)
                self._vad = None

        frame_bytes = int(
            self.settings.sample_rate * _SAMPLE_WIDTH * self.settings.vad_frame_ms / 1000
        )
        self._frame_bytes = frame_bytes
        self._silence_frames = max(1, self.settings.vad_silence_ms // self.settings.vad_frame_ms)
        self._speech_frames_needed = max(1, self.settings.vad_speech_ms // self.settings.vad_frame_ms)
        self._min_frames = max(1, self.settings.vad_min_utterance_ms // self.settings.vad_frame_ms)
        self._max_frames = max(self._min_frames, int(self.settings.vad_max_utterance_s * 1000 / self.settings.vad_frame_ms))

        logger.info(
            "vad ready engine=%s frame=%dms silence=%dms speech=%dms",
            self.engine, self.settings.vad_frame_ms, self.settings.vad_silence_ms,
            self.settings.vad_speech_ms,
        )

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------

    @property
    def frame_bytes(self) -> int:
        """Exact frame size in bytes this detector expects."""
        return self._frame_bytes

    @staticmethod
    def _rms(frame: bytes) -> int:
        """Root-mean-square amplitude of a signed 16-bit frame, 0-32767.

        ``array`` is used because it is the fastest stdlib way to reinterpret
        the buffer, and it runs in C.
        """
        usable = len(frame) - (len(frame) % _SAMPLE_WIDTH)
        if usable <= 0:
            return 0
        samples = array.array("h")
        samples.frombytes(frame[:usable])
        if not samples:
            return 0
        total = 0
        for value in samples:
            total += value * value
        return int((total / len(samples)) ** 0.5)

    def is_speech(self, frame: bytes) -> bool:
        """Classify one frame.

        Args:
            frame: Exactly ``frame_bytes`` of PCM.

        Returns:
            True when the frame most likely contains speech.
        """
        if len(frame) != self._frame_bytes:
            # Resample-tolerant path: score whatever arrived, padding short
            # frames so the engine always sees its expected size.
            if len(frame) > self._frame_bytes:
                frame = frame[: self._frame_bytes]
            else:
                frame = frame + b"\x00" * (self._frame_bytes - len(frame))

        if self._vad is not None:
            try:
                return bool(self._vad.is_speech(frame, self.settings.sample_rate))
            except Exception as exc:  # noqa: BLE001
                logger.debug("webrtcvad raised, switching to energy: %s", exc)
                self._vad = None
                self.engine = "energy"

        return self._rms(frame) > self.settings.vad_energy_threshold

    # ------------------------------------------------------------------
    # Segmentation
    # ------------------------------------------------------------------

    async def segments(
        self, frames: AsyncIterator[bytes]
    ) -> AsyncIterator[Segment]:
        """Group a frame stream into utterances.

        An utterance opens after ``vad_speech_ms`` of contiguous speech and
        closes after ``vad_silence_ms`` of quiet, or is force-closed at
        ``vad_max_utterance_s`` so a stuck-open microphone cannot run forever.

        Args:
            frames: A stream of PCM frames, typically from the microphone.

        Yields:
            One :class:`Segment` per detected utterance.
        """
        state = SpeechState.SILENCE
        buffer: list[bytes] = []
        speech_run = 0
        silence_run = 0
        speech_frames = 0
        started_at = 0.0
        #: Frames held as pre-roll. The onset of speech must survive the
        #: speech-detection threshold, or the first ~vad_speech_ms of every
        #: utterance is lost and transcription quality suffers.
        preroll_limit = self._speech_frames_needed
        #: Consecutive non-speech frames required to abandon a pre-roll.
        self._preroll_reset_frames = 3

        async for frame in frames:
            if not frame:
                continue

            speaking = self.is_speech(frame)

            if speaking:
                silence_run = 0
                speech_run += 1
                if state is SpeechState.SILENCE:
                    # Accumulate the onset, bounded so isolated noise clicks
                    # in a quiet room cannot grow the buffer without limit.
                    buffer.append(frame)
                    if len(buffer) > preroll_limit:
                        del buffer[: len(buffer) - preroll_limit]
                    if speech_run >= self._speech_frames_needed:
                        state = SpeechState.SPEECH
                        started_at = time.monotonic()
                        speech_frames = len(buffer)
                        logger.debug("speech started with %d pre-roll frames", speech_frames)
                else:
                    buffer.append(frame)
                    speech_frames += 1
            else:
                if state is SpeechState.SPEECH:
                    speech_run = 0
                    buffer.append(frame)
                    silence_run += 1
                    if silence_run >= self._silence_frames:
                        segment = self._close(
                            buffer, started_at, speech_frames, trailing=buffer[-silence_run:]
                        )
                        if segment is not None:
                            yield segment
                        state = SpeechState.SILENCE
                        buffer = []
                        speech_frames = 0
                        silence_run = 0
                else:
                    # Still in pre-roll. A single non-speech frame is not
                    # enough to abandon it: with the energy engine, room noise
                    # routinely dips a frame below the threshold, and resetting
                    # on one frame would reject most real speech. The run
                    # counter and the buffer must be reset together, otherwise
                    # the counter restarts while the buffered onset survives,
                    # and the threshold is reached with the onset discarded.
                    silence_run += 1
                    if silence_run >= self._preroll_reset_frames:
                        silence_run = 0
                        speech_run = 0
                        buffer = []

            if state is SpeechState.SPEECH and len(buffer) >= self._max_frames:
                segment = self._close(buffer, started_at, speech_frames, trailing=[])
                if segment is not None:
                    yield segment
                state = SpeechState.SILENCE
                buffer = []
                speech_frames = 0

        # Flush a trailing utterance when the stream ends mid-speech.
        if state is SpeechState.SPEECH and buffer:
            segment = self._close(buffer, started_at, speech_frames, trailing=[])
            if segment is not None:
                yield segment

    def _close(
        self,
        buffer: list[bytes],
        started_at: float,
        speech_frames: int,
        *,
        trailing: list[bytes],
    ) -> Optional[Segment]:
        """Assemble a finished segment, discarding it if it is too short.

        Args:
            buffer: Every frame captured for the utterance.
            started_at: Monotonic speech start.
            speech_frames: Count of frames classified as speech.
            trailing: The silent frames at the end, which are dropped so the
                payload contains speech only.

        Returns:
            A :class:`Segment`, or None when the utterance was noise.
        """
        if trailing:
            buffer = buffer[: len(buffer) - len(trailing)]
        if len(buffer) < self._min_frames:
            logger.debug(
                "discarding utterance frames=%d below minimum %d", len(buffer), self._min_frames
            )
            return None

        ended = time.monotonic()
        pcm = b"".join(buffer)
        logger.info(
            "utterance detected seconds=%.2f speech_ms=%d engine=%s",
            len(pcm) / (_SAMPLE_WIDTH * self.settings.sample_rate),
            speech_frames * self.settings.vad_frame_ms,
            self.engine,
        )
        return Segment(
            pcm=pcm,
            started_at=started_at,
            ended_at=ended,
            speech_ms=speech_frames * self.settings.vad_frame_ms,
            engine=self.engine,
        )


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale PCM amplitude, clipping rather than wrapping around.

    Args:
        pcm: Signed 16-bit mono samples.
        gain: Linear multiplier. 1.0 is a no-op.

    Returns:
        The scaled samples. Wrapping would turn loud audio into noise, so
        values beyond the signed 16-bit range are clamped.
    """
    if abs(gain - 1.0) < 1e-3 or not pcm:
        return pcm

    samples = array.array("h")
    usable = len(pcm) - (len(pcm) % _SAMPLE_WIDTH)
    samples.frombytes(pcm[:usable])
    limit = 32767

    for index, value in enumerate(samples):
        scaled = int(value * gain)
        samples[index] = (
            limit
            if scaled > limit
            else (-limit - 1 if scaled < -limit - 1 else scaled)
        )

    return samples.tobytes()
