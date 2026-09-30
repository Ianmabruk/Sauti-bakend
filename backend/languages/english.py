"""English language module for SautiPay.

Contains vocabulary, phrases, intents, translations, variations,
code-switching examples, common expressions, and domain terminology.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VocabularyEntry:
    """A single vocabulary entry."""

    word: str
    translation: str = ""
    part_of_speech: str = ""
    domain: str = ""
    examples: list = field(default_factory=list)


@dataclass
class Phrase:
    """A common phrase or expression."""

    phrase: str
    meaning: str = ""
    intent: str = ""
    variations: list = field(default_factory=list)
    code_switching_examples: list = field(default_factory=list)


class EnglishLanguageModule:
    """English language module for SautiPay."""

    CODE = "en"
    NAME = "English"

    def __init__(self):
        self.vocabulary: list[VocabularyEntry] = []
        self.phrases: list[Phrase] = []
        self._load_data()

    def _load_data(self):
        """Load English language data."""
        # Greetings
        self.phrases.extend([
            Phrase(
                phrase="hello",
                intent="greeting",
                variations=["hi", "hey", "good morning", "good afternoon"],
            ),
            Phrase(
                phrase="how are you",
                intent="greeting",
                variations=["how do you do", "what's up"],
            ),
        ])

        # Help phrases
        self.phrases.extend([
            Phrase(
                phrase="help me",
                intent="help",
                variations=["I need help", "can you help", "assist me"],
            ),
            Phrase(
                phrase="what can you do",
                intent="help",
                variations=["tell me about yourself", "what are your capabilities"],
            ),
        ])

        # Domain vocabulary
        self.vocabulary.extend([
            VocabularyEntry(
                word="government",
                translation="government",
                part_of_speech="noun",
                domain="civic",
                examples=["government services", "government office"],
            ),
            VocabularyEntry(
                word="agriculture",
                translation="agriculture",
                part_of_speech="noun",
                domain="agriculture",
                examples=["agriculture department", "agriculture officer"],
            ),
            VocabularyEntry(
                word="market",
                translation="market",
                part_of_speech="noun",
                domain="commerce",
                examples=["market price", "market day"],
            ),
        ])

    def get_phrases_for_intent(self, intent: str) -> list[Phrase]:
        """Get all phrases associated with a given intent."""
        return [p for p in self.phrases if p.intent == intent]

    def get_vocabulary_for_domain(self, domain: str) -> list[VocabularyEntry]:
        """Get all vocabulary entries for a given domain."""
        return [v for v in self.vocabulary if v.domain == domain]