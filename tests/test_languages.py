"""Tests for language processing."""
import pytest
from backend.services.language import LanguageService, SimpleLanguageDetector
from backend.languages.registry import LanguageRegistry


class TestLanguageRegistry:
    """Tests for the language registry."""

    def test_registry_has_english(self):
        """Test that English is registered."""
        registry = LanguageRegistry()
        assert registry.is_supported("en")

    def test_registry_has_swahili(self):
        """Test that Swahili is registered."""
        registry = LanguageRegistry()
        assert registry.is_supported("sw")

    def test_registry_has_stub_languages(self):
        """Test that stub languages are registered."""
        registry = LanguageRegistry()
        for code in ["luo", "kikuyu", "kamba", "kisii", "meru", "samburu"]:
            assert registry.is_supported(code)

    def test_english_has_verified_data(self):
        """Test that English has verified data."""
        registry = LanguageRegistry()
        assert registry.is_verified("en")

    def test_swahili_has_verified_data(self):
        """Test that Swahili has verified data."""
        registry = LanguageRegistry()
        assert registry.is_verified("sw")

    def test_stub_languages_no_verified_data(self):
        """Test that stub languages do not have verified data."""
        registry = LanguageRegistry()
        for code in ["luo", "kikuyu", "kamba", "kisii", "meru", "samburu"]:
            assert not registry.is_verified(code)

    def test_get_all_returns_all_languages(self):
        """Test that get_all returns all languages."""
        registry = LanguageRegistry()
        languages = registry.get_all(active_only=False)
        assert len(languages) == 8

    def test_get_all_active_only(self):
        """Test that get_all with active_only returns active languages."""
        registry = LanguageRegistry()
        languages = registry.get_all(active_only=True)
        assert len(languages) == 8


class TestLanguageDetection:
    """Tests for language detection."""

    def test_detect_english(self):
        """Test detection of English text."""
        detector = SimpleLanguageDetector()
        result = detector.detect("Hello, how are you?")
        assert result.language == "en"
        assert result.confidence > 0

    def test_detect_swahili(self):
        """Test detection of Swahili text."""
        detector = SimpleLanguageDetector()
        result = detector.detect("Hujumba, habari gani?")
        assert result.language == "sw"
        assert result.confidence > 0

    def test_detect_unknown_returns_default(self):
        """Test detection of unknown text returns default."""
        detector = SimpleLanguageDetector()
        result = detector.detect("xyz123")
        assert result.language == "en"
        assert result.confidence == 0.0

    def test_language_service_detect(self):
        """Test LanguageService.detect method."""
        service = LanguageService()
        result = service.detect("Hello world")
        assert result.language in ["en", "sw"]


class TestLanguageService:
    """Tests for the LanguageService class."""

    def test_get_supported_languages(self):
        """Test that supported languages are returned."""
        service = LanguageService()
        languages = service.get_supported_languages()
        assert len(languages) == 8

    def test_is_supported(self):
        """Test is_supported method."""
        service = LanguageService()
        assert service.is_supported("en")
        assert service.is_supported("sw")
        assert not service.is_supported("xyz")

    def test_is_verified(self):
        """Test is_verified method."""
        service = LanguageService()
        assert service.is_verified("en")
        assert service.is_verified("sw")
        assert not service.is_verified("luo")
