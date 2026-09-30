"""Tests for SAUTI language detection and the planner."""
from __future__ import annotations

import asyncio

import pytest

from backend.agent.language import (
    SautiLanguageDetector,
    detect_requested_language,
    strip_language_request,
)
from backend.agent.planner import Planner
from backend.config.settings import Settings
from backend.services.research.pipeline import build_search_query, needs_research


@pytest.fixture
def detector():
    return SautiLanguageDetector()


class TestLanguageDetection:
    """English, Kiswahili, French and code-switched input."""

    def test_english(self, detector):
        result = detector.analyze("What is the current price of maize in Kenya?")
        assert result.language == "en"
        assert result.confidence > 0

    def test_swahili(self, detector):
        result = detector.analyze("Bei ya mahindi Kenya kwa sasa ni kiasi gani?")
        assert result.language == "sw"

    def test_french(self, detector):
        result = detector.analyze("Quel est le prix actuel du maïs au Kenya ?")
        assert result.language == "fr"

    def test_french_with_accents(self, detector):
        assert detector.analyze("Explique la photosynthèse.").language == "fr"

    def test_mixed_code_switch_is_swahili_dominant(self, detector):
        result = detector.analyze("Sauti tafuta current price ya maize Kenya.")
        assert result.language == "sw"
        assert result.is_mixed is True

    def test_mixed_contains_english_marker(self, detector):
        result = detector.analyze("Sauti tafuta current price ya maize Kenya.")
        assert "en" in result.mixed_languages

    def test_stable_english_question(self, detector):
        assert detector.analyze("What is Python?").language == "en"

    def test_empty_falls_back_to_english(self, detector):
        result = detector.analyze("")
        assert result.language == "en"
        assert result.confidence == 0.0

    def test_repeated_discovery(self, detector):
        for _ in range(3):
            assert detector.detect("Quel temps fait-il aujourd'hui ?") == "fr"


class TestLanguageRequests:
    """'Answer in X' overrides the reply language."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("What is the maize price? Answer in Kiswahili.", "sw"),
            ("Quel est le prix du maïs? Réponds en anglais.", "en"),
            ("Tell me about Kenya. Réponds en français.", "fr"),
            ("Bei ya mahindi ni nini? Eleza kwa Kiingereza.", "en"),
        ],
    )
    def test_requested_language(self, text, expected):
        assert detect_requested_language(text) == expected

    def test_no_request_returns_none(self):
        assert detect_requested_language("What is the price of maize?") is None

    def test_strip_removes_instruction(self):
        cleaned = strip_language_request("current maize price Kenya. Answer in Kiswahili.")
        assert "kiswahili" not in cleaned.lower()
        assert "maize" in cleaned.lower()


class TestResearchDetection:
    """Deciding when external information is required."""

    @pytest.mark.parametrize(
        "text",
        [
            "What is the current price of maize in Kenya?",
            "Bei ya mahindi Kenya kwa sasa ni kiasi gani?",
            "Quel est le prix actuel du maïs au Kenya ?",
            "What happened in Kenya today?",
            "What is the USD/KES exchange rate?",
            "How much is a Mercedes GLE in Kenya right now?",
            "What's the current weather in Nairobi?",
        ],
    )
    def test_current_information_triggers_research(self, text):
        needs, reasons = needs_research(text)
        assert needs is True
        assert reasons

    @pytest.mark.parametrize(
        "text",
        [
            "What is photosynthesis?",
            "What is Python?",
            "What is a database?",
            "Explain the difference between TCP and UDP.",
        ],
    )
    def test_stable_knowledge_does_not_trigger_research(self, text):
        needs, _ = needs_research(text)
        assert needs is False

    def test_search_query_is_compact(self):
        query = build_search_query("Sauti, please check the current price of maize in Kenya?")
        assert "sauti" not in query.lower()
        assert "please" not in query.lower()
        assert "maize" in query.lower()

    def test_search_query_truncates_long_input(self):
        query = build_search_query("maize " * 200)
        assert len(query) <= 220


def _run(coro):
    """Run a coroutine in a fresh loop (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


class TestPlanner:
    """The planner routes current-information questions to web_search."""

    @pytest.fixture
    def planner(self):
        return Planner(Settings(search_provider="stub"))

    def test_marketplace_price_uses_platform_database(self, planner):
        """A price Sauti itself sells must come from Sauti's database."""
        plan = _run(planner.plan("What is the current price of maize in Kenya?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name == "search_marketplace"
        assert "maize" in plan.tools[0].arguments["query"].lower()

    def test_swahili_price_uses_platform_database(self, planner):
        plan = _run(planner.plan("Bei ya mahindi Kenya kwa sasa ni kiasi gani?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name == "search_marketplace"

    def test_french_price_uses_platform_database(self, planner):
        plan = _run(planner.plan("Quel est le prix actuel du maïs au Kenya ?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name == "search_marketplace"

    def test_vehicle_request_uses_marketplace(self, planner):
        plan = _run(planner.plan("How much is a Mercedes GLE in Kenya right now?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name == "search_marketplace"

    def test_news_request_uses_own_newsroom(self, planner):
        plan = _run(planner.plan("What are the latest major news stories in Kenya?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name == "search_news"

    def test_external_information_uses_web_search(self, planner):
        """Weather and exchange rates are not in the marketplace."""
        plan = _run(planner.plan("What is the USD KES exchange rate today?"))
        assert plan.needs_tool is True
        assert plan.tools[0].name in {"web_search", "search_marketplace"}

    def test_stable_question_needs_no_tool(self, planner):
        plan = _run(planner.plan("What is Python?"))
        assert plan.needs_tool is False
        assert plan.tools == []

    def test_heuristic_is_used_without_llm_key(self, planner):
        plan = _run(planner.plan("What is the current price of maize?"))
        assert plan.decided_by == "heuristic"

    def test_language_instruction_does_not_pollute_query(self, planner):
        plan = _run(planner.plan(
            "What is the current maize price? Answer in Kiswahili."
        ))
        assert plan.needs_tool is True
        assert "kiswahili" not in plan.tools[0].arguments["query"].lower()
