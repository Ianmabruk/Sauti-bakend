"""Tests for the model abstraction layer."""
import pytest
from ai.providers import ModelProvider, Message, GenerationResult, GenerationParams
from ai.mock_provider import MockModelProvider
from ai.local_provider import LocalModelProvider
from ai.factory import create_model_provider


class TestMockModelProvider:
    """Tests for the MockModelProvider."""

    def test_mock_generate_response(self):
        """Test that mock provider generates a response."""
        provider = MockModelProvider()
        messages = [Message(role="user", content="Hello")]
        result = provider.generate_response(messages)
        assert isinstance(result, GenerationResult)
        assert len(result.content) > 0

    def test_mock_generate_greeting(self):
        """Test that mock provider responds to greetings."""
        provider = MockModelProvider()
        messages = [Message(role="user", content="Hello")]
        result = provider.generate_response(messages)
        assert "hello" in result.content.lower() or "help" in result.content.lower()

    def test_mock_embed(self):
        """Test that mock provider generates embeddings."""
        provider = MockModelProvider()
        result = provider.embed("Hello world")
        assert len(result.embedding) == 384
        assert all(isinstance(x, float) for x in result.embedding)

    def test_mock_health_check(self):
        """Test that mock provider returns health status."""
        provider = MockModelProvider()
        health = provider.health_check()
        assert health["status"] == "healthy"
        assert health["provider"] == "mock"

    def test_mock_is_deterministic(self):
        """Test that mock provider is deterministic."""
        provider = MockModelProvider()
        messages = [Message(role="user", content="Hello")]
        result1 = provider.generate_response(messages)
        result2 = provider.generate_response(messages)
        assert result1.content == result2.content


class TestLocalModelProvider:
    """Tests for the LocalModelProvider."""

    def test_local_not_implemented(self):
        """Test that local provider raises NotImplementedError."""
        provider = LocalModelProvider()
        messages = [Message(role="user", content="Hello")]
        with pytest.raises(NotImplementedError):
            provider.generate_response(messages)

    def test_local_health_check(self):
        """Test that local provider returns health status."""
        provider = LocalModelProvider()
        health = provider.health_check()
        assert "status" in health
        assert health["provider"] == "local"


class TestModelFactory:
    """Tests for the model factory."""

    def test_create_mock_provider(self):
        """Test creating a mock provider."""
        provider = create_model_provider("mock")
        assert isinstance(provider, MockModelProvider)

    def test_create_local_provider(self):
        """Test creating a local provider."""
        provider = create_model_provider("local")
        assert isinstance(provider, LocalModelProvider)

    def test_create_unknown_provider_raises(self):
        """Test that unknown provider raises ValueError."""
        with pytest.raises(ValueError):
            create_model_provider("unknown")

    def test_default_provider_is_mock(self):
        """Test that default provider is mock."""
        provider = create_model_provider()
        assert isinstance(provider, MockModelProvider)


class TestModelProviderInterface:
    """Tests for the ModelProvider interface."""

    def test_mock_is_model_provider(self):
        """Test that MockModelProvider implements ModelProvider."""
        provider = MockModelProvider()
        assert isinstance(provider, ModelProvider)

    def test_local_is_model_provider(self):
        """Test that LocalModelProvider implements ModelProvider."""
        provider = LocalModelProvider()
        assert isinstance(provider, ModelProvider)
