"""Local model provider for self-hosted open-weight models.

This provider is designed to work with locally hosted models.
For Phase 1, it raises NotImplementedError when used without a real model.
A real model can be plugged in later without changing the application code.
"""
from __future__ import annotations

from typing import Any, Optional

from .providers import (
    EmbeddingResult,
    GenerationResult,
    Message,
    ModelProvider,
)


class LocalModelProvider(ModelProvider):
    """Provider for self-hosted open-weight models.

    This is a placeholder adapter. To use a real model:
    1. Set MODEL_PROVIDER=local in environment
    2. Point LOCAL_MODEL_PATH to your model directory
    3. Implement the actual model loading and inference

    The application code only depends on the ModelProvider interface,
    so swapping this implementation is straightforward.
    """

    provider_name = "local"

    def __init__(
        self,
        model_path: str = "/models/sauti-model",
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> None:
        self.model_path = model_path
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._model = None  # Lazy-loaded model

    def _load_model(self) -> Any:
        """Load the local model. Override this for actual implementation."""
        raise NotImplementedError(
            "Local model loading is not implemented. "
            "To use a real model, implement _load_model() with your "
            "preferred framework (e.g., transformers, llama-cpp-python, vllm). "
            f"Model path: {self.model_path}"
        )

    def generate_response(
        self,
        messages: list[Message],
        context: Optional[str] = None,
        language: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate response from local model."""
        model = self._load_model()
        # Build prompt from messages and context
        prompt = self._build_prompt(messages, context, language)
        # Call model inference (implementation-specific)
        # This is a placeholder - actual implementation depends on the framework
        raise NotImplementedError(
            "Local model inference is not implemented. "
            "Implement generate_response() with your model framework."
        )

    def embed(self, text: str, **kwargs: Any) -> EmbeddingResult:
        """Generate embedding from local model."""
        model = self._load_model()
        raise NotImplementedError(
            "Local embedding is not implemented. "
            "Implement embed() with your model framework."
        )

    def _build_prompt(
        self,
        messages: list[Message],
        context: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str:
        """Build a prompt string from messages and context."""
        parts = []
        if context:
            parts.append(f"Context: {context}")
        if language:
            parts.append(f"Respond in language code: {language}")
        for msg in messages:
            parts.append(f"{msg.role}: {msg.content}")
        return "\n".join(parts)

    def health_check(self) -> dict:
        """Return health status."""
        try:
            # Check if model path exists
            import os

            if os.path.exists(self.model_path):
                return {
                    "status": "healthy",
                    "provider": self.provider_name,
                    "model_path": self.model_path,
                    "note": "Model path exists but inference is not implemented.",
                }
            return {
                "status": "unhealthy",
                "provider": self.provider_name,
                "error": f"Model path not found: {self.model_path}",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": self.provider_name,
                "error": str(e),
            }