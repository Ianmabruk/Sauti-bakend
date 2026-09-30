"""Multilingual language detection for SAUTI.

Handles English, Kiswahili and French, including code-switched messages such
as "Sauti tafuta current price ya maize Kenya" where the dominant language is
not the language of most characters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

SUPPORTED_LANGUAGES = ("en", "sw", "fr")

LANGUAGE_NAMES = {
    "en": "English",
    "sw": "Kiswahili",
    "fr": "French",
}

# Function words and high-frequency markers. Weighted so that a single strong
# marker (e.g. a French negation) is not outweighed by many weak ones.
_STOPWORDS: dict[str, set[str]] = {
    "en": {
        "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on",
        "for", "and", "or", "but", "what", "how", "why", "when", "where",
        "who", "which", "can", "could", "should", "would", "will", "do",
        "does", "did", "have", "has", "had", "please", "tell", "me", "about",
        "current", "price", "today", "now", "explain", "give", "need", "want",
        "much", "cost", "this", "that", "it", "you", "your", "my",
    },
    "sw": {
        "ni", "na", "ya", "wa", "kwa", "za", "la", "katika", "ni", "ni",
        "hii", "hilo", "huyu", "kwamba", "kwenda", "kuwa", "kutoka", "nini",
        "wapi", "nani", "vipi", "kubwa", "bei", "sasa", "zaidi", "siku",
        "habari", "tafuta", "nisaidie", "jibu", "asante", "karibu", "hapana",
        "ndiyo", "jambo", "hujumba", "mambo", "shikamoo", "sauti", "mahindi",
        "kenya", "kilimo", "soko", "serikali", "habari", "mzuri", "kwamba",
    },
    "fr": {
        "le", "la", "les", "un", "une", "des", "est", "sont", "et", "ou",
        "mais", "de", "du", "des", "à", "au", "aux", "en", "dans", "sur",
        "pour", "par", "avec", "sans", "ce", "cette", "quel", "quelle",
        "quels", "quelles", "quoi", "comment", "pourquoi", "quand", "où",
        "qui", "combien", "est-ce", "je", "tu", "vous", "nous", "ils", "elles",
        "bonjour", "salut", "merci", "s'il", "actuel", "actuelle", "prix",
        "aujourd'hui", "maintenant", "combien", "réponds", "réponse", "explique",
    },
}

# Diacritic-bearing characters are a strong French signal.
_FRENCH_MARKERS = re.compile(r"[àâçéèêëîïôûùüÿœæ]", re.IGNORECASE)
_ASCII_FRENCH_HINTS = re.compile(
    r"\b(bonjour|salut|merci|combien|actuel|actuelle|aujourd'hui|aujourd|"
    r"reponds|réponds|pourquoi|comment|quelle|quel|les|des|pour|avec)\b",
    re.IGNORECASE,
)

# Tokens that stay English when embedded in Swahili or French text. Used to
# detect code-switching rather than to mis-attribute the whole sentence.
_CODE_SWITCH_MARKERS = re.compile(
    r"\b(price|prices|current|latest|news|search|find|check|today|now|"
    r"maize|corn|wheat|kenya|nairobi|exchange|rate|what|how|is|of|in|for|"
    r"and|weather|forecast|sauti|sautipay)\b",
    re.IGNORECASE,
)

_LANGUAGE_REQUEST_PATTERNS = (
    # English phrasing, any target language
    (
        re.compile(
            r"\b(?:answer|reply|respond|write|speak|talk|explain)\s+"
            r"(?:me\s+)?(?:in|using)\s+(english|swahili|kiswahili|french|français|francais)\b",
            re.I,
        ),
        None,  # resolved from the captured language below
    ),
    # Kiswahili phrasing
    (re.compile(r"\b(?:jibu|eleza|andika)\s+kwa\s+(kiingereza|kiswahili|français|francais)\b", re.I), None),
    # French phrasing
    (
        re.compile(
            r"\b(?:r[eé]ponds|r[eé]pondre|explique|[eé]cris|parle)\s+en\s+"
            r"(anglais|swahili|kiswahili|français|francais)\b",
            re.I,
        ),
        None,
    ),
)

#: Map a language word used in a request onto a supported code.
_REQUEST_NAME_TO_CODE = {
    "english": "en",
    "anglais": "en",
    "kiingereza": "en",
    "swahili": "sw",
    "kiswahili": "sw",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
}

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass
class LanguageAnalysis:
    """Outcome of analysing a message."""

    language: str
    confidence: float
    is_mixed: bool = False
    mixed_languages: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    requested_language: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "confidence": round(self.confidence, 3),
            "is_mixed": self.is_mixed,
            "mixed_languages": self.mixed_languages,
            "requested_language": self.requested_language,
        }


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def detect_requested_language(text: str) -> Optional[str]:
    """Detect an explicit "answer me in <language>" instruction.

    Handles the request in any supported language, e.g. "Answer in Kiswahili",
    "Jibu kwa Kiingereza", "Réponds en français".

    Args:
        text: The user's message.

    Returns:
        A supported language code, or None when no explicit request is present.
    """
    for pattern, _ in _LANGUAGE_REQUEST_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        captured = match.group(1) if match.groups() else None
        if captured:
            code = _REQUEST_NAME_TO_CODE.get(captured.lower())
            if code:
                return code
    return None


def strip_language_request(text: str) -> str:
    """Remove a trailing/embedded language instruction from a message.

    Keeps search queries clean, e.g. "price of maize in Kenya. answer in
    Kiswahili" becomes "price of maize in Kenya".
    """
    cleaned = text
    for pattern, _ in _LANGUAGE_REQUEST_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def language_from_name(text: str) -> Optional[str]:
    """Map a language name or code mentioned in text to a supported code.

    Used to honour a stored preference such as "My preferred language is
    Kiswahili" without needing a database join.
    """
    if not text:
        return None
    lowered = text.lower()
    direct = {code: code for code in SUPPORTED_LANGUAGES}
    for code, names in {
        "en": ("english", "kiingereza", "anglais", "inglés", "anglais"),
        "sw": ("kiswahili", "swahili", "kiswahilini"),
        "fr": ("french", "français", "francais", "francais"),
    }.items():
        for name in names:
            if name in lowered:
                return code
    for code in direct:
        if re.search(rf"\b{code}\b", lowered):
            return code
    return None


class SautiLanguageDetector:
    """Deterministic detector for en / sw / fr with code-switch awareness."""

    def analyze(self, text: str) -> LanguageAnalysis:
        """Analyse a message and return the dominant plus detected languages.

        Args:
            text: Raw user message.

        Returns:
            A LanguageAnalysis. Never raises; falls back to English.
        """
        if not text or not text.strip():
            return LanguageAnalysis(language="en", confidence=0.0)

        lowered = text.lower()
        tokens = _tokens(text)
        token_set = set(tokens)

        scores: dict[str, float] = {code: 0.0 for code in SUPPORTED_LANGUAGES}

        # Stopword overlap
        for code, words in _STOPWORDS.items():
            hits = len(token_set & words)
            scores[code] += hits * 1.0

        # Relative frequency: languages with many unknown tokens score lower
        total_tokens = max(len(token_set), 1)
        for code, words in _STOPWORDS.items():
            scores[code] -= (total_tokens - len(token_set & words)) * 0.02

        # French-specific signals
        if _FRENCH_MARKERS.search(text):
            scores["fr"] += 3.0
        if _ASCII_FRENCH_HINTS.search(text):
            scores["fr"] += 1.5

        # Kiswahili agglutination hint: "nini/kwa nini" style question words
        if re.search(r"\b(nini|kwa nini|gani|zipi|wapi|nani|lini)\b", lowered):
            scores["sw"] += 2.0

        detected = [code for code, score in scores.items() if score > 0]
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        best_code, best_score = ordered[0]
        runner_code, runner_score = ordered[1] if len(ordered) > 1 else ("en", 0.0)

        # English is the fallback: do not let English win on a tie against an
        # empty score, and never return a language with no evidence.
        if best_score <= 0:
            best_code, best_score = "en", 0.0

        total = sum(max(v, 0.0) for v in scores.values()) or 1.0
        confidence = max(0.0, min(0.99, max(best_score, 0.0) / total))

        # Code-switching: another language is present with meaningful evidence
        # and there are English marker tokens embedded in the text.
        has_code_switch_tokens = bool(_CODE_SWITCH_MARKERS.search(text))
        mixed: list[str] = [best_code]
        if runner_code != best_code and runner_score >= max(1.5, best_score * 0.25):
            mixed.append(runner_code)
        if has_code_switch_tokens and "en" not in mixed and best_code in {"sw", "fr"}:
            mixed.append("en")
            confidence = min(confidence, 0.85)
        is_mixed = len(mixed) > 1

        return LanguageAnalysis(
            language=best_code,
            confidence=confidence,
            is_mixed=is_mixed,
            mixed_languages=mixed,
            scores=scores,
            requested_language=detect_requested_language(text),
        )

    def detect(self, text: str) -> str:
        """Return just the dominant language code."""
        return self.analyze(text).language
