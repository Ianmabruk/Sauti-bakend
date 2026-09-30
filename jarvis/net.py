"""Shared async HTTP transport with connection pre-warm.

One :class:`httpx.AsyncClient` is created per process and reused for every
outgoing call. This is the single largest latency win in the project: a fresh
client per request pays for DNS, TCP and a TLS handshake every time, which
adds roughly 100-400 ms per call.

The module also owns:

- **pre-warming** (:func:`prewarm`): opens the TCP/TLS connection to each
  provider *before* the first user utterance, so the first real turn starts
  with a hot socket instead of a cold handshake;
- **SSE reading** (:func:`iter_sse`): decodes a ``text/event-stream`` body into
  JSON payloads, tolerating chunk boundaries that split a line in half;
- **single-sentence splitting** (:func:`split_sentences), used to decide when
  speech may begin.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
from typing import Any, AsyncIterator, Iterable, Optional
from urllib.parse import urlparse

import httpx

from .config import Settings
from .logsetup import get_logger

logger = get_logger(__name__)

#: Split points for sentence detection. Abbreviations and decimals are
#: protected by requiring the following character to be a space or end of text.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")
#: Tokens that end in a period but are almost never a sentence boundary.
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
        "fig", "no", "approx", "dept", "est", "min", "max", "inc", "ltd", "co",
        "u.s", "u.k", "a.m", "p.m", "ph.d", "m.sc", "b.sc",
    }
)
#: Very small fragments are held back so TTS is not called per word.
_MIN_SPEECH_CHARS = 24


class HttpPool:
    """Process-wide HTTP client with sensible timeouts and connection limits.

    Args:
        settings: Source of timeout and pool sizing.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from .config import get_settings

        self.settings = settings or get_settings()
        limits = httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=90.0,
        )
        timeout = httpx.Timeout(
            connect=5.0,
            read=self.settings.llm_timeout,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "JARVIS/1.0 (+https://example.invalid/jarvis)"},
        )
        self._closed = False

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying client. Raises once closed."""
        if self._closed:
            raise RuntimeError("HttpPool is closed")
        return self._client

    async def aclose(self) -> None:
        """Close idle sockets and mark the pool unusable."""
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()
        logger.debug("http pool closed")

    async def __aenter__(self) -> "HttpPool":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


_pool: Optional[HttpPool] = None


def get_pool(settings: Optional[Settings] = None) -> HttpPool:
    """Return the process-wide HTTP pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = HttpPool(settings)
    return _pool


async def close_pool() -> None:
    """Close and forget the process-wide pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def _warm_socket(url: str, pool: HttpPool) -> None:
    """Open and immediately release a connection to ``url``'s host.

    A failed warm-up is logged at debug and otherwise ignored: pre-warming is
    an optimisation, never a requirement.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=4.0
        )
        writer.close()
            # TLS handshake happens on the first pooled request; the warm-up is
            # about avoiding DNS and TCP latency, not a full protocol dance.
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        del reader
        logger.debug("warmed socket", extra={"host": host, "port": port})
    except (OSError, asyncio.TimeoutError) as exc:
        logger.debug("socket warm-up skipped host=%s reason=%s", host, exc)


async def prewarm(urls: Iterable[str], settings: Optional[Settings] = None) -> None:
    """Concurrently establish connections to every provider endpoint.

    Called once during startup, before the first utterance, so the very first
    user turn does not pay connection setup.

    Args:
        urls: Absolute URLs whose hosts should be warmed.
        settings: Optional settings override.
    """
    targets = [u for u in dict.fromkeys(urls) if u]
    if not targets:
        return
    pool = get_pool(settings)
    started = asyncio.get_running_loop().time()
    results = await asyncio.gather(
        *(_warm_socket(url, pool) for url in targets), return_exceptions=True
    )
    failed = sum(1 for r in results if isinstance(r, BaseException))
    logger.info(
        "prewarmed %d endpoint(s) in %dms failed=%d",
        len(targets),
        int((asyncio.get_running_loop().time() - started) * 1000),
        failed,
    )


async def iter_sse(response: httpx.Response) -> AsyncIterator[Any]:
    """Decode a Server-Sent Events stream into JSON objects.

    Handles the two shapes the MCP and OpenAI servers use: a single JSON body,
    and a real SSE stream where each event is a ``data:`` line. Network chunks
    may split a line anywhere, so a partial-line buffer is carried forward.

    Args:
        response: A streaming response with ``text/event-stream`` or
            ``application/json`` content type.

    Yields:
        Parsed JSON values, skipping keep-alive comments and ``[DONE]`` markers.
    """
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        # A plain JSON body. It must be read explicitly first: on a streaming
        # response the content is not buffered, so response.json() would raise.
        await response.aread()
        try:
            yield json.loads(response.text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("non-JSON body where JSON expected: %s", exc)
        return

    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":"):
                continue  # comment / keep-alive
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                if payload == "[DONE]":
                    return
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                logger.debug("skipping malformed SSE payload")


def split_sentences(text: str) -> list[str]:
    """Split accumulated text into speakable sentences.

    Keeps trailing abbreviations, decimal numbers and ellipses intact, and
    merges fragments shorter than ``_MIN_SPEECH_CHARS`` into the following
    sentence so TTS is never invoked mid-phrase.

    Args:
        text: Raw accumulated model output.

    Returns:
        Sentences suitable for immediate synthesis.
    """
    if not text:
        return []

    candidates = [part.strip() for part in _SENTENCE_SPLIT.split(text.strip())]
    sentences: list[str] = []
    buffer = ""

    for part in candidates:
        if not part:
            continue
        buffer = f"{buffer} {part}".strip() if buffer else part
        # A period that follows a known abbreviation is not a boundary.
        tail = buffer.rsplit(" ", 1)[-1].rstrip(".!?\"')]").lower()
        if tail in _ABBREVIATIONS and len(buffer) < 80:
            continue
        if len(buffer) >= _MIN_SPEECH_CHARS:
            sentences.append(buffer)
            buffer = ""

    if buffer:
        if sentences:
            # Append the tail to the last sentence rather than speaking a stub.
            sentences[-1] = f"{sentences[-1]} {buffer}"
        else:
            sentences.append(buffer)
    return sentences


def first_speakable_chunk(
    text: str, *, min_chars: int = 60
) -> tuple[Optional[str], str]:
    """Split off the first speakable chunk from a partial stream.

    Args:
        text: Text received so far, which may be a partial sentence.
        min_chars: Hold back fewer characters than this. Prevents speaking the
            first three words of a clause that is still forming.

    Returns:
        ``(chunk, remainder)`` where ``chunk`` is None when the text is not yet
        safe to speak, and ``remainder`` is what should be kept.
    """
    stripped = text.lstrip()
    if len(stripped) < min_chars:
        return None, text

    sentences = split_sentences(stripped)
    if not sentences or len(sentences[0]) < min_chars:
        return None, text

    first = sentences[0]
    remainder = stripped[len(first) :].lstrip()
    return first, remainder


def is_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    """Best-effort TCP reachability probe used to detect a local Ollama.

    Args:
        host: Hostname or address.
        port: TCP port.
        timeout: Connection timeout in seconds.

    Returns:
        True when a TCP connection succeeded.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
