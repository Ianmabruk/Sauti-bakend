"""Intent detection interface for SautiPay.

The intent engine should return structured information such as:
{
    "intent": "market_price",
    "confidence": 0.91,
    "entities": {
        "commodity": "maize",
        "location": "Kisumu"
    }
}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class IntentResult:
    """Result of intent detection."""

    intent: str
    confidence: float  # 0.0 to 1.0
    entities: dict = field(default_factory=dict)
    language: Optional[str] = None
    alternatives: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "entities": self.entities,
            "language": self.language,
            "alternatives": self.alternatives,
        }


class IntentClassifier(ABC):
    """Abstract interface for intent classification."""

    @abstractmethod
    def classify(
        self,
        text: str,
        language: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> IntentResult:
        """Classify the intent of the given text.

        Args:
            text: Input text to classify.
            language: Optional language code.
            context: Optional conversation context.

        Returns:
            IntentResult with intent, confidence, and entities.
        """
        ...

    def classify_with_alternatives(
        self,
        text: str,
        language: Optional[str] = None,
        context: Optional[dict] = None,
        top_n: int = 3,
    ) -> IntentResult:
        """Classify intent with alternative possibilities.

        Args:
            text: Input text to classify.
            language: Optional language code.
            context: Optional conversation context.
            top_n: Number of alternative intents to return.

        Returns:
            IntentResult with alternatives.
        """
        result = self.classify(text, language, context)
        return result