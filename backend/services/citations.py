"""Structured source objects and citation assembly.

SAUTI must be able to tell the user where information came from, and must
never invent a URL or a publication date. Dates that cannot be determined are
recorded as None and rendered as "date unknown".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


@dataclass
class Source:
    """A single retrieved source.

    `published_at` stays None when the source does not state a date. It is
    never guessed.
    """

    title: str
    url: str
    domain: str = ""
    snippet: str = ""
    published_at: Optional[str] = None
    accessed_at: Optional[str] = None
    relevance: float = 0.0
    source_type: str = "unknown"
    content: str = ""

    def __post_init__(self) -> None:
        if not self.domain and self.url:
            self.domain = domain_of(self.url)
        if not self.accessed_at:
            self.accessed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def domain_of(url: str) -> str:
    """Extract a bare domain from a URL, or '' when it cannot be parsed."""
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


# Domains that carry more authority for the given kind of claim.
_TRUST_HINTS: dict[str, tuple[str, ...]] = {
    "official": (".gov.", ".go.ke", "europa.eu", "who.int", "worldbank.org"),
    "financial": ("centralbank.go.ke", "forex", "xe.com", "wise.com", "oanda.com"),
    "market": ("africaeis", "eis", "fe net", "feinet", "kwsb.go.ke"),
    "news": ("nation.africa", "standardmedia.co.ke", "citizen.digital", "bbc.com"),
}

# Domains that are commonly low-signal aggregators or content farms.
_LOW_QUALITY = (
    "pinterest.", "quora.com", "reddit.com", "yahoo.com", "answers.com",
    "top10", "best-", "buymeacoffee", "fandom.com",
)


def classify_source(url: str, title: str = "") -> str:
    """Classify a source so the agent can reason about its authority."""
    domain = domain_of(url).lower()
    haystack = f"{domain} {title.lower()}"

    if any(hint in haystack for hint in _TRUST_HINTS["official"]):
        return "official"
    if any(hint in domain for hint in _TRUST_HINTS["financial"]):
        return "financial"
    if any(hint in domain for hint in _TRUST_HINTS["news"]):
        return "news"
    if any(hint in domain for hint in _LOW_QUALITY):
        return "low_quality"
    return "unknown"


def source_quality(url: str, title: str = "") -> float:
    """A 0..1 authority score used to rank and filter sources."""
    kind = classify_source(url, title)
    return {
        "official": 1.0,
        "financial": 0.9,
        "market": 0.85,
        "news": 0.7,
        "unknown": 0.5,
        "low_quality": 0.2,
    }.get(kind, 0.5)


_DATE_PATTERNS = (
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), (0, 1, 2)),
    (re.compile(r"(\d{2})/(\d{2})/(\d{4})"), (2, 0, 1)),
)


def extract_published_at(text: str) -> Optional[str]:
    """Best-effort publication date extraction from page metadata.

    Returns an ISO date string, or None when no trustworthy date is present.
    Never fabricates a date.
    """
    if not text:
        return None

    candidates = [
        re.search(
            r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
            text, re.I,
        ),
        re.search(
            r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)', text, re.I
        ),
        re.search(
            r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)',
            text, re.I,
        ),
        re.search(
            r'<time[^>]+datetime=["\']([^"\']+)["\']', text, re.I
        ),
    ]
    for match in candidates:
        if not match:
            continue
        parsed = _coerce_date(match.group(1))
        if parsed:
            return parsed

    for pattern, (y, m, d) in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            year = int(match.group(y))
            month = int(match.group(m))
            day = int(match.group(d))
            if not (2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
                continue
            return datetime(year, month, day, tzinfo=timezone.utc).date().isoformat()
        except ValueError:
            continue
    return None


def _coerce_date(value: str) -> Optional[str]:
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def dedupe_sources(sources: list[Source]) -> list[Source]:
    """Remove duplicate URLs, keeping the highest-relevance copy."""
    best: dict[str, Source] = {}
    for source in sources:
        key = source.url.split("#", 1)[0].rstrip("/").lower() or source.title.lower()
        current = best.get(key)
        if current is None or source.relevance > current.relevance:
            best[key] = source
    return list(best.values())


def build_citations(sources: list[Source], limit: int = 6) -> list[dict]:
    """Produce the citation list returned to the frontend.

    Only sources with a real, verified URL are ever included.
    """
    usable = [s for s in sources if s.url and s.url.startswith(("http://", "https://"))]
    ranked = sorted(
        usable,
        key=lambda s: (source_quality(s.url, s.title), s.relevance),
        reverse=True,
    )[:limit]
    return [
        {
            "title": s.title or s.domain or "Untitled source",
            "url": s.url,
            "domain": s.domain,
            "date": s.published_at,
            "accessed_at": s.accessed_at,
            "source_type": s.source_type or classify_source(s.url, s.title),
            "relevance": round(s.relevance, 3),
        }
        for s in ranked
    ]
