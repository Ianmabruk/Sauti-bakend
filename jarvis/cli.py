"""Interactive loop for JARVIS.

Design decisions that shape this file:

**The microphone stays open.** Rather than starting and stopping capture per
turn, the mic runs continuously and the VAD carves utterances out of the
stream. Nothing is lost while the agent thinks or speaks, so the very first
syllable of the next question is already being captured. This is the single
biggest perceived-latency win in a voice agent.

**Half duplex, not full duplex.** While the assistant is speaking, incoming
audio is discarded. Full duplex would require echo cancellation, and hearing
your own voice loop is worse than waiting.

**Nothing kills the loop.** Every stage is wrapped, mic failures demote to text
input, and :func:`main` has an outer recovery loop that restarts the audio
stack if it ever dies.

Commands: ``/quit`` ``/text`` ``/voice`` ``/reset`` ``/stats`` ``/help``
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
from typing import Optional

from .agent import JarvisAgent, TurnResult
from .config import Settings, get_settings
from .logsetup import configure_logging, get_logger
from .net import close_pool
from .providers.stt import WhisperTranscriber
from .voice.mic import Microphone, MicrophoneUnavailable, detect_status
from .voice.tts import Speaker
from .voice.vad import VoiceActivityDetector, apply_gain

logger = get_logger(__name__)

_BANNER = r"""
    _  _____ ____    ______
   | |/ / __|___ \  / / __/___  ____
   | ' /| _ \  __ \/ / / / _ \/ __/
   | . \| |_/ /_/ / / /_/  __/ /_
   |_|\_\__/_.___/_/\__/\___/\__/   JARVIS 1.0

   Speak, or type. /help for commands, /quit to leave.
"""

class Session:
    """Owns the agent, the audio stack and the interaction loop.

    Args:
        settings: Runtime configuration.
        text_only: Start in text mode and never open the microphone.
    """

    def __init__(self, settings: Optional[Settings] = None, *, text_only: bool = False):
        self.settings = settings or get_settings()
        self.agent = JarvisAgent(self.settings)
        self.transcriber = WhisperTranscriber(self.settings)
        self._text_only = text_only
        self._running = False
        self._speaking = False
        self._turns = 0
        self._errors = 0
        self._last_turn_ms = 0

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def say(self, text: str) -> None:
        """Write a status line to stdout, bypassing the log stream."""
        print(f"\033[38;5;110m{text}\033[0m", flush=True)

    def warn(self, text: str) -> None:
        """Write a warning to stdout."""
        print(f"\033[38;5;214m{text}\033[0m", flush=True)

    # ------------------------------------------------------------------
    # Text mode
    # ------------------------------------------------------------------

    async def text_loop(self) -> None:
        """Read lines from stdin and answer them until the session ends."""
        self._running = True
        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)

        self.say("Text mode. Type a question, or /quit to leave.")
        while self._running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            message = line.strip()
            if not message:
                continue
            if await self.handle_command(message):
                continue
            await self.handle_turn(message)

    async def _prompt(self, message: str) -> None:
        """Speak a short prompt and wait for it to finish.

        Used to tell the user the microphone went away. It must not recurse
        into the main loop, so it bypasses command handling entirely.
        """
        speaker = self.agent.speaker
        if speaker.can_play:
            with contextlib.suppress(Exception):
                await speaker.speak(message)
        self.say(message)

    # ------------------------------------------------------------------
    # Voice mode
    # ------------------------------------------------------------------

    async def voice_loop(self) -> None:
        """Listen continuously, answering each detected utterance.

        Runs until the user leaves, the microphone disappears, or an
        unrecoverable error occurs. Never raises.
        """
        status = detect_status(self.settings)
        if not status.usable:
            self.warn("No microphone backend found. Switching to text input.")
            await self.text_loop()
            return

        loop = asyncio.get_running_loop()
        self._running = True
        self._install_signal_handlers(loop)

        attempts = 0
        while self._running:
            attempts += 1
            if attempts > 3:
                self.warn("Microphone kept failing. Switching to text input.")
                await self.text_loop()
                return
            try:
                await self._voice_session()
            except MicrophoneUnavailable as exc:
                logger.warning("microphone unavailable: %s", exc)
                self.warn("The microphone stopped responding. Switching to text input.")
                await self.text_loop()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._errors += 1
                logger.exception("voice session failed")
                await self._prompt("Something went wrong, sir. Recovering.")
                await asyncio.sleep(1.0)
                if self._errors > 10:
                    self.warn("Too many failures. Switching to text input.")
                    await self.text_loop()
                    return

    async def _voice_session(self) -> None:
        """One continuous listen-and-answer pass."""
        mic = Microphone(self.settings)
        detector = VoiceActivityDetector(self.settings)

        await mic.start()
        self.say(f"Listening. VAD engine: {detector.engine}. /text to type.")

        try:
            async for segment in detector.segments(mic.frames()):
                if not self._running:
                    break
                # Half duplex: ignore anything captured while we speak.
                if self._speaking:
                    continue

                pcm = apply_gain(segment.pcm, self.settings.mic_gain)
                if not self.transcriber.available:
                    self.warn("No speech-to-text key is set. Switching to text input.")
                    await mic.stop()
                    await self.text_loop()
                    return

                transcript = await self.transcribe(pcm)
                if not transcript:
                    continue
                if await self.handle_command(transcript):
                    continue
                await self.handle_turn(transcript, audio=segment.pcm)
        finally:
            await mic.stop()

    async def transcribe(self, pcm: bytes) -> str:
        """Transcribe one utterance, reporting failures in the user's terms."""
        started = time.perf_counter()
        wav = _pcm_to_wav(pcm, self.settings.sample_rate)
        result = await self.transcriber.transcribe(
            wav, mime_type="audio/wav", language=None
        )
        logger.info(
            "stt ok=%s audio_s=%.2f duration_ms=%d",
            result.ok, segment_seconds(pcm), int((time.perf_counter() - started) * 1000),
        )
        if not result.ok:
            if result.reason:
                self.warn(result.reason)
            return ""
        return result.text.strip()

    # ------------------------------------------------------------------
    # Turn handling
    # ------------------------------------------------------------------

    async def handle_turn(self, message: str, *, audio: Optional[bytes] = None) -> TurnResult:
        """Answer one message, speaking unless told not to."""
        self._turns += 1
        self._speaking = True
        started = time.perf_counter()
        try:
            result = await self.agent.ask(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a turn must never end the session
            self._errors += 1
            logger.exception("turn failed")
            self.warn("I hit a problem handling that, sir. Please try again.")
            return TurnResult(transcript=message, error=str(exc))
        finally:
            self._speaking = False

        self._last_turn_ms = int((time.perf_counter() - started) * 1000)

        # When speech was disabled the answer exists only in memory; show it.
        if not result.spoken and result.reply:
            print(result.reply, flush=True)

        if result.searched and result.sources:
            for source in result.sources[:2]:
                self.say(f"  · {source.get('title', 'source')} — {source.get('url', '')}")
        if result.error:
            self.warn(f"({result.error})")

        return result

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def handle_command(self, message: str) -> bool:
        """Handle a slash command. Returns True when the input was a command."""
        if not message.startswith("/"):
            return False

        command = message.strip().lower().split()[0]
        if command in {"/quit", "/exit", "/q"}:
            self._running = False
            return True
        if command == "/help":
            self.say(
                "/quit leave   /text type   /voice speak   /reset forget   /stats"
            )
            return True
        if command == "/text":
            self.say("Switching to text input.")
            self._running = False
            await self.text_loop()
            self._running = True
            return True
        if command == "/voice":
            self.warn("Voice mode is already active.")
            return True
        if command == "/reset":
            self.agent.memory.clear()
            self.agent.memory.save()
            self.say("I've forgotten this conversation, sir.")
            return True
        if command == "/stats":
            self.say(
                f"turns={self._turns} errors={self._errors} "
                f"last_turn_ms={self._last_turn_ms} "
                f"summary={len(self.agent.memory.summary)}B"
            )
            return True
        self.warn(f"Unknown command '{command}'. Try /help.")
        return True

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Stop the loop cleanly on Ctrl-C, without cancelling mid-turn."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, self._request_stop)

    def _request_stop(self) -> None:
        """Ask the loop to finish after the current turn."""
        if not self._running:
            return
        self._running = False
        self.say("Shutting down, sir.")

    async def run(self) -> None:
        """Start the session and block until it ends."""
        await self.agent.warm_up()
        self.say(_BANNER)

        mic = detect_status(self.settings)
        if not self.transcriber.available:
            self.warn("GROQ_API_KEY is not set, so voice input is unavailable.")
        if mic.usable and not self._text_only and self.transcriber.available:
            await self.voice_loop()
        else:
            await self.text_loop()

    async def aclose(self) -> None:
        """Release resources, saving memory first."""
        with contextlib.suppress(Exception):
            await self.agent.aclose()
        with contextlib.suppress(Exception):
            await close_pool()


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    """Wrap raw PCM in a WAV container."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def segment_seconds(pcm: bytes, rate: int = 16000) -> float:
    """Length of a PCM buffer in seconds."""
    return len(pcm) / float(2 * rate)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="A JARVIS-style voice and text assistant.",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Start in text mode and never open the microphone.",
    )
    parser.add_argument(
        "--no-speak",
        action="store_true",
        help="Print answers instead of speaking them.",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Override the Edge TTS voice, e.g. en-US-JennyNeural.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override the log level.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Also write newline-delimited JSON logs to this path.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report configuration and provider readiness, then exit.",
    )
    return parser


def _apply_overrides(args: argparse.Namespace) -> Settings:
    """Merge CLI flags over the environment configuration."""
    overrides: dict[str, object] = {}
    if args.no_speak:
        overrides["agent_speak"] = False
    if args.voice:
        overrides["tts_voice"] = args.voice
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.log_file:
        overrides["log_file"] = args.log_file

    from .config import Settings as BaseSettings

    return BaseSettings(**overrides)  # type: ignore[arg-type]


def _check(settings: Settings) -> int:
    """Print readiness diagnostics. Returns a process exit code."""
    from .net import is_port_open
    from urllib.parse import urlparse

    configure_logging(settings)
    status = detect_status(settings)
    parsed = urlparse(settings.ollama_base_url)
    ollama_up = is_port_open(
        parsed.hostname or "127.0.0.1", parsed.port or 11434, timeout=1.0
    )

    rows = [
        ("Groq LLM", "configured" if settings.groq_configured else "MISSING", settings.groq_model),
        ("Groq STT", "configured" if settings.groq_configured else "MISSING", settings.groq_stt_model),
        ("Ollama fallback", "reachable" if ollama_up else "not running", settings.ollama_model),
        ("Web search", "enabled" if settings.search_enabled else "disabled", settings.search_url),
        ("Microphone", status.backend, f"{status.sample_rate} Hz"),
        ("Speech", "ready" if Speaker(settings).available else "unavailable", settings.tts_voice),
        ("Memory", f"window {settings.memory_window}", settings.memory_state_path),
    ]
    width = max(len(name) for name, _, _ in rows)
    print("\nJARVIS readiness")
    print("-" * (width + 34))
    for name, state, detail in rows:
        print(f"{name:<{width}}  {state:<12}  {detail}")
    print("-" * (width + 34))

    if not settings.groq_configured:
        print("\nNo GROQ_API_KEY set: the agent will run in offline mode.")
    return 0 if settings.groq_configured else 1


async def _run(args: argparse.Namespace) -> int:
    """Start a session and supervise it with a recovery loop."""
    settings = _apply_overrides(args)
    configure_logging(settings)
    logger.info("jarvis starting %s", settings.redacted())

    session = Session(settings, text_only=args.text)
    restarts = 0
    try:
        while True:
            try:
                await session.run()
                break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the outer recovery net
                restarts += 1
                logger.exception("session crashed (restart %d)", restarts)
                if restarts >= 3:
                    logger.error("too many restarts; giving up")
                    return 1
                await asyncio.sleep(2.0)
                session = Session(settings, text_only=args.text)
    finally:
        await session.aclose()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Never lets an exception escape as a traceback.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.check:
        return _check(settings)

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nGoodbye, sir.", flush=True)
        return 0
    except Exception:  # noqa: BLE001
        configure_logging(settings)
        logger.exception("jarvis exited unexpectedly")
        print(
            "JARVIS stopped unexpectedly. The log above has the details.",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
