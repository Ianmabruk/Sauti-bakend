"""Language detection interface for SautiPay.

The system should detect language and confidence, and eventually support
code-switching (mixed-language messages common in Kenya).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LanguageDetectionResult:
    """Result of language detection."""

    language: str  # ISO 639-1 code (e.g., "en", "sw")
    confidence: float  # 0.0 to 1.0
    alternatives: list = None  # Optional alternative languages with scores

    def __post_init__(self):
        if self.alternatives is None:
            self.alternatives = []


class LanguageDetector(ABC):
    """Abstract interface for language detection."""

    @abstractmethod
    def detect(self, text: str) -> LanguageDetectionResult:
        """Detect the primary language of the given text.

        Args:
            text: Input text to analyze.

        Returns:
            LanguageDetectionResult with language code and confidence.
        """
        ...

    def detect_with_alternatives(self, text: str, top_n: int = 3) -> LanguageDetectionResult:
        """Detect language with alternative possibilities.

        Args:
            text: Input text to analyze.
            top_n: Number of alternative languages to return.

        Returns:
            LanguageDetectionResult with alternatives.
        """
        result = self.detect(text)
        return result