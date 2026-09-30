"""Model provider abstraction for SautiPay.

The application code should only depend on this interface.
Concrete providers (LocalModelProvider, MockModelProvider) implement it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    """A single chat message."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class GenerationParams:
    """Parameters for response generation."""

    temperature: float = 0.7
    max_tokens: int = 1024
    top_p: float = 1.0
    stop: Optional[list[str]] = None


@dataclass
class GenerationResult:
    """Result of a generation call."""

    content: str
    model: str
    usage: dict = field(default_factory=dict)
    finish_reason: Optional[str] = None


@dataclass
class EmbeddingResult:
    """Result of an embedding call."""

    embedding: list[float]
    model: str
    usage: dict = field(default_factory=dict)


class ModelProvider(ABC):
    """Abstract interface for AI model providers.

    All SautiPay code should use this interface, never a concrete provider.
    This allows swapping the underlying model without changing application logic.
    """

    provider_name: str = "base"

    @abstractmethod
    def generate_response(
        self,
        messages: list[Message],
        context: Optional[str] = None,
        language: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate a response from the model.

        Args:
            messages: Conversation history as Message objects.
            context: Optional retrieved context to include.
            language: Target language code (e.g., "en", "sw").
            temperature: Sampling temperature (0.0 to 2.0).
            max_tokens: Maximum tokens in the response.
            **kwargs: Additional provider-specific parameters.

        Returns:
            GenerationResult with content, model name, and usage.
        """
        ...

    @abstractmethod
    def embed(self, text: str, **kwargs: Any) -> EmbeddingResult:
        """Generate an embedding for the given text.

        Args:
            text: Input text to embed.
            **kwargs: Additional provider-specific parameters.

        Returns:
            EmbeddingResult with the embedding vector and model name.
        """
        ...

    def health_check(self) -> dict:
        """Return provider health status.

        Returns:
            Dict with 'status' and optional 'details'.
        """
        return {"status": "unknown", "provider": self.provider_name}

    def supports_streaming(self) -> bool:
        """Whether this provider supports streaming responses."""
        return False

    def get_model_info(self) -> dict:
        """Return information about the underlying model."""
        return {"provider": self.provider_name}