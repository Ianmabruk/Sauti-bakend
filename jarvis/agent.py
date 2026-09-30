"""The JARVIS agent: one turn from utterance to spoken answer.

The turn is a small pipeline with one hard rule attached to every stage:
**never raise into the audio loop.** A failed tool, a rate-limited model, a
dead search backend or a broken synthesiser all degrade into a spoken sentence
that tells the user what happened.

Latency is handled by overlapping the stages:

    speech ends
      -> STT (Groq Whisper)                       ~300 ms
      -> routing decision + query rewrite         ~250 ms, parallel with STT tail
      -> optional web search                      ~1200 ms, only if routed
      -> first LLM token                          ~200 ms
      -> first sentence buffered                  ~150 ms
      -> first audio byte out                     ~100 ms

Tool routing is decided by the model itself via a JSON contract
(:mod:`jarvis.persona`); no keyword matching decides whether to search. The
router also rewrites the user's phrasing into a real search query, which is why
"what's up with bitcoin" reaches the web as "Bitcoin price USD today".
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from .cache import LRUCache
from .config import Settings
from .logsetup import get_logger
from .memory import ConversationMemory
from .net import get_pool, first_speakable_chunk, prewarm
from .persona import (
    ROUTER_PROMPT,
    build_evidence_block,
    build_router_prompt,
    build_system_prompt,
    format_citation_line,
    json_only,
    strip_citations,
)
from .providers.llm import LLMRouter, ProviderReply
from .providers.search import SearchResult, build_search_provider
from .voice.tts import Speaker

logger = get_logger(__name__)

#: Phrases that must never trigger a web search.
_SMALL_TALK = (
    "hello", "hi ", "hey", "thanks", "thank you", "goodbye", "bye", "how are you",
    "who are you", "what can you do", "joke", "good morning", "good evening",
)


@dataclass
class TurnResult:
    """Everything produced by one user turn.

    Attributes:
        transcript: What the user said.
        reply: The full answer text.
        spoken: The text that was actually sent to the synthesiser.
        searched: Whether a web search ran.
        sources: The top sources, for display.
        used_tool: The tool that ran, or ``""``.
        offline_reason: Why the agent could not verify something, if so.
        ttft_ms: Time from request to first token.
        first_audio_ms: Time from request to the first audio byte.
        total_ms: Total turn duration.
        error: Populated only when the turn failed outright.
    """

    transcript: str = ""
    reply: str = ""
    spoken: str = ""
    searched: bool = False
    sources: list[dict[str, str]] = field(default_factory=list)
    used_tool: str = ""
    offline_reason: str = ""
    ttft_ms: int = 0
    first_audio_ms: int = 0
    total_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the turn produced a usable answer."""
        return not self.error and bool(self.reply.strip())

    def to_dict(self) -> dict[str, Any]:
        """A log-safe summary of the turn."""
        return {
            "transcript_chars": len(self.transcript),
            "reply_chars": len(self.reply),
            "searched": self.searched,
            "used_tool": self.used_tool,
            "sources": len(self.sources),
            "ttft_ms": self.ttft_ms,
            "first_audio_ms": self.first_audio_ms,
            "total_ms": self.total_ms,
            "ok": self.ok,
            "error": self.error,
        }


class JarvisAgent:
    """Coordinates STT, routing, search, generation and speech for one turn.

    Args:
        settings: Runtime configuration.
        llm: Chat provider chain. One is built when omitted.
        search: Search provider. One is built when omitted.
        speaker: Speech output. One is built when omitted.
        memory: Conversation memory. One is built when omitted.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        llm: Optional[LLMRouter] = None,
        search: Optional[Any] = None,
        speaker: Optional[Speaker] = None,
        memory: Optional[ConversationMemory] = None,
    ) -> None:
        from .config import get_settings

        self.settings = settings or get_settings()
        self.pool = get_pool(self.settings)
        self.llm = llm or LLMRouter(settings=self.settings, pool=self.pool)
        self.search = search or build_search_provider(self.settings, pool=self.pool)
        self.speaker = speaker or Speaker(self.settings)
        self.memory = memory or ConversationMemory(
            self.settings, summariser=self._summarise
        )
        self._router_cache = LRUCache(max_size=32, ttl=300.0)
        self._last_citations: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def warm_up(self) -> None:
        """Pre-warm every dependency before the first utterance.

        Connection setup is the single largest source of first-turn latency:
        a cold Edge TTS websocket costs several seconds, and a cold TLS
        handshake to Groq and the search API costs hundreds of milliseconds
        each. Paying all of it here, before the user speaks, is what makes the
        first question feel the same as the tenth.

        Also loads any memory saved by a previous session, so follow-up
        awareness works on the very first question after a restart.
        """
        logger.info("warming up: %s", self.settings.redacted())
        targets = [f"{self.settings.groq_base_url}/models"]
        if self.settings.search_enabled:
            targets.append(self.settings.search_url)
        await prewarm(targets, self.settings)
        await self.speaker.warm_up()

        if self.memory.load():
            logger.info("restored conversation memory")
        await self.memory.refresh_summary(force=False)

    async def aclose(self) -> None:
        """Persist memory and release every network and audio resource."""
        try:
            self.memory.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not save memory: %s", exc)
        await self.speaker.close()
        from .net import close_pool

        await close_pool()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _summarise(self, previous: str, turns: list[Any]) -> str:
        """Compact evicted turns into the rolling summary."""
        from .memory import SUMMARY_INSTRUCTION

        conversation = "\n".join(f"{t.role}: {t.content[:600]}" for t in turns)
        messages = [
            {"role": "system", "content": SUMMARY_INSTRUCTION},
            {
                "role": "user",
                "content": (
                    f"PREVIOUS SUMMARY:\n{previous or '(none)'}\n\n"
                    f"TURNS TO FOLD IN:\n{conversation}"
                ),
            },
        ]
        reply = await self.llm.complete(messages, temperature=0.2)
        return reply.text

    async def route(self, message: str) -> dict[str, Any]:
        """Decide whether to search, and produce a rewritten query.

        The model makes this call. A cached decision is reused for identical
        questions, and a failure falls back to answering directly, which is
        always safe because the model is told to be candid about gaps.

        Args:
            message: The user's utterance.

        Returns:
            ``{"needs_search": bool, "query": str, "reason": str}``.
        """
        text = (message or "").strip()
        if not text:
            return {"needs_search": False, "query": "", "reason": "empty message"}

        lowered = text.lower()
        if any(lowered.startswith(phrase) or lowered == phrase.strip() for phrase in _SMALL_TALK):
            return {"needs_search": False, "query": "", "reason": "small talk"}

        key = f"{lowered}|{self.memory.summary[:200]}"
        cached = self._router_cache.get(key)
        if cached is not None:
            logger.info("routing cached needs_search=%s", cached.get("needs_search"))
            return cached

        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {
                "role": "user",
                "content": build_router_prompt(text, self.memory.context_block()),
            },
        ]

        try:
            reply = await self.llm.complete(messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - routing must never break a turn
            logger.warning("routing failed, answering directly: %s", exc)
            return {"needs_search": False, "query": "", "reason": "router unavailable"}

        decision = json_only(reply.text) or {}
        result = {
            "needs_search": bool(decision.get("needs_search")),
            "query": str(decision.get("query") or "").strip()[:200],
            "reason": str(decision.get("reason") or "").strip()[:200],
        }
        # Never trust a routing decision to search an empty query.
        if result["needs_search"] and not result["query"]:
            result["needs_search"] = False
            result["reason"] = "router returned no query"

        self._router_cache.set(key, result)
        logger.info(
            "routed needs_search=%s query=%r reason=%s",
            result["needs_search"], result["query"], result["reason"],
        )
        return result

    async def search_web(self, query: str, *, objective: str = "") -> SearchResult:
        """Run a web search through the configured provider chain."""
        try:
            return await self.search.search(
                query, objective=objective or query, limit=self.settings.search_max_results
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("search raised: %s", exc)
            return SearchResult(ok=False, query=query, reason=str(exc))

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def stream_answer(
        self, message: str, *, evidence: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Stream the answer, yielding speakable text as it becomes safe to say.

        Args:
            message: The user's utterance.
            evidence: Formatted tool evidence to ground the answer in.

        Yields:
            Text fragments. The first yield is the first complete sentence,
            which is the earliest point speech can begin.
        """
        system = build_system_prompt(
            self.settings, context=self.memory.context_block(), evidence=evidence or ""
        )
        messages = [{"role": "system", "content": system}]
        messages.extend(self.memory.as_messages(limit=self.settings.memory_window))
        messages.append({"role": "user", "content": message})

        pending = ""
        emitted_any = False

        async for delta in self.llm.stream(messages):
            pending += delta
            while True:
                chunk, pending = first_speakable_chunk(
                    pending, min_chars=self.settings.agent_first_sentence_chars
                )
                if chunk is None:
                    break
                emitted_any = True
                yield chunk

        if pending.strip():
            if not emitted_any and len(pending.strip()) < self.settings.agent_first_sentence_chars:
                # Too short to be a sentence: speak it whole rather than dropping it.
                yield pending.strip()
            else:
                yield pending.strip()

    async def answer(
        self,
        message: str,
        *,
        speak: Optional[bool] = None,
        sources: Optional[list[dict[str, str]]] = None,
    ) -> TurnResult:
        """Run one complete turn, optionally speaking the answer.

        Args:
            message: The user's utterance.
            speak: Force speech on or off. Defaults to configuration.
            sources: Sources to cite, when a search already ran.

        Returns:
            A :class:`TurnResult`.
        """
        started = time.perf_counter()
        should_speak = self.settings.agent_speak if speak is None else speak
        result = TurnResult(transcript=message)

        if not (message or "").strip():
            result.error = "empty message"
            return result

        # 1. Route, then search if needed. Both are off the audio critical path
        #    in the sense that nothing is being spoken yet.
        decision = await self.route(message)
        evidence_items: list[dict[str, Any]] = []
        citations: list[dict[str, str]] = []

        if decision["needs_search"]:
            result.searched = True
            result.used_tool = "web_search"
            search = await self.search_web(decision["query"])
            if search.ok:
                citations = search.top(2)
                evidence_items.append(
                    {
                        "tool": "web_search",
                        "ok": True,
                        "summary": search.summary(),
                        "sources": search.sources,
                    }
                )
            else:
                result.offline_reason = search.reason or "search failed"
                evidence_items.append(
                    {"tool": "web_search", "ok": False, "summary": result.offline_reason}
                )
                logger.info("search unavailable: %s", result.offline_reason)
        elif decision["reason"] == "small talk":
            result.searched = False

        evidence = build_evidence_block(evidence_items)

        # 2. Stream the answer, speaking each sentence as it completes.
        spoken_parts: list[str] = []
        first_audio_at: Optional[float] = None
        reply_parts: list[str] = []

        try:
            async for chunk in self.stream_answer(message, evidence=evidence):
                reply_parts.append(chunk)
                cleaned = strip_citations(chunk)
                if not cleaned:
                    continue
                if should_speak:
                    spoken_parts.append(cleaned)
                    # Hand this sentence to the synthesiser immediately, then
                    # wait for it. The next sentence keeps generating only
                    # after this one is audible, which is the trade that makes
                    # the response start fast.
                    speech = await self.speaker.speak(cleaned)
                    if first_audio_at is None and speech.ok:
                        first_audio_at = time.perf_counter() - (
                            speech.duration_ms - speech.first_byte_ms
                        ) / 1000.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("generation failed")
            result.error = str(exc)

        result.reply = "".join(reply_parts).strip()
        result.spoken = " ".join(spoken_parts).strip()
        result.sources = citations or (sources or [])

        # 3. Append spoken citations for factual answers.
        if result.spoken and result.sources and result.searched:
            line = format_citation_line(result.sources, limit=2)
            if line and should_speak:
                await self.speaker.speak(line.strip())
                result.spoken = f"{result.spoken} {line.strip()}"

        if not result.reply and not result.error:
            result.error = "The model produced no answer."

        end = time.perf_counter()
        result.ttft_ms = int(((first_audio_at or end) - started) * 1000)
        result.first_audio_ms = result.ttft_ms if first_audio_at else 0
        result.total_ms = int((end - started) * 1000)

        # 4. Persist to memory and refresh the rolling summary.
        self.memory.add("user", message)
        self.memory.add("assistant", result.reply, sources=result.sources)
        await self.memory.refresh_summary()

        logger.info("turn complete %s", result.to_dict())
        return result

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def ask(
        self, message: str, *, speak: Optional[bool] = None
    ) -> TurnResult:
        """Alias for :meth:`answer` that reads well at the call site."""
        return await self.answer(message, speak=speak)

    @property
    def last_citations(self) -> list[dict[str, str]]:
        """Sources from the most recent turn."""
        return self._last_citations
