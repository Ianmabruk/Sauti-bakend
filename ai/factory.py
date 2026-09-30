"""Model provider factory for SautiPay.

Creates the appropriate ModelProvider based on configuration.
"""
from __future__ import annotations

import logging
from typing import Optional

from .providers import ModelProvider
from .mock_provider import MockModelProvider
from .local_provider import LocalModelProvider

logger = logging.getLogger(__name__)


def create_model_provider(
    provider_name: Optional[str] = None,
    model_path: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> ModelProvider:
    """Create a model provider based on the given name.

    Args:
        provider_name: "mock", "local", or None (uses default).
        model_path: Path to local model (for local provider).
        max_tokens: Default max tokens for generation.
        temperature: Default temperature for generation.

    Returns:
        A ModelProvider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider_name is None:
        provider_name = "mock"

    provider_name = provider_name.lower().strip()

    if provider_name == "mock":
        logger.info("Creating MockModelProvider (test-only)")
        return MockModelProvider(model_name="mock-model-v1")

    if provider_name == "local":
        logger.info(
            "Creating LocalModelProvider (path=%s, max_tokens=%s, temperature=%s)",
            model_path or "/models/sauti-model",
            max_tokens,
            temperature,
        )
        return LocalModelProvider(
            model_path=model_path or "/models/sauti-model",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    raise ValueError(
        f"Unknown model provider: {provider_name}. "
        f"Supported providers: mock, local"
    )


def get_provider_name(provider: ModelProvider) -> str:
    """Get the provider name from a ModelProvider instance."""
    return getattr(provider, "provider_name", "unknown")