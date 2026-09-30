"""Mock model provider for testing only.

This provider is deterministic and does NOT require any external API.
It must NEVER be used in production. It exists solely to allow automated
tests to run without a real model.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from .providers import (
    EmbeddingResult,
    GenerationParams,
    GenerationResult,
    Message,
    ModelProvider,
)


class MockModelProvider(ModelProvider):
    """Deterministic mock provider for automated tests.

    WARNING: This is a test-only provider. It generates predictable,
    deterministic responses based on input hashes. It does NOT use
    any real AI model and must never be mistaken for production AI.
    """

    provider_name = "mock"

    # Deterministic responses keyed by intent
    INTENT_RESPONSES = {
        "greeting": "Hello! How can I help you today?",
        "general_question": "That's a good question. I'd need more context to give a precise answer.",
        "news": "I don't have the latest news. Please check trusted sources for current events.",
        "government_information": "For official government information, please visit the relevant government website or office.",
        "civic_information": "Civic information varies by location. Please provide more details about your specific question.",
        "agriculture": "Agricultural advice depends on your region, crop, and season. Please consult local agricultural officers for verified guidance.",
        "market_price": "Market prices change frequently. I need access to current market data to give you accurate pricing information.",
        "product_search": "I don't have access to current product listings. Please check trusted market sources.",
        "order": "Ordering functionality is not yet available.",
        "payment": "Payment processing is not yet available.",
        "help": "I can help with questions about government services, agriculture, market prices, and civic information. What would you like to know?",
        "unknown": "I'm not sure I understand. Could you please rephrase your question?",
    }

    DEFAULT_RESPONSE = "I'm here to help with Kenyan information. What would you like to know?"

    def __init__(self, model_name: str = "mock-model-v1") -> None:
        self.model_name = model_name

    def generate_response(
        self,
        messages: list[Message],
        context: Optional[str] = None,
        language: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate a deterministic mock response."""
        # Build a deterministic key from the last user message
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_msg = msg.content
                break

        # If context is provided, use it in the response
        if context:
            content = f"Based on the information I found: {context[:200]}"
            if len(context) > 200:
                content += "..."
        else:
            # Use a simple hash to pick a response
            content = self._deterministic_response(last_user_msg, language)

        return GenerationResult(
            content=content,
            model=self.model_name,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )

    def embed(self, text: str, **kwargs: Any) -> EmbeddingResult:
        """Generate a deterministic mock embedding."""
        # Create a 384-dimensional deterministic embedding from text hash
        text_hash = hashlib.sha256(text.encode("utf-8")).digest()
        embedding = []
        for i in range(384):
            byte_val = text_hash[i % len(text_hash)]
            # Normalize to [-1, 1]
            embedding.append((byte_val - 128) / 128.0)
        return EmbeddingResult(
            embedding=embedding,
            model=self.model_name,
            usage={"prompt_tokens": 0, "total_tokens": 0},
        )

    def _deterministic_response(self, message: str, language: Optional[str]) -> str:
        """Generate a deterministic response based on message content."""
        message_lower = message.lower().strip()

        # Simple keyword matching for deterministic behavior
        if any(w in message_lower for w in ["hello", "hi", "hujumba", "mambo", "shikamoo"]):
            return self.INTENT_RESPONSES["greeting"]
        if any(w in message_lower for w in ["news", "habari", "magazeti"]):
            return self.INTENT_RESPONSES["news"]
        if any(w in message_lower for w in ["government", "serikali", "county"]):
            return self.INTENT_RESPONSES["government_information"]
        if any(w in message_lower for w in ["civic", "citizen", "raia", "haki"]):
            return self.INTENT_RESPONSES["civic_information"]
        if any(w in message_lower for w in ["agriculture", "kilimo", "crop", "maize", "mahindi"]):
            return self.INTENT_RESPONSES["agriculture"]
        if any(w in message_lower for w in ["price", "bei", "market", "soko"]):
            return self.INTENT_RESPONSES["market_price"]
        if any(w in message_lower for w in ["order", "order", "purchase", "ununuzi"]):
            return self.INTENT_RESPONSES["order"]
        if any(w in message_lower for w in ["payment", "malipo", "pesa"]):
            return self.INTENT_RESPONSES["payment"]
        if any(w in message_lower for w in ["help", "msaada", "saidia"]):
            return self.INTENT_RESPONSES["help"]

        return self.DEFAULT_RESPONSE

    def health_check(self) -> dict:
        """Return mock health status."""
        return {
            "status": "healthy",
            "provider": self.provider_name,
            "model": self.model_name,
            "note": "This is a deterministic mock. No real AI is being used.",
        }