"""Rule-based intent classifier for SautiPay.

This is a deterministic classifier using keyword matching.
It is suitable for Phase 1 and can be replaced by a model-based
classifier in a future phase without changing the application code.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .classifier import IntentClassifier, IntentResult


class RuleBasedIntentClassifier(IntentClassifier):
    """Deterministic rule-based intent classifier.

    Uses keyword matching and entity extraction to classify intents.
    This is suitable for testing and initial deployment.
    A model-based classifier can replace this without changing the interface.
    """

    # Intent definitions with keywords (English and Kiswahili)
    INTENT_KEYWORDS = {
        "greeting": [
            "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
            "hujumba", "jambo", "mambo", "shikamoo", "hivyo", "mzuri",
        ],
        "general_question": [
            "what", "how", "why", "when", "where", "who", "which",
            "nini", "jinsi", "kwa nini", "lini", "wapi", "nani",
        ],
        "news": [
            "news", "habari", "magazeti", "current events", "latest",
        ],
        "government_information": [
            "government", "serikali", "county", "ura", "citizen",
        ],
        "civic_information": [
            "civic", "citizen", "raia", "haki", "rights", "id", "passport",
        ],
        "agriculture": [
            "agriculture", "kilimo", "crop", "maize", "mahindi", "farming",
            "farm", "soil", "harvest",
        ],
        "market_price": [
            "price", "bei", "market", "soko", "cost", "how much",
        ],
        "product_search": [
            "product", "buy", "purchase", "shop", "item", "search",
            "bidhaa", "ununuzi",
        ],
        "order": [
            "order", "place order", "order", "buy", "purchase",
        ],
        "payment": [
            "payment", "malipo", "pesa", "money", "pay", "send money",
        ],
        "help": [
            "help", "msaada", "saidia", "assist", "support", "what can you do",
        ],
    }

    # Entity extraction patterns
    ENTITY_PATTERNS = {
        "commodity": [
            r"(maize|corn|mahindi)",
            r"(wheat|ngano)",
            r"(rice|mchele)",
            r"(beans|maharagwe)",
            r"(potatoes|viazi)",
            r"(tomatoes|nyanya)",
        ],
        "location": [
            r"(Nairobi|Mombasa|Kisumu|Nakuru|Eldoret|Thika|Kisii|Meru|Nyeri|Garissa|Machakos|Kitale|Kakamega|Narok|Kabarnet|Lodwar|Marsabit|Mandera|Wajir|Isiolo|Embu|Marsabit|Tana River|Laikipia|Baringo|Kajiado|Turkana|Samburu|West Pokot|Trans Nzoia|Bungoma|Busia|Siaya|Kisumu|Homa Bay|Migori|Kisii|Nyamira|Bomet|Nandi|Kericho|Baringo|Laikipia|Nyeri|Kirinyaga|Murang'a|Kiambu|Nairobi|Kajiado|Machakos| Makueni|Kitui|Taita Taveta|Kilifi|Kwale|Mombasa|Tana River|Lamu)",
        ],
        "amount": [
            r"(\d+)\s*(kg|kilograms|bags|sacks|tonnes|ton)",
        ],
    }

    def __init__(self) -> None:
        # Build reverse mapping for faster lookup
        self._keyword_to_intent: dict[str, str] = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            for keyword in keywords:
                self._keyword_to_intent[keyword.lower()] = intent

    def classify(
        self,
        text: str,
        language: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> IntentResult:
        """Classify intent using keyword matching.

        Args:
            text: Input text to classify.
            language: Optional language code.
            context: Optional conversation context.

        Returns:
            IntentResult with intent, confidence, and entities.
        """
        text_lower = text.lower().strip()
        entities: dict[str, str] = {}

        # Extract entities
        for entity_name, patterns in self.ENTITY_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    entities[entity_name] = match.group(1)
                    break

        # Count keyword matches per intent
        intent_scores: dict[str, int] = {}
        for keyword, intent in self._keyword_to_intent.items():
            if keyword in text_lower:
                intent_scores[intent] = intent_scores.get(intent, 0) + 1

        if not intent_scores:
            return IntentResult(
                intent="unknown",
                confidence=0.0,
                entities=entities,
                language=language,
            )

        # Sort by score descending, then by keyword length (prefer longer matches)
        # For equal scores, prefer more specific intents
        intent_priority = [
            "market_price", "product_search", "order", "payment",
            "agriculture", "government_information", "civic_information",
            "news", "help", "greeting", "general_question"
        ]

        def sort_key(item):
            intent, score = item
            priority = intent_priority.index(intent) if intent in intent_priority else 99
            return (-score, priority)

        sorted_intents = sorted(intent_scores.items(), key=sort_key)
        best_intent, score = sorted_intents[0]

        # Calculate confidence based on score and text length
        # More matches and shorter text = higher confidence
        confidence = min(0.95, 0.5 + (score * 0.15) + (0.1 if len(text) < 50 else 0))

        return IntentResult(
            intent=best_intent,
            confidence=round(confidence, 2),
            entities=entities,
            language=language,
        )
