"""Provider adapters for the JARVIS agent.

Each module in this package owns one external capability and hides its
transport details behind a small, typed interface:

- :mod:`jarvis.providers.llm` — streaming chat, Groq first, local Ollama as a
  rate-limit fallback.
- :mod:`jarvis.providers.search` — keyless Parallel Search over MCP, with a
  DuckDuckGo scrape as a secondary path.
- :mod:`jarvis.providers.stt` — Groq Whisper transcription.

All of them share the process HTTP pool and the retry policy from
:mod:`jarvis.retry`, and none of them raise on failure: callers get a
degraded result plus a reason, never an exception that can kill the audio loop.
"""
from __future__ import annotations

from .llm import LLMProvider, LLMRouter, OfflineProvider, OllamaProvider, ProviderReply
from .search import SearchProvider, SearchResult, build_search_provider
from .stt import Transcriber, WhisperTranscriber

__all__ = [
    "LLMProvider",
    "LLMRouter",
    "OfflineProvider",
    "OllamaProvider",
    "ProviderReply",
    "SearchProvider",
    "SearchResult",
    "build_search_provider",
    "Transcriber",
    "WhisperTranscriber",
]
