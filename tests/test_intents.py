"""Tests for the intent detection system."""
import pytest
from backend.services.intent import IntentEngine
from backend.services.intent.rule_based import RuleBasedIntentClassifier
from backend.services.intent.classifier import IntentResult


class TestRuleBasedIntentClassifier:
    """Tests for the rule-based intent classifier."""

    def test_classify_greeting_english(self):
        """Test classification of English greeting."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("Hello, how are you?", "en")
        assert result.intent == "greeting"
        assert result.confidence > 0

    def test_classify_greeting_swahili(self):
        """Test classification of Swahili greeting."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("Hujumba", "sw")
        assert result.intent == "greeting"

    def test_classify_market_price(self):
        """Test classification of market price query."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("What is the price of maize in Kisumu?")
        assert result.intent == "market_price"
        assert "commodity" in result.entities or "location" in result.entities

    def test_classify_agriculture(self):
        """Test classification of agriculture query."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("How do I farm maize?")
        assert result.intent == "agriculture"

    def test_classify_unknown(self):
        """Test classification of unknown text."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("xyz123 random text")
        assert result.intent == "unknown"

    def test_classify_returns_intent_result(self):
        """Test that classify returns IntentResult."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("Hello")
        assert isinstance(result, IntentResult)

    def test_classify_with_language(self):
        """Test that language is passed through."""
        classifier = RuleBasedIntentClassifier()
        result = classifier.classify("Hello", "en")
        assert result.language == "en"


class TestIntentEngine:
    """Tests for the IntentEngine class."""

    def test_engine_detect_greeting(self):
        """Test that engine detects greeting."""
        engine = IntentEngine()
        result = engine.detect("Hello there")
        assert result.intent == "greeting"

    def test_engine_detect_help(self):
        """Test that engine detects help intent."""
        engine = IntentEngine()
        result = engine.detect("Help me please")
        assert result.intent == "help"

    def test_engine_detect_market_price(self):
        """Test that engine detects market price intent."""
        engine = IntentEngine()
        result = engine.detect("Market price of maize")
        assert result.intent == "market_price"

    def test_engine_returns_intent_result(self):
        """Test that engine returns IntentResult."""
        engine = IntentEngine()
        result = engine.detect("Hello")
        assert isinstance(result, IntentResult)
