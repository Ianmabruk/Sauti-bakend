"""Kiswahili language module for SautiPay.

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


class SwahiliLanguageModule:
    """Kiswahili language module for SautiPay."""

    CODE = "sw"
    NAME = "Kiswahili"

    def __init__(self):
        self.vocabulary: list[VocabularyEntry] = []
        self.phrases: list[Phrase] = []
        self._load_data()

    def _load_data(self):
        """Load Kiswahili language data."""
        # Greetings
        self.phrases.extend([
            Phrase(
                phrase="hujumba",
                intent="greeting",
                variations=["jambo", "mambo", "shikamoo", "hivyo"],
            ),
            Phrase(
                phrase="hali gani",
                intent="greeting",
                variations=["habari gani", "mzuri"],
            ),
        ])

        # Help phrases
        self.phrases.extend([
            Phrase(
                phrase="nisaidie",
                intent="help",
                variations=["ninahitaji msaada", "tunaweza kusaidia"],
                code_switching_examples=[
                    "nisaidie kuwa ninaelewa market prices",
                    "nisaidie kuelewa government services",
                ],
            ),
        ])

        # Domain vocabulary
        self.vocabulary.extend([
            VocabularyEntry(
                word="serikali",
                translation="government",
                part_of_speech="noun",
                domain="civic",
                examples=["serikali ya kenya", "ofisi ya serikali"],
            ),
            VocabularyEntry(
                word="kilimo",
                translation="agriculture",
                part_of_speech="noun",
                domain="agriculture",
                examples=["wakili wa kilimo", "shule ya kilimo"],
            ),
            VocabularyEntry(
                word="soko",
                translation="market",
                part_of_speech="noun",
                domain="commerce",
                examples=["soko la kisumu", "bei za soko"],
            ),
            VocabularyEntry(
                word="bei",
                translation="price",
                part_of_speech="noun",
                domain="commerce",
                examples=["bei ya mahindi", "bei za soko"],
            ),
        ])

    def get_phrases_for_intent(self, intent: str) -> list[Phrase]:
        """Get all phrases associated with a given intent."""
        return [p for p in self.phrases if p.intent == intent]

    def get_vocabulary_for_domain(self, domain: str) -> list[VocabularyEntry]:
        """Get all vocabulary entries for a given domain."""
        return [v for v in self.vocabulary if v.domain == domain]