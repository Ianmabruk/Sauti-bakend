"""Response assembly for SAUTI.

Turns model output plus tool evidence into a reply the user can trust, and
produces the short activity list the frontend shows while SAUTI works.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..services.citations import Source, build_citations
from ..tools.base import ToolResult

logger = logging.getLogger(__name__)

#: Activity strings are intentionally short and factual. They describe what
#: SAUTI is doing, never its internal reasoning.
#:
#: Every value here describes work that has *already finished*. Nothing is
#: emitted for "answering now", because this list is only ever sent alongside a
#: completed answer: a trailing "Preparing answer…" would read as a stuck
#: progress indicator next to text that had already been delivered. The live
#: "Working on it…" state is the frontend's job, driven by its own sending flag.
ACTIVITY_SEARCHING = "Searching current sources…"
ACTIVITY_READING = "Reading sources…"
ACTIVITY_COMPARING = "Comparing information…"
ACTIVITY_MEMORY = "Recalling what I know about you…"
ACTIVITY_UNAVAILABLE = "Live web research is unavailable right now."


def activity_from_results(results: list[ToolResult]) -> list[str]:
    """Build the user-visible activity list from executed tool results."""
    steps: list[str] = []
    for result in results:
        if not result.ok:
            if result.tool in {"web_search", "web_reader"}:
                steps.append(ACTIVITY_UNAVAILABLE)
            continue

        if result.tool == "web_search":
            payload = result.data or {}
            sources = payload.get("sources", [])
            if not sources:
                steps.append("Search finished but returned no usable sources.")
                continue
            steps.append(ACTIVITY_SEARCHING)
            if result.metadata.get("read_count"):
                steps.append(ACTIVITY_READING)
            if payload.get("conflicts"):
                steps.append(ACTIVITY_COMPARING)

        elif result.tool == "web_reader":
            steps.append(ACTIVITY_READING)

    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for step in steps:
        if step not in seen:
            seen.add(step)
            ordered.append(step)
    return ordered


def collect_sources(results: list[ToolResult]) -> list[Source]:
    """Gather every source produced by successful tools, de-duplicated.

    A tool may report the same URL through both `result.sources` and the
    serialised payload, so URLs are collapsed here.
    """
    from ..services.citations import dedupe_sources

    sources: list[Source] = []
    for result in results:
        if not result.ok:
            continue
        sources.extend(result.sources or [])
        if result.tool == "web_search":
            for raw in (result.data or {}).get("sources", []) or []:
                if isinstance(raw, dict) and raw.get("url"):
                    sources.append(
                        Source(
                            title=raw.get("title", ""),
                            url=raw["url"],
                            domain=raw.get("domain", ""),
                            published_at=raw.get("date"),
                            relevance=float(raw.get("relevance") or 0.0),
                            source_type=raw.get("source_type", "unknown"),
                        )
                    )
    return dedupe_sources(sources)


def citations_for(results: list[ToolResult], limit: int = 6) -> list[dict]:
    return build_citations(collect_sources(results), limit=limit)


def unavailable_message(error: Optional[str], language: str = "en") -> str:
    """A truthful, language-appropriate failure message."""
    detail = (error or "").strip()
    if language == "sw":
        base = (
            "Samahani, utafutaji wa taarifa za moja kwa moja (web search) "
            "haupatikani kwa sasa hivyo siwezi kuthibitisha bei au taarifa za sasa."
        )
    elif language == "fr":
        base = (
            "Désolé, la recherche web en temps réel est indisponible pour le "
            "moment, je ne peux donc pas confirmer les prix ou informations actuelles."
        )
    else:
        base = (
            "I'm sorry — live web research is currently unavailable, so I can't "
            "verify current prices or information. I won't guess at a number."
        )
    if detail:
        return f"{base}\n\nTechnical detail: {detail}"
    return base


#: Shown when the AI engine itself is unreachable or rejected the request.
#: Deliberately distinct from the web-research message: blaming the wrong
#: subsystem would be its own kind of dishonesty.
ENGINE_UNAVAILABLE = {
    "en": (
        "Sauti's AI service isn't available right now, so I can't answer this "
        "properly. I won't guess. Please try again in a moment."
    ),
    "sw": (
        "Huduma ya AI ya Sauti haipatikani kwa sasa, kwa hivyo siweza kujibu "
        "hili vizuri. Sitakadiria. Tafadhali jaribu tena baadaye."
    ),
    "fr": (
        "Le service d'IA de Sauti est indisponible pour le moment ; je ne peux "
        "donc pas répondre correctement. Je ne vais pas inventer. Réessayez."
    ),
}


def engine_unavailable_message(detail: Optional[str], language: str = "en") -> str:
    """A truthful message for an AI-engine failure.

    Args:
        detail: Optional *user-safe* explanation. The raw provider error is
            deliberately not accepted here: it can contain the model name,
            the provider's organisation id, quota figures and service tier,
            none of which belong in a user-facing reply. Log the technical
            detail server-side instead.
        language: Reply language code.

    Returns:
        A localized, honest apology that makes no attempt to guess.
    """
    base = ENGINE_UNAVAILABLE.get(language, ENGINE_UNAVAILABLE["en"])
    # Only an explicit, already-sanitised short reason is ever appended.
    safe = (detail or "").strip()
    if safe and len(safe) <= 120 and "http" not in safe.lower() and "org_" not in safe.lower():
        return f"{base}\n\n({safe})"
    return base
