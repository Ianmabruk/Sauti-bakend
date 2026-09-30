"""LLM providers: Groq streaming chat with a local Ollama fallback.

The routing policy implements the reliability requirement directly:

1. Try **Groq** (free tier, Llama 3.3 70B).
2. On 429, retry with exponential backoff, announcing the wait.
3. When the budget is exhausted, fall back to a **local Ollama** model if one
   is reachable.
4. If neither is available, return an honest, spoken apology rather than
   raising into the audio loop.

Streaming matters for latency. :meth:`LLMProvider.stream` yields text deltas as
they arrive so the caller can begin speech on the first complete sentence
instead of waiting for the whole answer. A retry is only safe *before* the
first token has been yielded, because partial output cannot be un-said; once a
caller has begun speaking, the stream is allowed to fail quietly.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx

from ..cache import LRUCache
from ..config import Settings
from ..logsetup import get_logger
from ..net import HttpPool, get_pool, is_port_open
from ..retry import (
    ProviderError,
    RateLimited,
    RetryPolicy,
    with_retry,
)

logger = get_logger(__name__)


@dataclass
class ProviderReply:
    """A completed (or abandoned) model response.

    Attributes:
        text: The answer text. Empty when the provider failed.
        ok: False when the provider was exhausted or unavailable.
        model: The model that produced the text.
        degraded: True when a fallback engine answered.
        reason: Short explanation when ``ok`` is False.
        ttft_ms: Time to first token, for latency logging.
        duration_ms: Total wall time.
        prompt_tokens: Prompt token count when reported.
        completion_tokens: Completion token count when reported.
        cached: True when served from the reply cache.
    """

    text: str = ""
    ok: bool = True
    model: str = ""
    degraded: bool = False
    reason: str = ""
    ttft_ms: int = 0
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        """A log-safe summary."""
        return {
            "model": self.model,
            "ok": self.ok,
            "degraded": self.degraded,
            "chars": len(self.text),
            "ttft_ms": self.ttft_ms,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
            "reason": self.reason,
        }


def _cache_key(model: str, messages: list[dict[str, str]], temperature: float) -> str:
    """Build a stable key for the reply cache."""
    blob = json.dumps(
        {"m": model, "msg": messages, "t": round(temperature, 3)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMProvider:
    """Base class defining the provider contract."""

    name: str = "base"

    @property
    def available(self) -> bool:
        """True when this provider could answer right now."""
        raise NotImplementedError

    async def stream(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Yield answer text incrementally.

        Args:
            messages: Chat history including the system prompt.
            temperature: Optional override of the configured default.

        Yields:
            Text deltas, possibly empty strings.
        """
        raise NotImplementedError

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> ProviderReply:
        """Return a complete answer.

        Args:
            messages: Chat history including the system prompt.
            temperature: Optional override of the configured default.

        Returns:
            A :class:`ProviderReply`.
        """
        chunks: list[str] = []
        async for delta in self.stream(messages, temperature=temperature):
            chunks.append(delta)
        return ProviderReply(text="".join(chunks), model=self.name)


class GroqProvider(LLMProvider):
    """Streaming chat against Groq's OpenAI-compatible endpoint.

    Args:
        settings: Supplies the key, model and retry policy.
        pool: Shared HTTP pool; one is created on demand when omitted.
        cache: Optional reply cache. Streaming is bypassed on a hit.
    """

    name = "groq"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        pool: Optional[HttpPool] = None,
        cache: Optional[LRUCache] = None,
    ) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.pool = pool
        self.cache = cache
        self._rate_limited_until = 0.0

    @property
    def available(self) -> bool:
        """True when a Groq key is configured and the cooldown has passed."""
        return self.settings.groq_configured and time.monotonic() >= self._rate_limited_until

    def _client(self) -> HttpPool:
        return self.pool or get_pool(self.settings)

    @property
    def policy(self) -> RetryPolicy:
        """Retry policy derived from configuration."""
        return RetryPolicy(
            attempts=self.settings.llm_max_retries + 1,
            base=self.settings.llm_backoff_base,
            maximum=self.settings.llm_backoff_max,
        )

    def _body(
        self, messages: list[dict[str, str]], temperature: float, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self.settings.groq_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": stream,
            # Ask the model to stop wandering; the persona prompt handles tone.
            "top_p": 0.9,
        }

    def _note_rate_limit(self, exc: BaseException) -> None:
        """Open a local cooldown so later turns skip Groq immediately."""
        self._rate_limited_until = time.monotonic() + 30.0
        logger.warning(
            "groq rate-limited; local cooldown 30s until=%d", int(self._rate_limited_until)
        )

    async def _stream_once(
        self,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> AsyncIterator[str]:
        """One streaming attempt. Yields deltas, or raises a retryable error."""
        pool = self._client()
        payload = self._body(messages, temperature, stream=True)

        async with pool.client.stream(
            "POST",
            f"{self.settings.groq_base_url}/chat/completions",
            json=payload,
            headers=self.settings.groq_auth_headers,
            timeout=httpx.Timeout(self.settings.llm_timeout, connect=5.0),
        ) as response:
            if response.status_code >= 400:
                # Consume the body so the error message is meaningful, then
                # raise something raise_for_status can classify.
                await response.aread()
                response.raise_for_status()

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    if chunk == "[DONE]":
                        return
                    continue
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    logger.debug("skipping malformed stream chunk")
                    continue
                for choice in event.get("choices", []) or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta

    async def stream(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Stream from Groq, retrying only while nothing has been said yet.

        Args:
            messages: Chat history including the system prompt.
            temperature: Optional override of the configured default.

        Yields:
            Text deltas.
        """
        temp = self.settings.llm_temperature if temperature is None else temperature
        cache_key = _cache_key(self.settings.groq_model, messages, temp)

        if self.cache is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                logger.info("llm cache hit model=%s chars=%d", self.name, len(hit))
                for piece in _as_deltas(hit):
                    yield piece
                return

        if not self.settings.groq_configured:
            raise ProviderError("No Groq API key is configured.")

        attempt = 0
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        collected: list[str] = []
        max_attempts = self.settings.llm_max_retries + 1

        while attempt < max_attempts:
            attempt += 1
            spoke = False  # once True, retrying is no longer safe
            try:
                async for delta in self._stream_once(messages, temp):
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                        logger.info(
                            "ttft_ms=%d model=%s",
                            int((first_token_at - started) * 1000),
                            self.settings.groq_model,
                        )
                    collected.append(delta)
                    spoke = True
                    yield delta
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if spoke:
                    # The user is already hearing a partial answer. Stop quietly.
                    logger.warning("stream broke after first token: %s", type(exc).__name__)
                    return
                from ..retry import classify, is_retryable

                if not is_retryable(exc) or attempt >= max_attempts:
                    raise classify(exc) from exc

                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    self._note_rate_limit(exc)

                delay = self.policy.delay_for(attempt)
                logger.warning(
                    "groq stream failed attempt=%d/%d delay=%.2fs error=%s",
                    attempt, max_attempts, delay, type(exc).__name__,
                )
                await asyncio.sleep(delay)
                continue

            if self.cache is not None:
                self.cache.set(cache_key, "".join(collected))
            return

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> ProviderReply:
        """Return a complete Groq answer with latency accounting."""
        started = time.perf_counter()
        chunks: list[str] = []
        first_at: Optional[float] = None
        temp = self.settings.llm_temperature if temperature is None else temperature

        async for delta in self.stream(messages, temperature=temp):
            if first_at is None:
                first_at = time.perf_counter()
            chunks.append(delta)

        text = "".join(chunks)
        if self.cache is not None:
            self.cache.set(_cache_key(self.settings.groq_model, messages, temp), text)

        return ProviderReply(
            text=text,
            model=self.settings.groq_model,
            ttft_ms=int(((first_at or time.perf_counter()) - started) * 1000),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class OllamaProvider(LLMProvider):
    """Local model fallback served by Ollama.

    Used when Groq rate-limits us. Ollama exposes an OpenAI-compatible
    ``/v1/chat/completions`` endpoint, so this reuses the same request shape.

    Args:
        settings: Supplies the base URL, model and timeouts.
        pool: Optional shared HTTP pool.
    """

    name = "ollama"

    def __init__(
        self, settings: Optional[Settings] = None, *, pool: Optional[HttpPool] = None
    ) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.pool = pool
        self._reachable: Optional[bool] = None
        self._checked_at = 0.0

    def _endpoint(self) -> str:
        return f"{self.settings.ollama_base_url.rstrip('/')}/v1/chat/completions"

    @property
    def available(self) -> bool:
        """True when Ollama is enabled and a local port answers.

        The probe result is cached for 30 seconds so a turn does not pay a TCP
        connect against a server that is not there.
        """
        if not self.settings.ollama_enabled:
            return False
        now = time.monotonic()
        if self._reachable is not None and (now - self._checked_at) < 30.0:
            return self._reachable

        from urllib.parse import urlparse

        parsed = urlparse(self.settings.ollama_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        self._reachable = is_port_open(host, port, timeout=self.settings.ollama_probe_timeout)
        self._checked_at = now
        if not self._reachable:
            logger.debug("ollama not reachable at %s:%s", host, port)
        return self._reachable

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": "Bearer ollama"}

    def _body(
        self, messages: list[dict[str, str]], temperature: float, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self.settings.ollama_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": stream,
        }

    async def _stream_once(
        self, messages: list[dict[str, str]], temperature: float
    ) -> AsyncIterator[str]:
        """One streaming attempt against Ollama."""
        pool = self.pool or get_pool(self.settings)
        async with pool.client.stream(
            "POST",
            self._endpoint(),
            json=self._body(messages, temperature, True),
            headers=self._headers(),
            timeout=httpx.Timeout(self.settings.ollama_timeout, connect=3.0),
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk:
                    continue
                if chunk == "[DONE]":
                    return
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                for choice in event.get("choices", []) or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta

    async def stream(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Stream from the local model.

        Args:
            messages: Chat history including the system prompt.
            temperature: Optional override of the configured default.

        Yields:
            Text deltas.

        Raises:
            ProviderError: When Ollama is disabled or unreachable.
        """
        if not self.available:
            raise ProviderError("No local model is available.")
        temp = self.settings.llm_temperature if temperature is None else temperature

        attempt = 0
        max_attempts = 2
        while attempt < max_attempts:
            attempt += 1
            spoke = False
            try:
                async for delta in self._stream_once(messages, temp):
                    spoke = True
                    yield delta
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if spoke or attempt >= max_attempts:
                    raise ProviderError(f"Local model failed: {exc}") from exc
                await asyncio.sleep(0.4 * attempt)

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> ProviderReply:
        """Return a complete local answer, flagged as degraded."""
        started = time.perf_counter()
        chunks: list[str] = []
        async for delta in self.stream(messages, temperature=temperature):
            chunks.append(delta)
        return ProviderReply(
            text="".join(chunks),
            model=self.settings.ollama_model,
            degraded=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class OfflineProvider(LLMProvider):
    """Last-resort provider that answers without any network.

    Keeps the agent usable when both Groq and Ollama are gone, by reflecting
    the user's message back in a way that is honest about being degraded. It
    never fabricates facts.
    """

    name = "offline"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    async def stream(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Yield a short, honest apology. Never raises."""
        last_user = next(
            (
                m.get("content", "")
                for m in reversed(messages)
                if m.get("role") == "user"
            ),
            "",
        )
        text = (
            "I'm afraid my language model is unavailable at the moment, sir. "
            "I can't answer that right now, but I'll be ready as soon as the "
            "connection comes back."
        )
        if last_user:
            logger.warning("offline fallback answering: %.60s", last_user)
        for delta in _as_deltas(text):
            yield delta
            await asyncio.sleep(0.004)

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> ProviderReply:
        """Return the offline message, flagged as degraded."""
        chunks: list[str] = []
        async for delta in self.stream(messages, temperature=temperature):
            chunks.append(delta)
        return ProviderReply(text="".join(chunks), model="offline", degraded=True)


@dataclass
class LLMRouter:
    """Chooses among providers, degrading rather than failing.

    Order: Groq, then Ollama, then offline. A provider that is rate-limited or
    down is skipped for the rest of the turn.

    Args:
        settings: Configuration.
        pool: Optional shared HTTP pool.
        on_degrade: Optional async callback invoked with a human-readable
            notice when a fallback is used, so the agent can tell the user why
            the answer quality just changed.
    """

    settings: Settings
    pool: Optional[HttpPool] = None
    on_degrade: Optional[Any] = None
    groq: GroqProvider = field(init=False)
    ollama: OllamaProvider = field(init=False)
    offline: OfflineProvider = field(init=False)

    def __post_init__(self) -> None:
        cache = (
            LRUCache(
                max_size=self.settings.llm_cache_max,
                ttl=self.settings.llm_cache_ttl or None,
            )
            if self.settings.llm_cache_max > 0
            else None
        )
        self.groq = GroqProvider(self.settings, pool=self.pool, cache=cache)
        self.ollama = OllamaProvider(self.settings, pool=self.pool)
        self.offline = OfflineProvider(self.settings)

    def _chain(self) -> list[LLMProvider]:
        providers: list[LLMProvider] = []
        if self.groq.available:
            providers.append(self.groq)
        if self.ollama.available:
            providers.append(self.ollama)
        if not providers:
            providers.append(self.offline)
        return providers

    async def _notify(self, notice: str) -> None:
        if self.on_degrade is None:
            return
        try:
            result = self.on_degrade(notice)
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.debug("degrade notice failed: %s", exc)

    async def stream(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        """Stream an answer, falling back through the provider chain.

        Yields:
            Text deltas from the first provider that produces any.
        """
        providers = self._chain()
        if len(providers) > 1:
            logger.info("provider chain: %s", [p.name for p in providers])

        for index, provider in enumerate(providers):
            emitted = False
            try:
                async for delta in provider.stream(messages, temperature=temperature):
                    emitted = emitted or bool(delta)
                    yield delta
                if emitted:
                    return
                if index == len(providers) - 1:
                    return
            except asyncio.CancelledError:
                raise
            except (RateLimited, ProviderError, httpx.HTTPError) as exc:
                if emitted:
                    logger.warning("provider %s broke mid-stream", provider.name)
                    return
                logger.warning(
                    "provider %s failed (%s), trying next", provider.name, exc
                )
                if index == len(providers) - 1:
                    break
                if isinstance(exc, RateLimited) or not self.ollama.available:
                    await self._notify(
                        "I'm rate limited upstream, sir. Falling back to the "
                        "local model."
                    )
        # Everything failed: the offline provider always yields something.
        async for delta in self.offline.stream(messages, temperature=temperature):
            yield delta

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: Optional[float] = None
    ) -> ProviderReply:
        """Return a complete answer, falling back through the provider chain."""
        started = time.perf_counter()
        chunks: list[str] = []
        first_at: Optional[float] = None

        async for delta in self.stream(messages, temperature=temperature):
            if first_at is None and delta:
                first_at = time.perf_counter()
            chunks.append(delta)

        text = "".join(chunks)
        degraded = text.startswith("I'm afraid my language model is unavailable")
        return ProviderReply(
            text=text,
            model=self.groq.name if not degraded else "offline",
            ok=bool(text.strip()),
            degraded=degraded,
            ttft_ms=int(((first_at or time.perf_counter()) - started) * 1000),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _as_deltas(text: str) -> list[str]:
    """Split text into small deltas that stream at a natural reading pace.

    Used when replaying a cached or offline answer so the synthesiser is fed
    progressively rather than in one lump.
    """
    if not text:
        return []
    deltas: list[str] = []
    buffer = ""
    for word in text.split(" "):
        buffer += word + " "
        if len(buffer) >= 12:
            deltas.append(buffer)
            buffer = ""
    if buffer:
        deltas.append(buffer)
    return deltas
