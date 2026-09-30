"""Groq tool-calling loop.

When Groq is configured, the model decides which tools to call using the
OpenAI-compatible ``tools`` API. The loop is:

    model -> tool call -> registry validation -> permission check
          -> execution -> structured result -> back to the model
          -> final answer

The model never executes code. Every call goes through the registry, exactly
like the Gemini and OpenAI paths, so tool safety is enforced in one place
regardless of which engine produced the request.

Three reliability guards sit around that loop, all defined in
:mod:`jarvis.backend.agent.reliability`:

1. a live-data question forces a tool call instead of trusting memory;
2. the query is rewritten before it is sent to the search backend;
3. the finished answer is checked against its own sources.

Every step is logged with the turn's ``request_id`` so a bad answer can be
traced end to end.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config.settings import Settings
from ..integrations.groq import GroqClient, GroqError, build_function_tools
from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry
from .reliability import (
    format_citations,
    has_real_sources,
    needs_forced_search,
    rewrite_query,
    verify_answer,
)

logger = logging.getLogger(__name__)

#: The exact sentence returned when grounding cannot be established. The
#: instruction document specifies this wording, so it is a constant rather
#: than something assembled.
UNVERIFIED_MESSAGE = "I couldn't verify that online, sir."

#: Cap on tool-result text handed back to the model. Page text is bulky and
#: providers meter input tokens per minute, so an unbounded payload can exceed
#: a whole request budget. Excerpts are what answers are built from anyway.
_MAX_TOOL_PAYLOAD_CHARS = 8000

#: Ceiling on *total* tool-result characters in the conversation, not per
#: message. Two searches in one turn otherwise stack into a payload that can
#: exceed the tier's per-request token allowance and fail the turn with a 413.
_MAX_TOTAL_TOOL_CHARS = 9000

#: At most one search per turn. A second search almost always re-asks the same
#: question with a slightly different phrase, and it doubles both the token
#: spend and the latency for no extra grounding.
_MAX_SEARCH_CALLS = 1

#: Tools that are expensive enough that calling one twice in a turn is pure
#: waste. The generative tools in particular get re-requested by the model after
#: the first result arrives, and a second identical generation costs a full
#: turn of the token-metered budget for content the model already has.
_ONE_CALL_TOOLS = frozenset({
    "place_search", "source_links", "study_generator", "business_advisor",
    "search_marketplace", "get_vendor_profile", "get_product_details",
})

#: Search tools whose query argument is rewritten before execution.
_REWRITABLE_TOOLS = {"web_search"}


@dataclass
class GroqTurn:
    """Outcome of one Groq agent turn."""

    ok: bool
    text: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Optional[str] = None
    error_detail: Optional[str] = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rounds: int = 0
    #: True when a live-data question forced a tool call.
    forced_search: bool = False
    #: The query actually sent to the search backend, after rewriting.
    search_query: str = ""
    #: True / False when the grounding check ran; None when it could not.
    verified: Optional[bool] = None
    #: Numbered source block appended to the answer, when present.
    citations: str = ""
    #: True when the answer was withheld because it failed verification.
    withheld: bool = False


def _tool_result_message(results: list[ToolResult], budget: int = _MAX_TOOL_PAYLOAD_CHARS) -> str:
    """Shape executed tool results for the model's next turn.

    The model is told explicitly that this is authoritative and that it must
    not invent anything absent from it, which is what stops a failed or empty
    search from turning into a fabricated vendor, price or article.

    Args:
        results: The tools that just ran.
        budget: Maximum characters of payload to include.

    Returns:
        A user-turn message carrying the results.
    """
    payload = []
    for result in results:
        entry: dict = {"tool": result.tool, "ok": result.ok}
        if result.ok:
            entry["result"] = result.data
        else:
            entry["error"] = result.error
        payload.append(entry)
    return (
        "TOOL RESULTS (authoritative, retrieved just now). Answer using only "
        "these. Never invent a vendor, price, phone number, availability or "
        "news fact that is not present here. If a tool failed or returned "
        "nothing, say plainly that you found no matching information.\n\n"
        + json.dumps(payload, default=str)[:budget]
    )


#: Appended once, on retry, when the model declines to call a tool even though
#: the request required it. A blind identical retry re-samples the same
#: behaviour and burns a whole turn of the rate-limited budget; an explicit
#: instruction is far more likely to succeed on the first extra call.
_FORCED_TOOL_NUDGE = (
    "IMPORTANT: this question depends on information that changes over time. "
    "You must answer it by calling one of the tools available to you. Do not "
    "answer from memory. If you genuinely cannot find the information, call a "
    "tool anyway and say the results were empty."
)


def _is_missing_forced_tool(exc: GroqError) -> bool:
    """Whether an error means the model refused the forced tool call.

    Args:
        exc: The provider error raised for this round.

    Returns:
        True when the request required a tool and none was produced.
    """
    if exc.status != 400:
        return False
    message = (exc.message or "").lower()
    return "tool choice is required" in message or "did not call a tool" in message


def _collect_sources(results: list[ToolResult]) -> list[dict]:
    """Flatten sources out of every successful tool result.

    Args:
        results: Executed tool results.

    Returns:
        Source dicts with at least ``url``, ``title`` and ``excerpt``.
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for result in results:
        if not result.ok or not isinstance(result.data, dict):
            continue
        for source in result.data.get("sources") or []:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "url": url,
                    "title": str(source.get("title") or url),
                    "excerpt": str(source.get("excerpt") or source.get("snippet") or ""),
                }
            )
    return sources


def _summarise_tool_result(result: ToolResult) -> str:
    """A short, log-safe description of one tool result.

    Deliberately reports counts and not bodies: a search excerpt can be
    thousands of characters and would make the log unreadable.
    """
    if not result.ok:
        return f"{result.tool}=failed({result.error})"
    if isinstance(result.data, dict):
        counts = {
            key: len(value)
            for key, value in result.data.items()
            if isinstance(value, list)
        }
        detail = ",".join(f"{k}={v}" for k, v in counts.items()) or "ok"
        return f"{result.tool}=ok({detail})"
    return f"{result.tool}=ok"


async def run_groq_turn(
    settings: Settings,
    registry: ToolRegistry,
    system: str,
    user_text: str,
    history: Optional[list[dict]] = None,
    request_id: str = "-",
    tool_names: Optional[list[str]] = None,
) -> GroqTurn:
    """Run a full Groq turn, executing any tools it requests.

    Args:
        settings: Runtime settings, including the server-side key.
        registry: The tool registry. All execution goes through it.
        system: System instruction.
        user_text: The user's message.
        history: Prior turns as [{"role", "content"}].
        request_id: Correlation id stamped on every log line for this turn.
        tool_names: Optional whitelist of tool names to offer. Used by the
            intent router to keep the prompt small, because every extra tool
            schema is paid for on every request by a token-metered provider.

    Returns:
        A GroqTurn. Failures are reported, never hidden.
    """
    client = GroqClient(settings)

    available = registry.describe()
    if tool_names is not None:
        allowed = set(tool_names)
        available = [t for t in available if t.get("name") in allowed]
    tool_schemas = build_function_tools(available)

    # Decide, before the model is called, whether this question depends on
    # facts that change. A "required" tool choice then stops the model from
    # answering it out of memory.
    forced = bool(
        settings.sauti_force_search and tool_schemas and needs_forced_search(user_text)
    )
    logger.info(
        "rid=%s force_search=%s chars=%d tools=%d",
        request_id, forced, len(user_text or ""), len(tool_schemas),
    )
    tool_choice = "required" if forced else "auto"

    # Build the conversation once. The user turn must appear exactly once, at
    # its natural position: appending it again on every tool round would both
    # scramble the order and duplicate the prompt, which matters because Groq's
    # free tier is capped on input tokens per minute.
    messages: list[dict] = []
    for turn in history or []:
        role = "assistant" if turn.get("role") in {"assistant", "model"} else "user"
        text = (turn.get("content") or "").strip()
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_text})

    collected: list[ToolResult] = []
    prompt_tokens = 0
    completion_tokens = 0
    model_used = ""
    search_query = ""
    system_messages: list[dict] = [{"role": "system", "content": system}] if system else []

    for round_index in range(settings.groq_max_tool_rounds):
        nudged = False
        while True:
            try:
                body = await client.chat(
                    system_messages + messages,
                    tools=tool_schemas,
                    tool_choice=tool_choice,
                )
                result = GroqClient._parse(body, model_used or settings.groq_model)
                break
            except asyncio.CancelledError:
                raise
            except GroqError as exc:
                # The model ignored a required tool call. One retry with an
                # explicit instruction is far more likely to work than
                # re-sending the identical request.
                if _is_missing_forced_tool(exc) and not nudged:
                    nudged = True
                    logger.info("rid=%s model skipped forced tool; nudging", request_id)
                    system_messages = system_messages + [
                        {"role": "system", "content": _FORCED_TOOL_NUDGE}
                    ]
                    continue
                logger.warning(
                    "rid=%s groq_failed round=%d status=%s detail=%s",
                    request_id, round_index, exc.status, exc.message[:160],
                )
                # If we were grounding a live question and the engine gave up,
                # the truthful answer is that it could not be verified — not
                # that the whole service is down.
                if forced and collected:
                    logger.warning(
                        "rid=%s returning unverified after engine failure", request_id
                    )
                    return GroqTurn(
                        ok=True,
                        text=UNVERIFIED_MESSAGE,
                        tool_results=collected,
                        model=model_used,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        rounds=round_index,
                        forced_search=forced,
                        search_query=search_query,
                        verified=False,
                        withheld=True,
                    )
                return GroqTurn(
                    ok=False,
                    tool_results=collected,
                    error=exc.user_message,
                    error_detail=exc.message,
                    rounds=round_index,
                    forced_search=forced,
                    search_query=search_query,
                )

        model_used = result.model
        prompt_tokens += result.prompt_tokens or 0
        completion_tokens += result.completion_tokens or 0

        # Once the model has actually used a tool, the forcing has done its
        # job and further rounds are optional again.
        if collected:
            tool_choice = "auto"

        if not result.wants_tools:
            answer = result.text
            logger.info(
                "rid=%s answer chars=%d preview=%r",
                request_id, len(answer or ""), (answer or "")[:200],
            )

            verified, withheld = await _verify_turn(
                client, settings, request_id, user_text, answer, collected
            )
            citations = ""
            if verified is not False:
                citations = format_citations(
                    _collect_sources(collected), limit=settings.sauti_citation_count
                )
            if withheld:
                answer = UNVERIFIED_MESSAGE

            return GroqTurn(
                ok=True,
                text=answer,
                tool_results=collected,
                model=model_used,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                rounds=round_index,
                forced_search=forced,
                search_query=search_query,
                verified=verified,
                citations=citations,
                withheld=withheld,
            )

        # Execute every requested call through the registry.
        responses: list[ToolResult] = []
        search_calls = sum(1 for r in collected if r.tool in _REWRITABLE_TOOLS)
        # How many times each capped tool has already run this turn.
        used_counts: dict[str, int] = {}
        for previous in collected:
            used_counts[previous.tool] = used_counts.get(previous.tool, 0) + 1

        for call in result.function_calls:
            if call.name in _REWRITABLE_TOOLS and search_calls >= _MAX_SEARCH_CALLS:
                logger.info(
                    "rid=%s skipping repeat search %s; one search per turn",
                    request_id, call.name,
                )
                continue
            if call.name in _ONE_CALL_TOOLS and used_counts.get(call.name, 0) >= 1:
                logger.info(
                    "rid=%s skipping repeat %s; already ran this turn",
                    request_id, call.name,
                )
                continue

            arguments = dict(call.arguments or {})

            # Rewrite the search phrase before it leaves the building. A model
            # that asks for "bitcoin" gets a query that can actually retrieve
            # something useful for this user, today.
            if call.name in _REWRITABLE_TOOLS and settings.sauti_query_rewriting:
                raw_query = str(arguments.get("query") or "").strip()
                if raw_query:
                    rewritten = await rewrite_query(
                        client,
                        raw_query,
                        city=settings.user_city,
                        country=settings.user_country,
                        timezone_name=settings.user_timezone,
                        question=user_text,
                    )
                    arguments["query"] = rewritten
                    search_query = rewritten
                    logger.info(
                        "rid=%s rewrite raw=%r -> final=%r", request_id, raw_query, rewritten
                    )

            logger.info(
                "rid=%s tool_call name=%s args=%s", request_id, call.name, arguments
            )
            tool_result = await registry.execute(call.name, arguments)
            if call.name in _REWRITABLE_TOOLS:
                search_calls += 1
            used_counts[call.name] = used_counts.get(call.name, 0) + 1
            responses.append(tool_result)
            logger.info(
                "rid=%s tool_result %s duration_ms=%s",
                request_id, _summarise_tool_result(tool_result), tool_result.duration_ms,
            )

        collected.extend(responses)

        # Feed the conversation forward in the order the API expects: the
        # model's own tool request, then one result block. The budget is shared
        # across the whole turn, because providers meter the entire request
        # rather than each appended message.
        if result.text:
            messages.append({"role": "assistant", "content": result.text})
        spent = sum(len(str(m.get("content") or "")) for m in messages)
        remaining = max(500, _MAX_TOTAL_TOOL_CHARS - spent)
        messages.append(
            {"role": "user", "content": _tool_result_message(responses, remaining)}
        )

    # Ran out of rounds: return whatever the model last said, honestly.
    return GroqTurn(
        ok=True,
        text="",
        tool_results=collected,
        model=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        rounds=settings.groq_max_tool_rounds,
        forced_search=forced,
        search_query=search_query,
    )


async def _verify_turn(
    client: GroqClient,
    settings: Settings,
    request_id: str,
    question: str,
    answer: str,
    results: list[ToolResult],
) -> tuple[Optional[bool], bool]:
    """Check the answer against its sources.

    Returns:
        ``(verdict, withheld)``. ``verdict`` is True/False when the check ran
        and None when it could not. ``withheld`` is True only when the answer
        was demonstrably unsupported and therefore replaced.
    """
    if not settings.sauti_self_verification or not results or not (answer or "").strip():
        return None, False

    sources = _collect_sources(results)
    if sources and not has_real_sources(sources):
        # Every retrieved source is a reserved test domain, which means the
        # search backend is the offline demo provider. Its "prices" are
        # fixtures. Withhold rather than present invented figures as facts.
        logger.warning(
            "rid=%s sources_are_fixtures domains=%s; withholding",
            request_id, [s.get("url") for s in sources[:3]],
        )
        return False, True

    verdict = await verify_answer(client, question, answer, sources)
    logger.info("rid=%s verified=%s sources=%d", request_id, verdict, len(sources))

    if verdict is False:
        logger.warning(
            "rid=%s grounding_failed; withholding answer sources=%d", request_id, len(sources)
        )
        return False, True
    return verdict, False
