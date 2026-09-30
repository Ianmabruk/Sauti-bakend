"""The research pipeline.

Deliberately split into distinct stages so each can be tested, replaced or
skipped on its own:

    plan -> SEARCH -> rank/select -> READ pages -> extract -> compare -> report

Answer *generation* is not here: the pipeline returns structured evidence and
the agent turns that into prose.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from ...config.settings import Settings
from ..citations import Source, build_citations, dedupe_sources, source_quality
from .providers import SearchUnavailable, build_search_provider
from .reader import ReadError, WebReader

logger = logging.getLogger(__name__)

#: Words that signal a question depends on information that changes over time.
CURRENT_MARKERS = {
    "current", "currently", "today", "now", "latest", "recent", "recently",
    "this week", "this month", "this year", "right now", "up to date",
    "kwa sasa", "sasa", "leo", "hivi karibuni", "wa zamani",
    "actuel", "actuelle", "actuels", "aujourd'hui", "maintenant", "récemment",
    "aujourd", "ce mois", "en ce moment",
}

#: Topics that are almost always time-sensitive.
CURRENT_DOMAINS = {
    "price": ("price", "prices", "cost", "costs", "bei", "beiya", "prix", "tarif",
              "tariffs", "soko", "market", "rate", "rates", "exchange", "forecast"),
    "news": ("news", "habari", "actualites", "actualités", "headlines", "today's", "leo"),
    "weather": ("weather", "hali ya hewa", "meteo", "météo", "forecast", "rain", "raining"),
    "vehicle": ("for sale", "price of", "cost of", "dealer", "listing", "listings",
                "vyandani", "in stock", "availability", "available"),
    "legal": ("law", "laws", "act", "regulation", "sheria", "loi", "règlement"),
    "gov": ("government", "county", "serikali", "ura", "ministry", "ministère"),
}

#: Stable-knowledge topics that should NOT trigger a search.
STABLE_MARKERS = {
    "definition", "explain", "what is", "what are", "how does", "how do",
    "difference between", "meaning of", "example of", "tutorial",
    "definition de", "qu'est-ce", "comment fonctionne", "explique",
    "maana ya", "nini maana", "elewa",
}


@dataclass
class ResearchResult:
    """Structured outcome of a research run."""

    query: str
    search_query: str
    ok: bool
    answer_available: bool = False
    sources: list[Source] = field(default_factory=list)
    extractions: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[str] = None
    duration_ms: int = 0
    searched_count: int = 0
    read_count: int = 0
    activity: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "search_query": self.search_query,
            "ok": self.ok,
            "answer_available": self.answer_available,
            "provider": self.provider,
            "duration_ms": self.duration_ms,
            "searched_count": self.searched_count,
            "read_count": self.read_count,
            "sources": build_citations(self.sources),
            "extractions": self.extractions,
            "observations": self.observations,
            "conflicts": self.conflicts,
            "error": self.error,
        }


# --------------------------------------------------------------------------
# Stage 1: decide whether research is needed at all
# --------------------------------------------------------------------------

def needs_research(text: str) -> tuple[bool, list[str]]:
    """Heuristic check for time-sensitive questions.

    This is a safety net. The model planner is the primary decision maker;
    this catches cases where no usable model is configured.

    Returns:
        (needs, reasons)
    """
    lowered = text.lower()
    reasons: list[str] = []

    if any(marker in lowered for marker in CURRENT_MARKERS):
        reasons.append("temporal marker")

    for domain, markers in CURRENT_DOMAINS.items():
        if any(marker in lowered for marker in markers):
            reasons.append(f"current domain: {domain}")

    # A stable-knowledge request overrides a weak time signal.
    stable_hits = sum(1 for marker in STABLE_MARKERS if marker in lowered)
    if stable_hits and not any(r.startswith("current domain") for r in reasons):
        return False, []

    return bool(reasons), reasons


# --------------------------------------------------------------------------
# Stage 2: build a focused search query
# --------------------------------------------------------------------------

_NOISE = re.compile(
    r"^\s*(please|sauti|sautipay|hey|hi|hello|can you|could you|tell me|"
    r"nisaidie|tafuta|onjeleza|sasa|kwa sasa|actuellement|actuel)\b[,\s]*",
    re.I,
)


def build_search_query(text: str, language: str = "en") -> str:
    """Turn a user message into a compact web search phrase.

    Leading address/politeness noise is stripped repeatedly so phrases like
    "Sauti, please check the price" reduce to "check the price".
    """
    cleaned = text.strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _NOISE.sub("", cleaned).strip()
        cleaned = re.sub(r"^[,;:.\-\s]+", "", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > 220:
        cleaned = cleaned[:220].rsplit(" ", 1)[0]
    return cleaned.rstrip("?.!") or text.strip()[:220]


# --------------------------------------------------------------------------
# Stage 3: the pipeline
# --------------------------------------------------------------------------

class ResearchPipeline:
    """Runs search -> read -> extract for a query."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(
        self,
        query: str,
        language: str = "en",
        read_pages: bool = True,
        max_results: Optional[int] = None,
    ) -> ResearchResult:
        """Execute the full research pipeline.

        Args:
            query: The user's question.
            language: Detected language code.
            read_pages: Whether to fetch page bodies.
            max_results: Override the configured result cap.

        Returns:
            A ResearchResult. On failure ``ok`` is False and ``error`` is set;
            no partial or invented data is produced.
        """
        started = time.perf_counter()
        limit = max_results or self.settings.search_max_results
        search_query = build_search_query(query, language)
        activity: list[dict] = []

        def step(stage: str, detail: str = "") -> None:
            activity.append({"stage": stage, "detail": detail})
            logger.info("RESEARCH %s %s", stage.upper(), detail)

        # --- provider selection ---
        try:
            provider = build_search_provider(self.settings)
        except SearchUnavailable as exc:
            step("unavailable", str(exc))
            return ResearchResult(
                query=query,
                search_query=search_query,
                ok=False,
                error=str(exc),
                activity=activity,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        # --- SEARCH ---
        step("searching", f"query={search_query!r} provider={provider.name}")
        try:
            results = await provider.search(search_query, max_results=limit)
        except SearchUnavailable as exc:
            step("search_failed", str(exc))
            return ResearchResult(
                query=query,
                search_query=search_query,
                ok=False,
                error=str(exc),
                provider=provider.name,
                activity=activity,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            step("search_failed", f"{type(exc).__name__}: {exc}")
            return ResearchResult(
                query=query,
                search_query=search_query,
                ok=False,
                error=f"Search request failed: {exc}",
                provider=provider.name,
                activity=activity,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        if not results:
            step("no_results", "provider returned zero results")
            return ResearchResult(
                query=query,
                search_query=search_query,
                ok=True,
                answer_available=False,
                error="No results were returned for this query.",
                provider=provider.name,
                activity=activity,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        step("search_complete", f"results={len(results)}")

        # --- rank and select ---
        deduped = dedupe_sources(results)
        for source in deduped:
            combined = 0.6 * source_quality(source.url, source.title) + 0.4 * source.relevance
            source.relevance = round(combined, 3)
        deduped.sort(key=lambda s: s.relevance, reverse=True)
        step("sources_selected", f"count={len(deduped)}")

        # --- READ (concurrent) ---
        read_sources: list[Source] = []
        read_errors: list[str] = []
        if read_pages:
            to_read = deduped[: self.settings.search_max_pages_to_read]
            step("reading", f"pages={len(to_read)}")
            reader = WebReader(self.settings)
            read_sources, read_errors = await reader.read_many(
                [s.url for s in to_read], query=search_query
            )
            step(
                "read_complete",
                f"ok={len(read_sources)} failed={len(read_errors)}",
            )

        final_sources = read_sources or deduped
        extractions = [
            {
                "url": s.url,
                "title": s.title,
                "domain": s.domain,
                "published_at": s.published_at,
                "source_type": s.source_type,
                "relevance": s.relevance,
                "excerpt": (s.content or s.snippet)[:1200],
            }
            for s in final_sources
        ]

        observations, conflicts = _extract_observations(final_sources, search_query)
        step("extracted", f"observations={len(observations)} conflicts={len(conflicts)}")

        return ResearchResult(
            query=query,
            search_query=search_query,
            ok=True,
            answer_available=bool(final_sources),
            sources=final_sources,
            extractions=extractions,
            observations=observations,
            conflicts=conflicts,
            provider=provider.name,
            searched_count=len(results),
            read_count=len(read_sources),
            activity=activity,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


# --------------------------------------------------------------------------
# Stage 4: light structured extraction
# --------------------------------------------------------------------------

_PRICE_RE = re.compile(
    r"(?P<currency>ksh|kes|ksh\.?|usd|eur|gbp|frf|\$|€|£)\s*"
    r"(?P<amount>\d{1,3}(?:[,  ]\d{3})*(?:\.\d+)?)\s*"
    r"(?P<per>per\s+(?:kg|kilogram|quintal|tonne|ton|bag|90kg|50kg|sack|unit|litre|liter)?)?",
    re.I,
)
_DATE_IN_TEXT = re.compile(
    r"\b(\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{4}|\d{4}-\d{2}-\d{2})\b", re.I
)


def _extract_observations(sources: list[Source], query: str) -> tuple[list[dict], list[dict]]:
    """Pull prices/dates out of source text to support grounded reporting.

    This is intentionally conservative: it only reports figures that literally
    appear in the retrieved text, and keeps the surrounding context so the
    model does not report a number out of context.
    """
    observations: list[dict] = []
    conflicts: list[dict] = []

    for source in sources:
        haystack = f"{source.snippet}\n{source.content[:6000]}"
        for match in _PRICE_RE.finditer(haystack):
            context = haystack[max(0, match.start() - 90) : match.end() + 90]
            observations.append(
                {
                    "raw": match.group(0).strip(),
                    "currency": (match.group("currency") or "").upper(),
                    "amount": match.group("amount"),
                    "unit": (match.group("per") or "").strip() or None,
                    "url": source.url,
                    "domain": source.domain,
                    "published_at": source.published_at,
                    "context": " ".join(context.split()),
                }
            )

    # Group by product-ish token to detect disagreement between sources.
    products = _product_keys(query)
    for product in products:
        by_domain: dict[str, list[dict]] = {}
        for obs in observations:
            if product in obs["context"].lower():
                by_domain.setdefault(obs["domain"], []).append(obs)
        if len(by_domain) >= 2:
            values = {
                domain: o[0]["amount"] for domain, o in by_domain.items()
            }
            distinct = {v.replace(",", "").replace(" ", "") for v in values.values()}
            if len(distinct) > 1:
                conflicts.append(
                    {
                        "topic": product,
                        "values_by_source": values,
                        "note": "Sources report different figures; present the range and explain.",
                    }
                )
    return observations, conflicts


def _product_keys(query: str) -> list[str]:
    keys = {
        "maize", "corn", "mahindi", "maïs", "mais", "wheat", "ngano", "blé",
        "rice", "mchele", "riz", "beans", "maharagwe", "haricots",
        "potato", "viazi", "pomme de terre", "tomato", "nyanya", "tomate",
        "mercedes", "gclass", "gle", "toyota", "nissan", "honda",
        "kes", "usd", "exchange", "forecast",
    }
    lowered = query.lower()
    return sorted(key for key in keys if key in lowered)


async def run_research(
    query: str, settings: Settings, language: str = "en", read_pages: bool = True
) -> ResearchResult:
    """Convenience wrapper around ResearchPipeline."""
    return await ResearchPipeline(settings).run(query, language=language, read_pages=read_pages)
