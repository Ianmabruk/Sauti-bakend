"""Text to speech: Edge TTS streamed straight into a player process.

Latency is the whole game here. Rather than synthesising a whole answer and
then playing it, :class:`Speaker` streams MP3 chunks from Edge into the stdin
of a long-lived player process. The first audio byte reaches the speakers
while the model is still generating the rest of the sentence.

Degradation is deliberate and in this order:

1. Edge TTS into ``ffplay`` (or any MP3-capable player).
2. Edge TTS into a temporary file, played once synthesis completes — used when
   the streaming player is unavailable.
3. The text printed to stdout. The user is told, in their own terminal, what
   they would have heard. Nothing is silently dropped.

Audio for a given sentence is cached on disk by content hash, so a repeated
phrase costs nothing to speak.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..logsetup import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - depends on the host
    import edge_tts

    _EDGE_AVAILABLE = True
except ImportError:  # pragma: no cover
    edge_tts = None  # type: ignore[assignment]
    _EDGE_AVAILABLE = False


@dataclass
class SpeechResult:
    """Outcome of one spoken chunk.

    Attributes:
        ok: True when audio was actually played.
        text: What was attempted.
        mode: ``"stream"``, ``"file"`` or ``"console"``.
        duration_ms: Wall time for the whole call.
        first_byte_ms: Milliseconds from the start of the call until the first
            audio byte reached the player. This is the number that matters for
            perceived latency; ``duration_ms`` includes the entire sentence.
        reason: Explanation when ``ok`` is False.
    """

    ok: bool = True
    text: str = ""
    mode: str = "console"
    duration_ms: int = 0
    first_byte_ms: int = 0
    reason: str = ""


class Speaker:
    """Speaks text, streaming audio into a player process.

    Args:
        settings: Supplies the voice, rate, player command and cache path.
        player_command: Explicit player command, overriding configuration.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        player_command: Optional[list[str]] = None,
    ) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.player_command = player_command or self._resolve_player()
        self._player: Optional[asyncio.subprocess.Process] = None
        self._cache_dir = Path(
            tempfile.gettempdir(), "jarvis-tts-cache"
        )

    @property
    def available(self) -> bool:
        """True when speech can actually be produced."""
        return _EDGE_AVAILABLE and self.settings.tts_enabled

    @property
    def can_play(self) -> bool:
        """True when audio can be rendered to a device."""
        return bool(self.player_command)

    def _resolve_player(self) -> list[str]:
        """Pick a player that can consume MP3 on stdin."""
        if not self.settings.tts_enabled or not self.settings.tts_player:
            return []
        candidates = {
            "ffplay": ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
            "mpg123": ["mpg123", "-q", "-"],
            "mpv": ["mpv", "--no-video", "--really-quiet", "-"],
            "afplay": ["afplay", "-"],
        }
        configured = self.settings.tts_player
        if configured in candidates:
            command = candidates[configured]
        else:
            command = configured.split()
        if not command or shutil.which(command[0]) is None:
            if command:
                logger.warning(
                    "tts player %r not found; falling back to console output", command[0]
                )
            return []
        return command

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_key(self, text: str) -> str:
        """Content-addressed filename for synthesised audio."""
        signature = "|".join(
            [
                self.settings.tts_voice,
                self.settings.tts_rate,
                self.settings.tts_pitch,
                self.settings.tts_volume,
                text,
            ]
        )
        return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:32] + ".mp3"

    def _cached(self, text: str) -> Optional[Path]:
        """Return a cached audio file for this text, if one exists."""
        if not self.settings.tts_enabled:
            return None
        path = self._cache_dir / self._cache_key(text)
        return path if path.exists() and path.stat().st_size > 0 else None

    def _store(self, text: str, audio: bytes) -> Optional[Path]:
        """Write synthesised audio to the cache, ignoring failures."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_dir / self._cache_key(text)
            path.write_bytes(audio)
            return path
        except OSError as exc:
            logger.debug("tts cache write failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def _synthesise(self, text: str) -> Optional[bytes]:
        """Fetch MP3 bytes from Edge TTS.

        Args:
            text: The text to speak.

        Returns:
            The audio bytes, or None when synthesis failed.
        """
        if not _EDGE_AVAILABLE:
            return None

        communicate = edge_tts.Communicate(
            text,
            self.settings.tts_voice,
            rate=self.settings.tts_rate,
            pitch=self.settings.tts_pitch,
            volume=self.settings.tts_volume,
        )
        buffer = bytearray()
        try:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buffer.extend(chunk["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tts synthesis failed: %s", exc)
            return None
        return bytes(buffer) or None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def _ensure_player(self) -> Optional[asyncio.subprocess.Process]:
        """Start the player process if it is not already running."""
        if self._player is not None and self._player.returncode is None:
            return self._player
        if not self.player_command:
            return None
        try:
            self._player = await asyncio.create_subprocess_exec(
                *self.player_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("could not start tts player: %s", exc)
            self._player = None
            return None
        return self._player

    async def stop(self) -> None:
        """Stop playback immediately. Used for barge-in."""
        player = self._player
        self._player = None
        if player is None or player.returncode is not None:
            return
        try:
            player.kill()
            await asyncio.wait_for(player.wait(), timeout=1.0)
            logger.info("tts playback stopped")
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    def _to_console(self, text: str) -> None:
        """Print text to stdout as the last-resort speech channel."""
        print(text, flush=True)

    async def speak(
        self, text: str, *, allow_console: bool = True
    ) -> SpeechResult:
        """Speak a piece of text, streaming to the player as audio arrives.

        Args:
            text: The text to speak. Must be speakable, not markdown.
            allow_console: Permit the console fallback.

        Returns:
            A :class:`SpeechResult`. Never raises.
        """
        started = time.perf_counter()
        clean = (text or "").strip()
        if not clean:
            return SpeechResult(ok=True, mode="console", duration_ms=0)

        if not self.settings.tts_enabled:
            if allow_console:
                self._to_console(clean)
            return SpeechResult(
                ok=True, text=clean, mode="console",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        # Cache hit: no synthesis, no network.
        cached = self._cached(clean)
        if cached is not None and self.player_command:
            if await self._play_file(cached):
                return SpeechResult(
                    ok=True, text=clean, mode="file",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )

        if not _EDGE_AVAILABLE:
            if allow_console:
                logger.info("edge-tts unavailable; printing instead")
                self._to_console(clean)
            return SpeechResult(
                ok=allow_console, text=clean, mode="console",
                reason="edge-tts is not installed",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        player = await self._ensure_player()
        if player is None or player.stdin is None:
            return await self._speak_via_file(clean, started, allow_console)

        # Stream audio into the player as it is produced.
        first_byte: Optional[float] = None
        try:
            communicate = edge_tts.Communicate(
                clean,
                self.settings.tts_voice,
                rate=self.settings.tts_rate,
                pitch=self.settings.tts_pitch,
                volume=self.settings.tts_volume,
            )
            collected = bytearray()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    if first_byte is None:
                        first_byte = time.perf_counter()
                        logger.info(
                            "tts first audio byte in %dms",
                            int((first_byte - started) * 1000),
                        )
                    collected.extend(chunk["data"])
                    try:
                        player.stdin.write(chunk["data"])
                        await player.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        logger.debug("player closed early; text will be printed")
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tts stream failed (%s); falling back", exc)
            if collected:
                await self._speak_via_file(clean, started, allow_console, bytes(collected))
            else:
                return await self._speak_via_file(clean, started, allow_console)

        if collected:
            self._store(clean, bytes(collected))

        await self._close_player_stdin()
        return SpeechResult(
            ok=True,
            text=clean,
            mode="stream",
            duration_ms=int((time.perf_counter() - started) * 1000),
            first_byte_ms=int(((first_byte or time.perf_counter()) - started) * 1000),
        )

    async def _close_player_stdin(self) -> None:
        """Signal end of audio and reap the player without blocking."""
        player = self._player
        if player is None or player.stdin is None or player.stdin.is_closing():
            return
        try:
            player.stdin.close()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            return
        # Reap in the background; ffplay exits once its input is exhausted.
        asyncio.create_task(self._reap(player))

    async def _reap(self, player: asyncio.subprocess.Process) -> None:
        """Wait for the player to exit, tolerating races with stop()."""
        try:
            await asyncio.wait_for(player.wait(), timeout=30.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                player.kill()
            except ProcessLookupError:
                pass
        if self._player is player:
            self._player = None

    async def _play_file(self, path: Path) -> bool:
        """Play a complete audio file through the player."""
        player = await self._ensure_player()
        if player is None or player.stdin is None:
            return False
        try:
            player.stdin.write(path.read_bytes())
            await player.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
        await self._close_player_stdin()
        return True

    async def _speak_via_file(
        self,
        text: str,
        started: float,
        allow_console: bool,
        audio: Optional[bytes] = None,
    ) -> SpeechResult:
        """Synthesise fully, then play. Slower, but works without a pipe."""
        import time

        payload = audio or await self._synthesise(text)
        if payload:
            path = self._store(text, payload)
            if path and self.player_command:
                if await self._play_file(path):
                    return SpeechResult(
                        ok=True, text=text, mode="file",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
            if path and shutil.which(self.settings.tts_player or ""):
                self._spawn_detached_play(path)

        if allow_console:
            self._to_console(text)
            return SpeechResult(
                ok=True, text=text, mode="console",
                reason="player unavailable",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return SpeechResult(
            ok=False, text=text, mode="none", reason="no audio output available",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _spawn_detached_play(self, path: Path) -> None:
        """Fire-and-forget playback through the configured player binary."""
        command = self.player_command or [
            self.settings.tts_player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)
        ]
        try:
            subprocess.Popen(  # noqa: S603 - command comes from our own config
                [*command, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.debug("detached playback failed: %s", exc)

    async def close(self) -> None:
        """Stop playback and release resources."""
        await self.stop()

    async def warm_up(self) -> None:
        """Open and discard a tiny synthesis to pay the connection cost early.

        Edge TTS is reached over a websocket, and the very first request from a
        cold process costs several seconds for DNS, TLS and the server-side
        session. Measuring that on a warm process shows roughly 0.9 s to the
        first audio byte, so this is the difference between the first question
        feeling broken and feeling instant.

        Failures are logged and ignored: warming is an optimisation, and a
        failure here must not stop the agent from starting.
        """
        if not self.available:
            return
        import time

        started = time.perf_counter()
        try:
            communicate = edge_tts.Communicate(
                "Ready.", self.settings.tts_voice, rate=self.settings.tts_rate
            )
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    break  # first audio chunk is enough to establish the path
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tts warm-up failed: %s", exc)
            return
        logger.info("tts warm-up complete in %dms", int((time.perf_counter() - started) * 1000))
