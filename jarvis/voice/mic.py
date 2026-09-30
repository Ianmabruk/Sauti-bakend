"""Microphone capture with pluggable backends.

Capture is the most environment-dependent part of a voice agent, so the
backend is selected at runtime and the agent is never allowed to die over it.
Three backends are tried in order:

1. **sounddevice** — PortAudio, the cleanest option when the host has it.
2. **arecord** — the ALSA command-line recorder, streamed as a subprocess. It
   needs no Python native extension, which makes it the dependable path on
   machines without ``libportaudio``.
3. **none** — the caller is told the microphone is unavailable and the CLI
   switches to text input.

All backends yield identical 16 kHz mono signed 16-bit little-endian frames of
``vad_frame_ms`` milliseconds, so the VAD never needs to know which one is
running.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from ..config import Settings
from ..logsetup import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - depends on the host
    import sounddevice as _sounddevice

    _SOUNDDEVICE_AVAILABLE = True
except Exception:  # noqa: BLE001 - ImportError, or missing PortAudio at import
    _sounddevice = None  # type: ignore[assignment]
    _SOUNDDEVICE_AVAILABLE = False


class MicrophoneUnavailable(RuntimeError):
    """No usable capture backend could be started."""


@dataclass
class MicStatus:
    """A description of the capture path actually in use.

    Attributes:
        backend: ``"sounddevice"``, ``"arecord"`` or ``"none"``.
        device: The concrete device string, when there is one.
        sample_rate: Capture rate in Hz.
        frame_bytes: Frame size handed to the VAD.
    """

    backend: str
    device: str
    sample_rate: int
    frame_bytes: int

    @property
    def usable(self) -> bool:
        """True when audio can actually be captured."""
        return self.backend != "none"


def frame_size_bytes(settings: Settings) -> int:
    """Bytes in one VAD frame at the configured rate and duration."""
    return int(settings.sample_rate * 2 * settings.vad_frame_ms / 1000)


def detect_status(settings: Optional[Settings] = None) -> MicStatus:
    """Report which capture backend would be used, without opening the mic.

    Args:
        settings: Configuration override.

    Returns:
        A :class:`MicStatus`.
    """
    from ..config import get_settings

    cfg = settings or get_settings()
    size = frame_size_bytes(cfg)

    if _SOUNDDEVICE_AVAILABLE:
        return MicStatus("sounddevice", cfg.mic_device or "default", cfg.sample_rate, size)
    if shutil.which("arecord"):
        return MicStatus("arecord", cfg.mic_device or "default", cfg.sample_rate, size)
    return MicStatus("none", "", cfg.sample_rate, size)


class Microphone:
    """Asynchronous microphone capture producing fixed-size PCM frames.

    Args:
        settings: Supplies the rate, frame size, gain and device.
        backend: Force a specific backend. ``None`` auto-selects.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        backend: Optional[str] = None,
    ) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.frame_bytes = frame_size_bytes(self.settings)
        self._explicit_backend = backend
        self._stream: Optional[asyncio.StreamReader] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._buffer = bytearray()
        self._status = detect_status(self.settings)

    @property
    def status(self) -> MicStatus:
        """The capture path in use."""
        return self._status

    @property
    def backend(self) -> str:
        """Name of the active backend."""
        return self._status.backend

    # ------------------------------------------------------------------
    # Backend selection
    # ------------------------------------------------------------------

    def _choose_backend(self) -> str:
        """Pick the first viable backend, honouring an explicit override."""
        if self._explicit_backend:
            return self._explicit_backend
        if _SOUNDDEVICE_AVAILABLE:
            return "sounddevice"
        if shutil.which("arecord"):
            return "arecord"
        return "none"

    async def start(self) -> None:
        """Open the capture stream.

        Raises:
            MicrophoneUnavailable: When no backend works, with a reason the CLI
                can show the user verbatim.
        """
        backend = self._choose_backend()
        if backend == "none":
            raise MicrophoneUnavailable(
                "No microphone backend is available on this system."
            )
        try:
            if backend == "sounddevice":
                self._start_sounddevice()
            elif backend == "arecord":
                await self._start_arecord()
            else:
                raise MicrophoneUnavailable(f"Unknown capture backend '{backend}'.")
        except MicrophoneUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MicrophoneUnavailable(
                f"Could not open the microphone: {exc}"
            ) from exc

        self._status = MicStatus(
            backend, self._status.device, self.settings.sample_rate, self.frame_bytes
        )
        logger.info(
            "microphone open backend=%s rate=%d frame_bytes=%d",
            self._status.backend, self.settings.sample_rate, self.frame_bytes,
        )

    def _start_sounddevice(self) -> None:
        """Open PortAudio capture in a background thread."""
        if not _SOUNDDEVICE_AVAILABLE:
            raise MicrophoneUnavailable("sounddevice is not installed.")

        import numpy as np

        queue: asyncio.Queue[bytes] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            """PortAudio callback: copy PCM into the event loop's queue."""
            if status:
                logger.debug("portaudio status: %s", status)
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait, bytes(indata[:, 0].astype(np.int16).tobytes())
                )
            except Exception:  # noqa: BLE001 - never raise inside the callback
                pass

        self._sd_stream = _sounddevice.RawInputStream(
            samplerate=self.settings.sample_rate,
            blocksize=int(self.settings.sample_rate * self.settings.vad_frame_ms / 1000),
            dtype="int16",
            channels=1,
            callback=callback,
        )
        self._sd_stream.start()
        self._queue = queue

    async def _start_arecord(self) -> None:
        """Spawn ``arecord`` and read its stdout as raw PCM."""
        device = self.settings.mic_device or "default"
        command = [
            "arecord",
            "-q",
            "-t", "raw",              # no WAV header: emit bare PCM
            "-f", "S16_LE",           # signed 16-bit little endian
            "-r", str(self.settings.sample_rate),
            "-c", "1",                # mono
            "-D", device,
            "-",
        ]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise MicrophoneUnavailable("arecord is not installed.") from exc

        self._stream = self._process.stdout
        if self._stream is None:
            raise MicrophoneUnavailable("arecord produced no output stream.")
        logger.debug("arecord started device=%s", device)

    # ------------------------------------------------------------------
    # Frame production
    # ------------------------------------------------------------------

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield fixed-size PCM frames until the microphone is stopped.

        Yields:
            Exactly ``frame_bytes`` of PCM per iteration.
        """
        if self._status.backend == "sounddevice" and _SOUNDDEVICE_AVAILABLE:
            queue: asyncio.Queue[bytes] = self._queue
            while True:
                block = await queue.get()
                self._buffer.extend(block)
                while len(self._buffer) >= self.frame_bytes:
                    frame = bytes(self._buffer[: self.frame_bytes])
                    del self._buffer[: self.frame_bytes]
                    yield frame
        else:
            stream = self._stream
            if stream is None:
                raise MicrophoneUnavailable("Capture stream is not open.")
            while True:
                block = await stream.read(self.frame_bytes)
                if not block:
                    logger.info("microphone stream ended")
                    return
                self._buffer.extend(block)
                while len(self._buffer) >= self.frame_bytes:
                    frame = bytes(self._buffer[: self.frame_bytes])
                    del self._buffer[: self.frame_bytes]
                    yield frame

    async def stop(self) -> None:
        """Release the microphone. Safe to call more than once."""
        stream = getattr(self, "_sd_stream", None)
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("portaudio close failed: %s", exc)
            self._sd_stream = None

        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        self._process = None
        self._stream = None
        self._buffer.clear()
        logger.info("microphone released")

    async def __aenter__(self) -> "Microphone":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()
