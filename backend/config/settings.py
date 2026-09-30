"""Central configuration for SAUTI.

Every credential and tunable lives here and is read from the environment.
Nothing in this module ever returns a secret to the frontend.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the SAUTI agent."""

    # --- LLM -------------------------------------------------------------
    model_provider: str = field(default_factory=lambda: os.environ.get("MODEL_PROVIDER", "mock"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "")
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.3))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 900))
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 45.0))

    # --- Gemini (Google) --------------------------------------------------
    #: Server-side only. Never returned to a client, never bundled.
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    )
    gemini_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )
    )
    gemini_timeout: float = field(default_factory=lambda: _env_float("GEMINI_TIMEOUT", 45.0))
    gemini_temperature: float = field(default_factory=lambda: _env_float("GEMINI_TEMPERATURE", 0.3))
    gemini_max_tokens: int = field(default_factory=lambda: _env_int("GEMINI_MAX_TOKENS", 1200))
    gemini_max_tool_rounds: int = field(
        default_factory=lambda: _env_int("GEMINI_MAX_TOOL_ROUNDS", 3)
    )
    #: Max audio bytes accepted for server-side transcription (~8 MB).
    gemini_max_audio_bytes: int = field(
        default_factory=lambda: _env_int("GEMINI_MAX_AUDIO_BYTES", 8 * 1024 * 1024)
    )

    # --- Groq (primary inference provider) ---------------------------------
    #: Groq serves the chat model and Whisper speech-to-text from one key.
    #: Server-side only: read from the environment, never returned by an
    #: endpoint, never bundled into the frontend.
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    #: OpenAI-compatible base. Groq's API is a superset of the OpenAI shape,
    #: which is why the existing OpenAI-compatible client can target it.
    groq_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
        )
    )
    #: Configurable so the model can be changed without a code edit. Must be a
    #: model this account can actually reach; see GROQ_FALLBACK_MODELS.
    groq_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
    )
    groq_stt_model: str = field(
        default_factory=lambda: os.environ.get(
            "GROQ_STT_MODEL", "whisper-large-v3-turbo"
        )
    )
    groq_timeout: float = field(default_factory=lambda: _env_float("GROQ_TIMEOUT", 45.0))
    groq_temperature: float = field(default_factory=lambda: _env_float("GROQ_TEMPERATURE", 0.3))
    # Must stay under the account's on-demand output-tokens-per-minute limit
    # (1000 on the standard tier). Asking for more than the limit does not get
    # a truncated answer, it gets a 429 for the whole request, which silently
    # degrades the turn to the "service unavailable" reply.
    groq_max_tokens: int = field(default_factory=lambda: _env_int("GROQ_MAX_TOKENS", 900))
    groq_max_tool_rounds: int = field(
        default_factory=lambda: _env_int("GROQ_MAX_TOOL_ROUNDS", 3)
    )
    groq_max_audio_bytes: int = field(
        default_factory=lambda: _env_int("GROQ_MAX_AUDIO_BYTES", 8 * 1024 * 1024)
    )

    # --- Model split: a cheap tier for mechanical work ----------------------
    #: Query rewriting and answer verification are narrow, repeatable tasks.
    #: Running them on the main reasoning model wastes its capacity and
    #: latency, so they get their own small model. Keep this env-configurable:
    #: which small models exist depends on the Groq account, and the
    #: instruction document's suggested ids are not available on every plan.
    groq_fast_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
    )
    #: The fast tier is on the critical path twice per turn, so it gets a
    #: tighter budget than the main model and fails fast rather than hanging.
    groq_fast_timeout: float = field(
        default_factory=lambda: _env_float("GROQ_FAST_TIMEOUT", 12.0)
    )
    groq_fast_max_tokens: int = field(
        default_factory=lambda: _env_int("GROQ_FAST_MAX_TOKENS", 160)
    )

    # --- User location, injected into the system prompt ---------------------
    #: SAUTI has no way to ask the browser where the user is, so a question
    #: like "what's the weather" would otherwise be answered about some
    #: arbitrarily chosen city. These defaults are the fallback; a future
    #: phase can let the client send an explicit location that overrides them.
    user_city: str = field(default_factory=lambda: os.environ.get("USER_CITY", "Nairobi"))
    user_country: str = field(default_factory=lambda: os.environ.get("USER_COUNTRY", "Kenya"))
    user_timezone: str = field(
        default_factory=lambda: os.environ.get("USER_TIMEZONE", "Africa/Nairobi")
    )

    # --- Answer reliability switches ----------------------------------------
    #: Force a tool call for questions that depend on changing facts, so a
    #: confident-sounding model cannot skip the search and invent a value.
    sauti_force_search: bool = field(
        default_factory=lambda: _env_bool("SAUTI_FORCE_SEARCH", True)
    )
    #: Rewrite the user's phrasing into a real search query before searching.
    sauti_query_rewriting: bool = field(
        default_factory=lambda: _env_bool("SAUTI_QUERY_REWRITING", True)
    )
    #: Check the finished answer against its sources and retry once if it
    #: does not hold up.
    sauti_self_verification: bool = field(
        default_factory=lambda: _env_bool("SAUTI_SELF_VERIFICATION", True)
    )
    #: Number of sources cited inline as [1], [2] and listed at the end.
    sauti_citation_count: int = field(
        default_factory=lambda: _env_int("SAUTI_CITATION_COUNT", 2)
    )
    #: Per-session ceiling on AI turns, enforced in the API layer.
    sauti_chat_rate_limit: str = field(
        default_factory=lambda: os.environ.get("SAUTI_CHAT_RATE_LIMIT", "30 per minute")
    )
    #: Optional model list for the intent/intent-bias phase.
    sauti_mode_default: str = field(
        default_factory=lambda: os.environ.get("SAUTI_MODE_DEFAULT", "internet")
    )

    # --- Research / search ------------------------------------------------
    search_provider: str = field(
        default_factory=lambda: os.environ.get("SEARCH_PROVIDER", "auto")
    )
    search_api_key: str = field(default_factory=lambda: os.environ.get("SEARCH_API_KEY", ""))
    search_base_url: str = field(
        default_factory=lambda: os.environ.get("SEARCH_BASE_URL", "")
    )
    search_timeout: float = field(default_factory=lambda: _env_float("SEARCH_TIMEOUT", 12.0))
    search_retries: int = field(default_factory=lambda: _env_int("SEARCH_RETRIES", 2))
    search_max_results: int = field(default_factory=lambda: _env_int("SEARCH_MAX_RESULTS", 8))
    search_max_pages_to_read: int = field(
        default_factory=lambda: _env_int("SEARCH_MAX_PAGES_TO_READ", 4)
    )
    # Hard ceiling on extracted price/date observations. Each one carries its
    # surrounding context into the tool payload, and that payload is replayed
    # into the model on every later turn of the tool loop. Unbounded, a
    # figure-dense page produced enough context to trip the provider's
    # input-tokens-per-minute limit, discarding the research and the answer.
    research_max_observations: int = field(
        default_factory=lambda: _env_int("RESEARCH_MAX_OBSERVATIONS", 12)
    )

    # --- Page reader ------------------------------------------------------
    reader_timeout: float = field(default_factory=lambda: _env_float("READER_TIMEOUT", 12.0))
    reader_max_bytes: int = field(default_factory=lambda: _env_int("READER_MAX_BYTES", 1_500_000))
    reader_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "READER_USER_AGENT",
            "Mozilla/5.0 (compatible; SAUTI-Agent/1.0; +https://example.invalid/sauti)",
        )
    )

    # --- Agent ------------------------------------------------------------
    agent_max_tool_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_TOOL_STEPS", 4))
    agent_history_limit: int = field(default_factory=lambda: _env_int("AGENT_HISTORY_LIMIT", 12))
    memory_recall_limit: int = field(default_factory=lambda: _env_int("MEMORY_RECALL_LIMIT", 5))
    memory_enabled: bool = field(default_factory=lambda: _env_bool("MEMORY_ENABLED", True))
    # Caps how much of one memory is injected per turn. A stored study set or
    # business plan runs to several kilobytes; injecting it whole crowds out
    # the question and the rest of the system prompt.
    memory_max_content_chars: int = field(
        default_factory=lambda: _env_int("MEMORY_MAX_CONTENT_CHARS", 800)
    )

    # --- Safety -----------------------------------------------------------
    enable_dangerous_tools: bool = field(
        default_factory=lambda: _env_bool("ENABLE_DANGEROUS_TOOLS", False)
    )
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    # --- Derived helpers --------------------------------------------------

    @property
    def llm_configured(self) -> bool:
        """True when a real (non-mock) model provider can be used."""
        if self.groq_configured:
            return True
        if self.gemini_configured:
            return True
        return bool(self.llm_api_key) and self.model_provider not in {"", "mock"}

    @property
    def groq_configured(self) -> bool:
        """True when a Groq key is present on the server."""
        return bool(self.groq_api_key.strip())

    @property
    def gemini_configured(self) -> bool:
        """True when a Gemini key is present on the server."""
        return bool(self.gemini_api_key.strip())

    @property
    def primary_engine(self) -> str:
        """Which engine the agent will actually use.

        Groq is preferred. Gemini remains available as a secondary engine so an
        existing deployment does not lose its working provider.
        """
        if self.groq_configured:
            return "groq"
        if self.gemini_configured:
            return "gemini"
        if self.llm_api_key and self.model_provider not in {"", "mock"}:
            return "openai_compatible"
        return "offline"

    @property
    def search_configured(self) -> bool:
        """True when the configured search provider has what it needs to run."""
        provider = (self.search_provider or "auto").lower()
        if provider in {"stub", "duckduckgo"}:
            # Both are keyless. DuckDuckGo scrapes a public HTML endpoint, so
            # it needs no credential and was wrongly reported as unavailable
            # purely because it was not named here.
            return True
        if provider in {"brave", "tavily", "serpapi"}:
            return bool(self.search_api_key)
        # auto: an API key enables the keyed providers
        return bool(self.search_api_key)

    def public_summary(self) -> dict:
        """A safe-to-log view. Never includes credential values."""
        return {
            "model_provider": self.model_provider,
            "groq_configured": self.groq_configured,
            "groq_model": self.groq_model if self.groq_configured else None,
            "groq_stt_model": self.groq_stt_model if self.groq_configured else None,
            "gemini_configured": self.gemini_configured,
            "gemini_model": self.gemini_model if self.gemini_configured else None,
            "llm_configured": self.llm_configured,
            "primary_engine": self.primary_engine,
            "search_provider": self.search_provider,
            "search_configured": self.search_configured,
            "memory_enabled": self.memory_enabled,
            "enable_dangerous_tools": self.enable_dangerous_tools,
        }


_settings: Optional[Settings] = None


def get_settings(refresh: bool = False) -> Settings:
    """Return the process-wide settings singleton.

    Args:
        refresh: Rebuild from the environment. Used by tests.
    """
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
