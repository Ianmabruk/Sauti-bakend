"""Language processing service for SautiPay."""
from __future__ import annotations

import logging
from typing import Optional

from ..languages.detector import LanguageDetectionResult
from ..languages.registry import LanguageRegistry

logger = logging.getLogger(__name__)


class SimpleLanguageDetector:
    """Simple rule-based language detector.

    Detects primary language based on keyword matching.
    Supports English and Kiswahili.
    """

    # Language-specific keywords
    LANGUAGE_KEYWORDS = {
        "sw": [
            "hujumba", "jambo", "mambo", "shikamoo", "hivyo", "mzuri",
            "hali", "gani", "habari", "bwana", "bibi", "ndugu",
            "asante", "samahani", "ndio", "la", "hapana",
            "nisaidie", "tunaweza", "ninahitaji", "na", "wa",
            "kwa", "ya", "ni", "katika", "kwenye",
        ],
        "en": [
            "hello", "hi", "hey", "good", "morning", "afternoon",
            "evening", "how", "are", "you", "what", "when", "where",
            "who", "why", "which", "the", "and", "is", "to", "in",
        ],
    }

    def detect(self, text: str) -> LanguageDetectionResult:
        """Detect the primary language of the text.

        Args:
            text: Input text to analyze.

        Returns:
            LanguageDetectionResult with language code and confidence.
        """
        text_lower = text.lower().strip()
        scores: dict[str, int] = {}

        for lang, keywords in self.LANGUAGE_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 1
            if score > 0:
                scores[lang] = score

        if not scores:
            return LanguageDetectionResult(
                language="en",
                confidence=0.0,
            )

        best_lang = max(scores, key=lambda k: scores[k])  # type: ignore
        total_words = len(text_lower.split())
        confidence = min(0.95, 0.5 + (scores[best_lang] * 0.1))

        return LanguageDetectionResult(
            language=best_lang,
            confidence=round(confidence, 2),
        )


class LanguageService:
    """Service for language processing and detection."""

    def __init__(self):
        self.registry = LanguageRegistry()
        self.detector = SimpleLanguageDetector()

    def detect(self, text: str) -> LanguageDetectionResult:
        """Detect the language of the given text.

        Args:
            text: Input text to analyze.

        Returns:
            LanguageDetectionResult with language code and confidence.
        """
        return self.detector.detect(text)

    def get_supported_languages(self):
        """Get all supported languages.

        Returns:
            A list of language metadata.
        """
        return self.registry.get_all()

    def is_supported(self, code: str) -> bool:
        """Check if a language code is supported."""
        return self.registry.is_supported(code)

    def is_verified(self, code: str) -> bool:
        """Check if a language has verified data."""
        return self.registry.is_verified(code)
