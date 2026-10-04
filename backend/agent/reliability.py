"""Answer-reliability helpers: forced search, query rewriting, verification.

These three fixes attack the same failure: a confident-sounding model answers
a question about the real world from memory and gets it wrong.

- :func:`needs_forced_search` decides, before the model is called, whether a
  question depends on changing facts. When it does, the tool call is
  *required* rather than optional, so the model cannot politely skip it.
- :func:`rewrite_query` turns whatever the model proposed into a search
  phrase worth submitting, adding the user's city and the current year when
  they are relevant.
- :func:`verify_answer` asks a cheap model whether the finished answer is
  actually supported by the sources it was built from.

Every function here degrades rather than raises. A missing fast model, a
timeout or a malformed reply must never turn into a broken turn: the caller
falls back to the un-rewritten query and, for verification, to accepting the
answer it has.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from .prompts import today_in

logger = logging.getLogger(__name__)

#: Words that mark a question as depending on the real world right now.
#:
#: Compiled once at import: this runs on every single turn. The list is
#: deliberately English-only for the decision itself, because the check is a
#: safety net rather than the primary router; the model handles the rest,
#: and matching across languages would cost more than it saves.
FORCE_SEARCH_PATTERNS = re.compile(
    r"\b(weather|temperature|rain|forecast|news|price|prices|stock|bitcoin|"
    r"who is|where is|when did|how much|latest|current|today|now|"
    r"score|match|election|president|ceo|released|launched|"
    r"rate|rates|exchange|market|open|closed|available|availability|"
    r"inflation|interest|currency|flight|traffic)\b",
    re.IGNORECASE,
)

#: Questions that must never trigger a search even if they match the list
#: above, because they are stable knowledge and a search would only add noise.
#:
#: Deliberately does NOT include "what is" / "what are". Those phrasings are
#: used for live questions too — "what is the current price of maize" — and
#: treating them as stable vetoed a genuine search. The live list below is
#: specific enough on its own that no broad override is needed.
_NO_SEARCH_PATTERNS = re.compile(
    r"\b(define|definition of|meaning of|explain|how does|how do|why does|"
    r"example of|difference between|synonym|antonym|spelled|pronounce)\b",
    re.IGNORECASE,
)

#: Terms that on their own make a question about the live world, regardless
#: of how the rest of the sentence is phrased.
_ALWAYS_CURRENT = re.compile(
    r"\b(weather|forecast|temperature|rain|news|latest|right now|"
    r"today|tonight|current|currently|today's|this (week|month|year)|"
    r"price|prices|stock|bitcoin|exchange rate|exchange rates|inflation|"
    r"interest rate|market|open|closed|available|availability|"
    r"kwa sasa|leo|habari|bei|soko)\b",
    re.IGNORECASE,
)


def needs_forced_search(text: str) -> bool:
    """Decide whether a question must be grounded in a live tool result.

    The rule is deliberately biased towards searching. A wasted search costs
    a second; an invented price or forecast costs the user's trust.

    Args:
        text: The user's message.

    Returns:
        True when a tool call should be required rather than optional.
    """
    if not text or not text.strip():
        return False

    # An explicit request to define or explain something is stable knowledge,
    # even when the topic also happens to be time-sensitive-sounding
    # ("define inflation"). This is checked first because it is the one case
    # where the user's own wording settles the question.
    if _NO_SEARCH_PATTERNS.search(text):
        return False

    # A decisive live term settles it. "What is the current price of maize"
    # starts with a stable-sounding stem but is plainly a live question.
    if _ALWAYS_CURRENT.search(text):
        return True

    return bool(FORCE_SEARCH_PATTERNS.search(text))


def build_rewrite_prompt(
    raw_query: str,
    *,
    city: str,
    country: str,
    timezone_name: str,
    question: str = "",
) -> str:
    """Build the instruction for the cheap rewriting model.

    The original question is included because models routinely propose a
    uselessly short query ("Kenya" for "who won the latest Kenya election").
    Rewriting that string alone cannot recover the intent, so the question is
    the only context that makes the rewrite worthwhile.

    Args:
        raw_query: The query the main model proposed.
        city: Fallback city to add when the query is location-dependent.
        country: Country context.
        timezone_name: IANA zone, used to resolve the current year/date.
        question: The user's original message, for intent.

    Returns:
        A single user-turn prompt.
    """
    year = today_in(timezone_name).split()[-1]
    context = ""
    if question.strip():
        context = (
            "\nThe user's original question, which is what the query must "
            f"serve:\n{question.strip()[:400]}\n"
        )
    return (
        "Rewrite the search request below into one optimal web search query.\n"
        + context
        + "\nRules:\n"
        f"- If the request is location-dependent, include the location "
        f"('{city}', {country}) unless one is already named.\n"
        f"- If the request is time-sensitive, include the year {year}.\n"
        "- Preserve weather, forecast, temperature, rain and similar live-data intent; "
        "do not strip it down to a bare city name.\n"
        "- Keep the user's own language words if they help retrieval.\n"
        "- Do not add a place name that the question does not support.\n"
        "- Three to eight words. Not a sentence, not a question.\n"
        "- Output ONLY the rewritten query as plain text. No quotes, no "
        "explanation, no preamble, no trailing period.\n\n"
        f"Request to rewrite: {raw_query}\n\n"
        "Rewritten query:"
    )


def _normalise_weather_rewrite(question: str, rewritten: str) -> str:
    """Keep live-weather intent when a model strips the query to a bare city."""
    cleaned = (rewritten or "").strip()
    if not cleaned:
        return cleaned

    if re.search(r"\b(weather|forecast|temperature|rain|humidity|wind|meteo|hali ya hewa)\b", cleaned, re.I):
        return cleaned

    if not question or not re.search(
        r"\b(weather|forecast|temperature|rain|humidity|wind|meteo|hali ya hewa)\b",
        question,
        re.I,
    ):
        return cleaned

    location_match = re.search(
        r"\b(?:in|for|at|near)\s+([A-Za-z][A-Za-z\s'-]{1,40})(?=\s*(?:today|tomorrow|now|this week|this month|forecast|weather|report|$))",
        question,
        re.I,
    ) or re.search(
        r"\b(?:in|for|at|near)\s+([A-Za-z][A-Za-z\s'-]{1,40})$",
        question,
        re.I,
    )
    location = (location_match.group(1) if location_match else cleaned).strip(" ,.-")
    if not location:
        return cleaned

    prefix = "weather forecast" if "forecast" in question.lower() else "weather"
    return f"{prefix} in {location}"


async def rewrite_query(
    client,
    raw_query: str,
    *,
    city: str,
    country: str,
    timezone_name: str,
    question: str = "",
    max_tokens: int = 160,
) -> str:
    """Rewrite a proposed search query using the cheap model.

    Args:
        client: A Groq client-like object exposing ``complete_text``.
        raw_query: The query the main model proposed.
        city: Fallback city.
        country: Country context.
        timezone_name: IANA zone for the current year.
        question: The user's original message, for intent.
        max_tokens: Cap on the rewrite length.

    Returns:
        The rewritten query, or ``raw_query`` unchanged if anything failed.
        Never raises: a rewrite is an optimisation, not a requirement.
    """
    cleaned = (raw_query or "").strip()
    if not cleaned:
        return cleaned

    prompt = build_rewrite_prompt(
        cleaned,
        city=city,
        country=country,
        timezone_name=timezone_name,
        question=question,
    )
    try:
        result = await client.complete_text(
            prompt,
            temperature=0.0,
            max_tokens=max_tokens,
            model=getattr(getattr(client, "settings", None), "groq_fast_model", None),
        )
    except Exception as exc:  # noqa: BLE001 - a rewrite must never break a turn
        logger.info("query rewrite failed, using raw query: %s", type(exc).__name__)
        return cleaned

    rewritten = (result or "").strip().strip('"').strip()
    # A model that rambles must not be allowed to become the search query.
    if not rewritten or len(rewritten) > 200 or "\n" in rewritten:
        return cleaned
    return _normalise_weather_rewrite(question, rewritten)


#: Domain suffixes reserved by RFC 2606 / RFC 6761 that can never resolve to
#: a real, publicly reachable site. A "source" on one of these is definitionally
#: a fixture, not a finding, so an answer built on it must not be presented as
#: verified. This matters because the offline demo provider returns confident
#: looking prices on ``example-*.test`` URLs, which a naive grounding check
#: would happily wave through.
RESERVED_SOURCE_TLDS = (".test", ".example", ".invalid", ".localhost")


def is_real_source(url: str) -> bool:
    """Whether a URL can plausibly be a real, reachable source.

    Checks the *hostname* rather than the end of the string, because a
    reserved domain is still reserved when a path follows it.

    Args:
        url: A source URL from a tool result.

    Returns:
        False for reserved-TLD domains and for anything unparseable.
    """
    from urllib.parse import urlparse

    candidate = (url or "").strip().lower()
    if not candidate.startswith(("http://", "https://")):
        return False

    host = (urlparse(candidate).hostname or "").strip(".")
    if not host:
        return False
    return not host.endswith(RESERVED_SOURCE_TLDS)


def has_real_sources(sources: Sequence[dict]) -> bool:
    """Whether at least one source is a genuinely reachable domain.

    Args:
        sources: Retrieved sources.

    Returns:
        True when at least one source is not a reserved test domain.
    """
    return any(is_real_source(str(s.get("url") or "")) for s in sources)


def build_verification_prompt(question: str, answer: str, sources: Sequence[dict]) -> str:
    """Build the instruction for the grounding check.

    Deliberately small. The checker runs on the cheap tier, which has a much
    lower token-per-minute budget than the main model, so an unbounded prompt
    here can exceed the whole minute's allowance and take the turn down with
    it. Three sources and a short excerpt are enough to decide grounding.

    Args:
        question: The user's question.
        answer: The answer the model produced.
        sources: Retrieved sources, summarised for the prompt.

    Returns:
        A single user-turn prompt ending in a YES/NO question.
    """
    lines: list[str] = []
    for index, source in enumerate(sources[:3], start=1):
        title = str(source.get("title") or source.get("url") or "source")
        url = str(source.get("url") or "")
        excerpt = " ".join(str(source.get("excerpt") or "").split())[:180]
        lines.append(f"[{index}] {title} <{url}>\n    {excerpt}")

    evidence = "\n".join(lines) if lines else "(no sources were retrieved)"

    return (
        "You are checking whether an answer is supported by its sources.\n\n"
        f"QUESTION: {question[:400]}\n\n"
        f"ANSWER: {(answer or '')[:1200]}\n\n"
        f"SOURCES:\n{evidence}\n\n"
        "Rules:\n"
        "- Reply YES only if every factual claim in the answer is supported by "
        "the sources above.\n"
        "- Reply NO if the answer asserts something the sources do not "
        "support, contradicts them, or has no sources at all.\n"
        "- Ignore differences in wording and language translation.\n"
        "- Output ONLY the single word YES or NO."
    )


async def verify_answer(
    client,
    question: str,
    answer: str,
    sources: Sequence[dict],
    *,
    max_tokens: int = 8,
) -> Optional[bool]:
    """Check whether an answer is actually supported by its sources.

    Args:
        client: A Groq client-like object exposing ``complete_text``.
        question: The user's question.
        answer: The answer to check.
        sources: Retrieved sources.
        max_tokens: Cap on the reply, which is only YES or NO.

    Returns:
        True if the answer is supported, False if it is not, or None when the
        check could not run. None means "do not block the answer" and is the
        safe default: failing closed here would hide every good answer
        whenever the cheap model is unavailable.
    """
    if not (answer or "").strip():
        return False
    if not sources:
        return None
    if not has_real_sources(sources):
        # Every source is a reserved test domain, so there is nothing real to
        # check against. Returning None means "do not block", but the caller
        # uses has_real_sources separately to refuse to present these as facts.
        return None

    prompt = build_verification_prompt(question, answer, sources)
    try:
        raw = await client.complete_text(
            prompt,
            temperature=0.0,
            max_tokens=max_tokens,
            model=getattr(getattr(client, "settings", None), "groq_fast_model", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("verification skipped (%s)", type(exc).__name__)
        return None

    verdict = (raw or "").strip().upper()
    if verdict.startswith("YES"):
        return True
    if verdict.startswith("NO"):
        return False
    logger.info("verification returned an unusable reply; treating as unknown")
    return None


def format_citations(sources: Sequence[dict], limit: int = 2) -> str:
    """Render the numbered source list that follows an answer.

    Args:
        sources: Retrieved sources.
        limit: How many to include.

    Returns:
        A markdown block, or an empty string when there is nothing to cite.
    """
    picked = [s for s in sources if s.get("url")][:limit]
    if not picked:
        return ""
    lines = ["Sources:"]
    for index, source in enumerate(picked, start=1):
        title = str(source.get("title") or source.get("url") or "Source").strip()
        lines.append(f"[{index}] {title} — {source['url']}")
    return "\n".join(lines)
