"""Intent engine for SautiPay.

Provides a unified interface for intent detection.
"""
from __future__ import annotations

import logging
from typing import Optional

from .classifier import IntentClassifier, IntentResult
from .rule_based import RuleBasedIntentClassifier

logger = logging.getLogger(__name__)


class IntentEngine:
    """Main intent engine that delegates to a classifier.

    This is the primary interface for intent detection in SautiPay.
    """

    def __init__(self, classifier: Optional[IntentClassifier] = None):
        """Initialize the intent engine.

        Args:
            classifier: Optional custom classifier. Defaults to RuleBasedIntentClassifier.
        """
        self.classifier = classifier or RuleBasedIntentClassifier()

    def detect(
        self,
        text: str,
        language: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> IntentResult:
        """Detect the intent of the given text.

        Args:
            text: Input text to analyze.
            language: Optional language code.
            context: Optional conversation context.

        Returns:
            IntentResult with intent, confidence, and entities.
        """
        return self.classifier.classify(text, language, context)

    def detect_with_alternatives(
        self,
        text: str,
        language: Optional[str] = None,
        context: Optional[dict] = None,
        top_n: int = 3,
    ) -> IntentResult:
        """Detect intent with alternative possibilities.

        Args:
            text: Input text to analyze.
            language: Optional language code.
            context: Optional conversation context.
            top_n: Number of alternative intents to return.

        Returns:
            IntentResult with alternatives.
        """
        return self.classifier.classify_with_alternatives(text, language, context, top_n)