"""Deterministic grounded responder used when no live model is configured.

This is NOT a language model. It never invents facts. It composes an answer
strictly from evidence that was actually retrieved, and it states plainly that
it is running without a language model so the user is never misled about where
the answer came from.
"""
from __future__ import annotations

import re
from typing import Optional

from ..services.citations import build_citations
from ..tools.base import ToolResult

#: How much each language notices the absence of a model. The internal
#: configuration variable name is deliberately not shown to users.
_NO_MODEL_NOTICE = {
    "en": (
        "Note: no language model is configured on this deployment, so this is a "
        "direct summary of retrieved sources rather than a written answer."
    ),
    "sw": (
        "Kumbuka: hakuna modeli ya lugha iliyosanidiwa, hivyo huu ni muhtasari "
        "wa vyanzo vilivyopatikana, si jibu lililoundwa."
    ),
    "fr": (
        "À noter : aucun modèle de langue n'est configuré ; ceci est un résumé "
        "direct des sources consultées, et non une réponse rédigée."
    ),
}

_HEADINGS = {
    "en": {
        "found": "Based on the latest information I found:",
        "none": "I could not find usable sources for this question.",
        "failed": "I was unable to complete the web research for this question.",
        "direct": "Here is what I can say without needing external sources:",
        "figures": "Figures found in the sources:",
        "reported": "Cited below ({count} sources).",
        "caveat": (
            "These figures vary by location and seller, so treat them as a range "
            "and confirm with the source before making a decision."
        ),
    },
    "sw": {
        "found": "Kutokana na taarifa za hivi karibuni nilizipata:",
        "none": "Sikupata vyanzo vinavyotumika kwa swali hili.",
        "failed": "Sikuweza kukamilisha utafutaji wa taarifa za sawa kwa swali hili.",
        "direct": "Haya niliyoyoweza kusema bila kuhitaji taarifa za nje:",
        "figures": "Takwimu zilizopatikana kwenye vyanzo:",
        "reported": "Vimeelezwa hapa chini ({count} vyanzo).",
        "caveat": (
            "Bei hizi hutofautiana kwa eneo na muuzaji, kwa hivyo zizingatie kama "
            "masafa na uthibitishe chanzo kabla ya kufanya maamuzi."
        ),
    },
    "fr": {
        "found": "D'après les informations récentes que j'ai trouvées :",
        "none": "Je n'ai pas trouvé de sources exploitables pour cette question.",
        "failed": "Je n'ai pas pu mener la recherche web pour cette question.",
        "direct": "Voici ce que je peux dire sans sources externes :",
        "figures": "Chiffres trouvés dans les sources :",
        "reported": "Sources référencées ci-dessous ({count}).",
        "caveat": (
            "Ces chiffres varient selon le lieu et le vendeur : traitez-les comme "
            "une fourchette et confirmez auprès de la source avant de décider."
        ),
    },
}

_UNAVAILABLE = {
    "en": (
        "I'm sorry — live web research is currently unavailable, so I cannot verify "
        "current prices or information. I won't guess at a number."
    ),
    "sw": (
        "Samahani, utafutaji wa taarifa za moja kwa moja haupatikani kwa sasa, hivyo "
        "siwezi kuthibitisha bei au taarifa za sasa. Sitakadiria kubahatisha."
    ),
    "fr": (
        "Désolé, la recherche web en temps réel est indisponible pour le moment ; je "
        "ne peux donc pas confirmer les prix actuels. Je ne vais pas inventer de chiffre."
    ),
}

_PRICE_RE = re.compile(
    r"(?P<currency>ksh|kes|usd|eur|gbp|\$|€|£)\s*"
    r"(?P<amount>\d{1,3}(?:[, ]\d{3})*(?:\.\d+)?)",
    re.I,
)


def _lang(language: str) -> str:
    return language if language in _HEADINGS else "en"


def compose_reply(
    message: str,
    tool_results: list[ToolResult],
    language: str = "en",
    model_reply: Optional[str] = None,
) -> str:
    """Build an honest reply from retrieved evidence.

    Args:
        message: The user's message.
        tool_results: Results from every tool the agent ran.
        language: Reply language code.
        model_reply: Text from a live model, when one produced it.

    Returns:
        The reply text. Never contains invented facts.
    """
    lang = _lang(language)
    heads = _HEADINGS[lang]

    if not tool_results:
        return f"{heads['direct']}\n\n{message.strip()}"

    research_tools = [r for r in tool_results if r.tool in {"web_search", "web_reader"}]
    failures = [r for r in research_tools if not r.ok]

    if failures and not any(r.ok for r in research_tools):
        detail = failures[0].error or "unknown error"
        return f"{_UNAVAILABLE[lang]}\n\nTechnical detail: {detail}"

    successful = [r for r in research_tools if r.ok]
    citations = build_citations(
        [s for r in successful for s in (r.sources or [])], limit=5
    )

    if not citations:
        return f"{heads['none']}"

    lines = [heads["found"], ""]

    # Report only figures that literally appear in retrieved text.
    figures = _collect_figures(successful)
    if figures:
        lines.append(heads["figures"])
        for figure in figures[:6]:
            parts = [figure["raw"]]
            if figure.get("domain"):
                parts.append(figure["domain"])
            if figure.get("published_at"):
                parts.append(figure["published_at"])
            else:
                parts.append("date unknown")
            lines.append(f"- {' — '.join(parts)}")
        lines.append("")

    # The full citation list travels in the response's `sources` field so the
    # client can render clickable references; repeating it here would be noise.
    lines.append(heads["reported"].format(count=len(citations)) if "{count}" in heads["reported"]
                 else heads["reported"])
    lines.append("")
    lines.append(_NO_MODEL_NOTICE[lang])
    lines.append(heads["caveat"])
    return "\n".join(lines)


def _collect_figures(results: list[ToolResult]) -> list[dict]:
    """Extract price figures that actually appear in retrieved text."""
    figures: list[dict] = []
    seen: set[str] = set()

    for result in results:
        for source in result.sources or []:
            haystack = f"{source.snippet}\n{source.content or ''}"
            for match in _PRICE_RE.finditer(haystack):
                raw = " ".join(match.group(0).split())
                key = (raw, source.domain)
                if key in seen:
                    continue
                seen.add(key)
                figures.append(
                    {
                        "raw": raw,
                        "domain": source.domain,
                        "published_at": source.published_at,
                        "url": source.url,
                    }
                )
    return figures
