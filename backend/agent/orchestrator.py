"""The SAUTI orchestrator.

    user message
      -> language analysis
      -> memory recall
      -> plan (direct answer vs tool)
      -> tool execution through the registry
      -> evidence assembly
      -> final response
      -> persistence

Every step is logged. The agent never executes model-authored code: tools are
always resolved and validated through the registry.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ai.providers import Message as ProviderMessage

from ..config.settings import Settings
from ..memory.service import MemoryService
from ..services.citations import Source
from ..services.llm import LLMService, LLMUnavailable, summarise_tool_result
from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry
from . import response as response_mod
from .artifacts import ArtifactStore
from .offline import compose_reply
from .language import SautiLanguageDetector, strip_language_request
from .planner import Planner
from .prompts import build_system_prompt

logger = logging.getLogger(__name__)


@dataclass
class SautiTurn:
    """The complete result of handling one user message."""

    message: str
    reply: str
    language: str
    reply_language: str
    is_mixed: bool
    intent: str
    confidence: float
    used_tools: list[str] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    activity: list[str] = field(default_factory=list)
    memory_used: list[str] = field(default_factory=list)
    research_performed: bool = False
    research_available: bool = True
    marketplace: dict | None = None
    offline_fallback: bool = False
    plan_reason: str = ""
    decided_by: str = "heuristic"
    #: Classified intent, e.g. ``place_search``. Distinct from ``intent``,
    #: which the orchestrator sets from whether research ran.
    routed_intent: str = ""
    intent_confidence: float = 0.0
    #: Typed result cards: ``{"type": ..., "data": {...}}``.
    cards: list = field(default_factory=list)
    #: Sidebar mode applied to this turn.
    mode: str = "internet"
    #: Whether the configured AI engine actually produced this reply. False
    #: when the provider failed and the canned `engine_unavailable_message`
    #: was substituted, and false on the offline path where no engine runs at
    #: all. `decided_by` and `engine` name the configured provider either way,
    #: so neither can be used to infer success -- this field can.
    engine_ok: bool = True
    #: Short, safe reason for a failure. Carries no provider internals: no
    #: model name, organisation, quota figure or traceback.
    engine_error: str = ""
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    duration_ms: int = 0
    conversation_id: Optional[str] = None

    def to_dict(self) -> dict:
        from ..services.citations import build_citations

        return {
            "message": self.reply,
            "response": self.reply,
            "language": self.language,
            "reply_language": self.reply_language,
            "is_mixed": self.is_mixed,
            "intent": self.intent,
            "routedIntent": self.routed_intent,
            "intentConfidence": round(self.intent_confidence, 3),
            "cards": self.cards,
            "mode": self.mode,
            "confidence": self.confidence,
            "used_tools": self.used_tools,
            "tool_results": [r.to_dict() for r in self.tool_results],
            "sources": build_citations(self.sources),
            "activity": self.activity,
            "memory_used": self.memory_used,
            "research_performed": self.research_performed,
            "research_available": self.research_available,
            "marketplace": self.marketplace,
            "offline_fallback": self.offline_fallback,
            # Single authoritative success signal. True only when the
            # configured engine actually generated the reply text.
            "engine_ok": self.engine_ok,
            "engine_error": self.engine_error,
            # True whenever the reply is not a live engine answer, so a client
            # can branch on one field instead of combining several.
            "degraded": not self.engine_ok or self.offline_fallback,
            "plan_reason": self.plan_reason,
            "decided_by": self.decided_by,
            "conversation_id": self.conversation_id,
            "request_id": self.request_id,
            "duration_ms": self.duration_ms,
        }


class SautiOrchestrator:
    """Coordinates the full agent loop for a single turn."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        registry: Optional[ToolRegistry] = None,
        memory: Optional[MemoryService] = None,
        llm: Optional[LLMService] = None,
    ):
        self.settings = settings or Settings()
        self.registry = registry or ToolRegistry(self.settings)
        self.llm = llm or LLMService(self.settings)
        self.memory = memory or MemoryService(self.settings)
        # Generated study material and business plans, persisted so a
        # follow-up can build on them.
        self.artifacts = ArtifactStore(self.memory)
        self.language = SautiLanguageDetector()
        self.planner = Planner(self.settings, self.llm, self.registry.names())
        self.used_offline_fallback = False

    # ------------------------------------------------------------------
    async def handle(
        self,
        message: str,
        language_hint: str = "auto",
        conversation_history: Optional[list[dict]] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        use_tools: bool = True,
        mode: str = "internet",
        user_location: Optional[str] = None,
    ) -> SautiTurn:
        """Handle one user message end to end.

        Args:
            message: The user's message.
            language_hint: "auto" or an explicit language code.
            conversation_history: Prior turns as [{"role","content"}].
            user_id: Optional user scoping for memory.
            conversation_id: Optional conversation id.
            use_tools: Set False to force a direct answer.
            mode: Sidebar mode. Biases routing, never locks it.
            user_location: Optional explicit location for this turn.

        Returns:
            A SautiTurn. Failures are reported in the reply, never hidden.
        """
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        logger.info("REQUEST RECEIVED id=%s chars=%d", request_id, len(message))

        # --- 0. intent routing ------------------------------------------
        # Runs before the engine so the model is only offered the tools this
        # kind of question can need. That is both cheaper and more accurate:
        # eleven always-on tool schemas cost input tokens on every request,
        # and the provider meters exactly that.
        from .intent_router import IntentRouter, allowed_tools, normalise_mode

        active_mode = normalise_mode(mode)
        routing = await IntentRouter(self.settings).route(
            message, mode=active_mode, user_location=user_location, request_id=request_id
        )
        # Artifacts are recalled after routing, so the tool set is decided
        # below, once we know whether earlier material is in play.
        _scoped = list(allowed_tools(routing.intent)) if use_tools else []

        # --- 1. language -------------------------------------------------
        analysis = self.language.analyze(message)
        detected = analysis.language
        logger.info(
            "LANGUAGE DETECTED: %s confidence=%.2f mixed=%s requested=%s",
            detected,
            analysis.confidence,
            analysis.is_mixed,
            analysis.requested_language,
        )

        if language_hint and language_hint != "auto":
            reply_language = language_hint
        elif analysis.requested_language:
            reply_language = analysis.requested_language
        else:
            reply_language = detected

        # A stored language preference applies unless the user overrides it
        # in this message or the caller pinned a language.
        if language_hint in (None, "", "auto") and not analysis.requested_language:
            try:
                stored = await asyncio.to_thread(self.memory.get_preference, "language")
            except Exception:  # noqa: BLE001
                stored = None
            if stored:
                from .language import language_from_name

                preferred = language_from_name(stored)
                if preferred:
                    reply_language = preferred
                    logger.info("LANGUAGE PREFERENCE APPLIED: %s", preferred)

        # --- 2. memory ---------------------------------------------------
        memory_lines: list[str] = []
        try:
            memory_lines = await asyncio.to_thread(
                self.memory.recall_as_prompt_lines, message, user_id
            )
        except Exception as exc:  # noqa: BLE001 - memory must never break a turn
            logger.warning("Memory recall failed: %s", exc)
        if memory_lines:
            logger.info("MEMORY RECALLED items=%d", len(memory_lines))

        # Previously generated study material or business plans. Recalled only
        # in the modes that use them, so an ordinary question does not drag
        # old revision notes into context.
        artifact_lines: list[str] = []
        if active_mode in {"education", "business"}:
            kind = "study" if active_mode == "education" else "business"
            try:
                # Newest first, then any relevance hits, de-duplicated.
                # "make them harder" lexically matches older artifacts that
                # merely mention questions, so a pure relevance search can
                # hand the model stale material when the user plainly means
                # what was generated moments ago.
                artifact_lines = await asyncio.to_thread(
                    self.artifacts.latest_for_mode, kind, user_id=user_id, limit=2
                )
                related = await asyncio.to_thread(
                    self.artifacts.recall_lines, message, user_id=user_id
                )
                for line in related:
                    if line not in artifact_lines:
                        artifact_lines.append(line)
                del artifact_lines[3:]
            except Exception as exc:  # noqa: BLE001 - never break a turn
                logger.warning("Artifact recall failed: %s", exc)
            if artifact_lines:
                logger.info(
                    "ARTIFACTS RECALLED items=%d mode=%s", len(artifact_lines), active_mode
                )

        if use_tools:
            scoped_tools = list(
                allowed_tools(routing.intent, has_artifacts=bool(artifact_lines))
            )

        cleaned = strip_language_request(message)

        # --- 3a. Groq native function-calling path -------------------------
        # When Groq is configured it decides which tools to call itself. This is
        # the preferred path: the model chooses its own tools rather than being
        # routed by a separate planner call.
        if self.llm.uses_groq:
            return await self._handle_with_groq(
                message=message,
                cleaned=cleaned,
                reply_language=reply_language,
                history=conversation_history,
                memory_lines=memory_lines,
                detected=detected,
                is_mixed=analysis.is_mixed,
                confidence=analysis.confidence,
                conversation_id=conversation_id,
                user_id=user_id,
                started=started,
                request_id=request_id,
                scoped_tools=scoped_tools,
                routing=routing,
                mode=active_mode,
                artifact_lines=artifact_lines,
            )

        # --- 3b. Gemini native function-calling path ----------------------
        # Retained as the secondary engine so an existing Gemini-only
        # deployment keeps working unchanged.
        if self.llm.uses_gemini:
            return await self._handle_with_gemini(
                message=message,
                cleaned=cleaned,
                reply_language=reply_language,
                history=conversation_history,
                memory_lines=memory_lines,
                detected=detected,
                is_mixed=analysis.is_mixed,
                confidence=analysis.confidence,
                conversation_id=conversation_id,
                user_id=user_id,
                started=started,
                request_id=request_id,
            )

        # --- 3. plan -----------------------------------------------------
        if use_tools:
            plan = await self.planner.plan(cleaned, language=detected,
                                           conversation_history=conversation_history)
        else:
            from .planner import Plan

            plan = Plan(needs_tool=False, reason="tools disabled by caller", decided_by="caller")

        logger.info(
            "PLAN decided_by=%s needs_tool=%s reason=%s",
            plan.decided_by,
            plan.needs_tool,
            plan.reason,
        )

        # --- 4. execute tools -------------------------------------------
        tool_results: list[ToolResult] = []
        used_tools: list[str] = []
        research_performed = False
        research_available = True

        if plan.needs_tool and plan.tools:
            step = 0
            while step < self.settings.agent_max_tool_steps and plan.tools:
                request = plan.tools.pop(0)
                logger.info("TOOL SELECTED: %s args=%s", request.name, request.arguments)
                result = await self.registry.execute(request.name, request.arguments)
                tool_results.append(result)
                used_tools.append(request.name)
                logger.info(
                    "TOOL %s status=%s duration_ms=%s",
                    request.name,
                    "ok" if result.ok else "failed",
                    result.duration_ms,
                )
                if request.name in {"web_search", "web_reader"}:
                    research_performed = True
                    if not result.ok:
                        research_available = False
                step += 1

        # --- 5. evidence -------------------------------------------------
        marketplace_result = next(
            (r for r in tool_results if r.tool == "search_marketplace"), None
        )
        marketplace = None
        if marketplace_result is not None:
            data = marketplace_result.data or {}
            marketplace = {
                "ok": marketplace_result.ok,
                "query": data.get("query", cleaned),
                "resultCount": data.get("resultCount", 0),
                "results": data.get("results", []),
                "filters": data.get("filters", []),
                "parsed": data.get("parsed", {}),
                "message": data.get("message"),
            }

        sources = response_mod.collect_sources(tool_results)
        activity = response_mod.activity_from_results(tool_results)
        citations = response_mod.citations_for(tool_results)

        # --- 6. final response ------------------------------------------
        system_prompt = build_system_prompt(
            language=reply_language,
            memories=memory_lines,
            tool_names=self.registry.names(),
            settings=self.settings,
        )

        evidence_blocks = [summarise_tool_result(r) for r in tool_results]
        if evidence_blocks:
            evidence = "\n\n".join(evidence_blocks)
            user_prompt = (
                f"USER MESSAGE:\n{cleaned}\n\n"
                f"TOOL EVIDENCE (this is the only information you retrieved; do not "
                f"invent anything beyond it):\n{evidence}\n\n"
                "Now answer the user. If the evidence shows failures or no results, "
                "say so plainly. Cite the sources you actually used."
            )
        else:
            user_prompt = cleaned

        reply: Optional[str] = None
        if not tool_results and self._should_refuse_without_tools(cleaned):
            reply = response_mod.unavailable_message(
                "No web research tool is available in this session.", reply_language
            )
        elif self.llm.is_live:
            try:
                reply = await self.llm.complete(
                    system_prompt, user_prompt, history=conversation_history
                )
            except LLMUnavailable as exc:
                logger.error("LLM unavailable: %s", exc)
                reply = response_mod.unavailable_message(str(exc), reply_language)

        if not reply:
            # No live model produced an answer: compose a grounded reply from
            # the evidence we actually retrieved. Never invent anything.
            marketplace = next(
                (r for r in tool_results if r.tool == "search_marketplace"), None
            )
            news_result = next((r for r in tool_results if r.tool == "search_news"), None)
            if marketplace is not None:
                from .marketplace_reply import compose_marketplace_reply

                reply = compose_marketplace_reply(
                    marketplace, reply_language, cleaned
                )
            elif news_result is not None:
                from .marketplace_reply import compose_news_reply

                reply = compose_news_reply(news_result, reply_language)
            else:
                reply = compose_reply(cleaned, tool_results, reply_language)
            self.used_offline_fallback = True
        else:
            self.used_offline_fallback = False

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "RESPONSE GENERATED id=%s tools=%s sources=%d duration_ms=%d",
            request_id,
            used_tools,
            len(citations),
            duration,
        )

        return SautiTurn(
            message=message,
            reply=reply,
            language=detected,
            reply_language=reply_language,
            is_mixed=analysis.is_mixed,
            intent="current_information" if research_performed else "general_question",
            confidence=analysis.confidence,
            used_tools=used_tools,
            tool_results=tool_results,
            sources=sources,
            activity=activity,
            memory_used=memory_lines,
            research_performed=research_performed,
            research_available=research_available,
            marketplace=marketplace,
            offline_fallback=self.used_offline_fallback,
            # The offline path never calls an engine; the reply is composed
            # locally. `offline_fallback` already marks it, and `engine_ok`
            # is false for the same reason.
            engine_ok=not self.used_offline_fallback,
            plan_reason=plan.reason,
            decided_by=plan.decided_by,
            request_id=request_id,
            duration_ms=duration,
            conversation_id=conversation_id,
        )

    # ------------------------------------------------------------------
    async def _handle_with_groq(
        self,
        *,
        message: str,
        cleaned: str,
        reply_language: str,
        history: Optional[list[dict]],
        memory_lines: list[str],
        detected: str,
        is_mixed: bool,
        confidence: float,
        conversation_id: Optional[str],
        user_id: Optional[str],
        started: float,
        request_id: str,
        scoped_tools: list[str],
        routing,
        mode: str,
        artifact_lines: list[str],
    ) -> SautiTurn:
        """One turn driven by Groq's own function calling.

        Mirrors :meth:`_handle_with_gemini`: the model requests tools, the
        registry validates and executes them, and the final answer is grounded
        in whatever the tools actually returned.
        """
        from .groq_loop import run_groq_turn

        system_prompt = build_system_prompt(
            language=reply_language,
            memories=memory_lines,
            tool_names=self.registry.names(),
            settings=self.settings,
            mode=mode,
            artifacts=artifact_lines,
        )

        turn = await run_groq_turn(
            settings=self.settings,
            registry=self.registry,
            system=system_prompt,
            user_text=cleaned,
            history=history,
            request_id=request_id,
            tool_names=scoped_tools,
        )

        used_tools = [r.tool for r in turn.tool_results]
        marketplace_result = next(
            (r for r in turn.tool_results if r.tool == "search_marketplace"), None
        )
        marketplace = None
        if marketplace_result is not None:
            data = marketplace_result.data or {}
            marketplace = {
                "ok": marketplace_result.ok,
                "query": data.get("query", cleaned),
                "resultCount": data.get("resultCount", 0),
                "results": data.get("results", []),
                "filters": data.get("filters", []),
                "parsed": data.get("parsed", {}),
                "message": data.get("message"),
            }

        research_tools = {"web_search", "web_reader"}
        research_performed = bool(research_tools & set(used_tools))
        research_available = all(
            r.ok for r in turn.tool_results if r.tool in research_tools
        )

        reply = turn.text
        if not turn.ok:
            # The AI engine failed. Do not blame web research for that.
            # The technical detail (which can name the model, the provider
            # organisation and quota figures) is logged, never returned.
            logger.warning(
                "AI engine failure engine=groq detail=%s", turn.error_detail
            )
            reply = response_mod.engine_unavailable_message(
                turn.error, reply_language
            )
        elif not reply.strip():
            # The model returned no text (for example it only called tools and
            # ran out of rounds). Fall back to the grounded composer rather
            # than sending an empty message.
            from .marketplace_reply import compose_marketplace_reply
            from .offline import compose_reply

            news_result = next((r for r in turn.tool_results if r.tool == "search_news"), None)
            if marketplace_result is not None:
                reply = compose_marketplace_reply(marketplace_result, reply_language, cleaned)
            elif news_result is not None:
                from .marketplace_reply import compose_news_reply

                reply = compose_news_reply(news_result, reply_language)
            else:
                reply = compose_reply(cleaned, turn.tool_results, reply_language)

        # Append the numbered source list. The loop only produces it when the
        # answer survived the grounding check, so a withheld answer never gets
        # citations attached to it.
        if turn.ok and turn.citations and not turn.withheld:
            reply = f"{reply}\n\n{turn.citations}".strip()

        logger.info(
            "rid=%s groq_turn model=%s tools=%s forced=%s query=%r verified=%s "
            "withheld=%s reply_chars=%d",
            request_id, turn.model, used_tools, turn.forced_search,
            turn.search_query, turn.verified, turn.withheld, len(reply or ""),
        )

        from .cards import cards_from_results

        cards = cards_from_results(turn.tool_results)

        # Keep generated material so a follow-up ("make it harder", "more
        # questions") builds on it instead of regenerating from scratch.
        for tool_result in turn.tool_results:
            self.artifacts.record_from_result(
                tool_result,
                user_id=user_id,
                conversation_id=conversation_id,
                language=reply_language,
            )

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "GROQ TURN id=%s model=%s tools=%s ok=%s cards=%d duration_ms=%d",
            request_id,
            turn.model,
            used_tools,
            turn.ok,
            len(cards),
            duration,
        )

        return SautiTurn(
            message=message,
            reply=reply,
            language=detected,
            reply_language=reply_language,
            is_mixed=is_mixed,
            intent="current_information" if research_performed or marketplace else "general_question",
            confidence=confidence,
            used_tools=used_tools,
            tool_results=turn.tool_results,
            sources=response_mod.collect_sources(turn.tool_results),
            activity=response_mod.activity_from_results(turn.tool_results),
            memory_used=memory_lines,
            research_performed=research_performed,
            research_available=research_available,
            marketplace=marketplace,
            offline_fallback=False,
            decided_by="groq",
            # `turn.ok` is the provider's own verdict. False means the reply
            # below is the canned unavailable message, not a model answer.
            engine_ok=turn.ok,
            engine_error="" if turn.ok else (turn.error or ""),
            plan_reason="groq native function calling",
            request_id=request_id,
            duration_ms=duration,
            conversation_id=conversation_id,
            routed_intent=routing.intent.value,
            intent_confidence=routing.confidence,
            cards=cards,
            mode=mode,
        )

    # ------------------------------------------------------------------
    async def _handle_with_gemini(
        self,
        *,
        message: str,
        cleaned: str,
        reply_language: str,
        history: Optional[list[dict]],
        memory_lines: list[str],
        detected: str,
        is_mixed: bool,
        confidence: float,
        conversation_id: Optional[str],
        user_id: Optional[str],
        started: float,
        request_id: str,
    ) -> SautiTurn:
        """One turn driven by Gemini's own function calling."""
        from .gemini_loop import run_gemini_turn

        system_prompt = build_system_prompt(
            language=reply_language,
            memories=memory_lines,
            tool_names=self.registry.names(),
        )

        turn = await run_gemini_turn(
            settings=self.settings,
            registry=self.registry,
            system=system_prompt,
            user_text=cleaned,
            history=history,
        )

        used_tools = [r.tool for r in turn.tool_results]
        marketplace_result = next(
            (r for r in turn.tool_results if r.tool == "search_marketplace"), None
        )
        marketplace = None
        if marketplace_result is not None:
            data = marketplace_result.data or {}
            marketplace = {
                "ok": marketplace_result.ok,
                "query": data.get("query", cleaned),
                "resultCount": data.get("resultCount", 0),
                "results": data.get("results", []),
                "filters": data.get("filters", []),
                "parsed": data.get("parsed", {}),
                "message": data.get("message"),
            }

        research_tools = {"web_search", "web_reader"}
        research_performed = bool(research_tools & set(used_tools))
        research_available = all(
            r.ok for r in turn.tool_results if r.tool in research_tools
        )

        reply = turn.text
        if not turn.ok:
            # The AI engine failed. Do not blame web research for that.
            # The technical detail (which can name the model, the provider
            # organisation and quota figures) is logged, never returned.
            logger.warning(
                "AI engine failure engine=gemini detail=%s", turn.error_detail
            )
            reply = response_mod.engine_unavailable_message(
                turn.error, reply_language
            )
        elif not reply.strip():
            # The model returned no text (for example it only called tools and
            # ran out of rounds). Fall back to the grounded composer rather
            # than sending an empty message.
            from .marketplace_reply import compose_marketplace_reply
            from .offline import compose_reply

            news_result = next((r for r in turn.tool_results if r.tool == "search_news"), None)
            if marketplace_result is not None:
                reply = compose_marketplace_reply(marketplace_result, reply_language, cleaned)
            elif news_result is not None:
                from .marketplace_reply import compose_news_reply

                reply = compose_news_reply(news_result, reply_language)
            else:
                reply = compose_reply(cleaned, turn.tool_results, reply_language)

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "GEMINI TURN id=%s tools=%s ok=%s duration_ms=%s",
            request_id,
            used_tools,
            turn.ok,
            duration,
        )

        return SautiTurn(
            message=message,
            reply=reply,
            language=detected,
            reply_language=reply_language,
            is_mixed=is_mixed,
            intent="current_information" if research_performed or marketplace else "general_question",
            confidence=confidence,
            used_tools=used_tools,
            tool_results=turn.tool_results,
            sources=response_mod.collect_sources(turn.tool_results),
            activity=response_mod.activity_from_results(turn.tool_results),
            memory_used=memory_lines,
            research_performed=research_performed,
            research_available=research_available,
            marketplace=marketplace,
            offline_fallback=False,
            decided_by="gemini",
            engine_ok=turn.ok,
            engine_error="" if turn.ok else (turn.error or ""),
            plan_reason="gemini native function calling",
            request_id=request_id,
            duration_ms=duration,
            conversation_id=conversation_id,
        )

    def _should_refuse_without_tools(self, message: str) -> bool:
        """True when a time-sensitive question cannot be answered safely."""
        from ..marketplace.query import is_discovery_request
        from ..services.research.pipeline import needs_research

        if is_discovery_request(message):
            # The marketplace tool can answer this; it is not a web question.
            return False

        required, _ = needs_research(message)
        if not required:
            return False
        # Only refuse when the model cannot be trusted to be current on its own.
        return not self.llm.is_live
