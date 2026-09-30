"""Central configuration for JARVIS.

Every credential, endpoint and tunable lives here and is sourced from the
environment (optionally via a ``.env`` file). Nothing in this module ever
returns a secret to a log line or to the console.

Design notes:
- Grouped by subsystem with an explicit prefix per group.
- Sensible defaults exist for every key, so the agent boots (in degraded
  mode) with an empty ``.env``.
- :meth:`Settings.redacted` is the only sanctioned way to log this object.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Groq's OpenAI-compatible endpoint. Used for both chat and audio.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

#: Keyless Parallel Search MCP server (no account, no API key required).
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"


class Settings(BaseSettings):
    """Immutable runtime configuration.

    Attributes are grouped by subsystem. All fields may be overridden through
    environment variables or a ``.env`` file sitting next to the process
    working directory.
    """

    model_config = SettingsConfigDict(
        env_file=os.environ.get("JARVIS_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Groq LLM + STT (one key serves both)
    # ------------------------------------------------------------------
    groq_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Groq API key. Free tier is sufficient for this workload.",
    )
    groq_base_url: str = Field(default=GROQ_BASE_URL)
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Chat model. Fast, strong instruction following, free tier.",
    )
    groq_stt_model: str = Field(
        default="whisper-large-v3-turbo",
        description="Speech-to-text model.",
    )
    llm_temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=512, ge=32, le=8192)
    llm_timeout: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_backoff_base: float = Field(default=0.6, gt=0)
    llm_backoff_max: float = Field(default=12.0, gt=0)
    llm_stream: bool = Field(
        default=True,
        description="Stream tokens so speech can start on the first sentence.",
    )

    # ------------------------------------------------------------------
    # Local model fallback (Ollama) — used when Groq rate-limits us
    # ------------------------------------------------------------------
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_model: str = Field(default="llama3.2")
    ollama_enabled: bool = Field(default=True)
    ollama_timeout: float = Field(default=25.0, gt=0)
    ollama_probe_timeout: float = Field(
        default=1.5,
        gt=0,
        description="Short probe timeout so a missing Ollama never stalls a turn.",
    )

    # ------------------------------------------------------------------
    # Web search
    # ------------------------------------------------------------------
    search_url: str = Field(default=PARALLEL_MCP_URL)
    search_timeout: float = Field(default=15.0, gt=0)
    search_max_retries: int = Field(default=2, ge=0, le=5)
    search_max_results: int = Field(default=6, ge=1, le=20)
    search_session_id: str = Field(
        default="",
        description="Stable per-session id reused across searches for rate limits.",
    )
    search_enabled: bool = Field(default=True)

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------
    search_cache_ttl: float = Field(
        default=600.0, gt=0, description="Search cache lifetime in seconds (10 min)."
    )
    search_cache_max: int = Field(default=256, ge=1)
    llm_cache_max: int = Field(
        default=100,
        ge=0,
        description="LRU entries for replies. 0 disables reply caching.",
    )
    llm_cache_ttl: float = Field(default=0.0, ge=0, description="0 disables reply caching.")

    # ------------------------------------------------------------------
    # Voice activity detection
    # ------------------------------------------------------------------
    vad_aggressiveness: int = Field(
        default=2, ge=0, le=3, description="webrtcvad aggressiveness: 0 lenient, 3 strict."
    )
    vad_silence_ms: int = Field(
        default=700, gt=0, description="Silence after speech that ends an utterance."
    )
    vad_speech_ms: int = Field(
        default=250, gt=0, description="Continuous voice needed to start an utterance."
    )
    vad_min_utterance_ms: int = Field(default=400, gt=0)
    vad_max_utterance_s: float = Field(default=25.0, gt=0)
    vad_energy_threshold: int = Field(
        default=500,
        ge=0,
        description="RMS floor for the pure-python fallback VAD (0-32767).",
    )
    vad_frame_ms: int = Field(default=20, ge=10, le=30)
    sample_rate: int = Field(default=16000, description="Capture rate required by STT.")
    mic_device: Optional[str] = Field(
        default=None, description="ALSA/sounddevice device. None picks the default."
    )
    mic_gain: float = Field(default=1.0, gt=0, description="Input gain applied before STT.")

    # ------------------------------------------------------------------
    # Text to speech
    # ------------------------------------------------------------------
    tts_enabled: bool = Field(default=True)
    tts_voice: str = Field(default="en-GB-RyanNeural", description="Edge TTS voice.")
    tts_rate: str = Field(default="+8%", description="Edge TTS rate modifier.")
    tts_pitch: str = Field(default="+0Hz")
    tts_volume: str = Field(default="+0%")
    tts_timeout: float = Field(default=20.0, gt=0)
    tts_player: str = Field(
        default="ffplay",
        description="Command used to play MP3. Empty means console-only.",
    )
    tts_max_chars: int = Field(
        default=400, ge=20, description="Longest chunk handed to a single TTS request."
    )

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    memory_window: int = Field(default=10, ge=1, description="Recent turns kept verbatim.")
    memory_state_path: str = Field(default="jarvis_state.json")
    memory_summary_max_chars: int = Field(default=2000, gt=0)
    memory_summarise: bool = Field(
        default=True, description="Summarise turns that fall out of the window."
    )

    # ------------------------------------------------------------------
    # Agent behaviour
    # ------------------------------------------------------------------
    agent_max_tool_rounds: int = Field(default=3, ge=1, le=8)
    agent_first_sentence_chars: int = Field(
        default=60, ge=10, description="Minimum buffer before first-sentence speech."
    )
    agent_history_chars: int = Field(
        default=12000, gt=0, description="Hard cap on prompt size sent to the model."
    )
    agent_speak: bool = Field(default=True, description="Speak replies by default.")
    agent_user_name: str = Field(default="sir", description="How the agent addresses you.")

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Optional[str] = None
    log_json: bool = Field(default=False, description="Emit newline-delimited JSON logs.")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @field_validator("tts_voice")
    @classmethod
    def _strip_voice(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("tts_voice must not be empty")
        return cleaned

    @field_validator("sample_rate")
    @classmethod
    def _valid_rate(cls, value: int) -> int:
        if value not in (8000, 16000, 32000, 48000):
            raise ValueError("sample_rate must be one of 8000, 16000, 32000, 48000")
        return value

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def groq_configured(self) -> bool:
        """True when a Groq key is present."""
        return bool(self.groq_api_key.get_secret_value().strip())

    @property
    def groq_auth_headers(self) -> dict[str, str]:
        """Authorization headers for Groq. Never logged."""
        return {
            "Authorization": f"Bearer {self.groq_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    @property
    def search_session(self) -> str:
        """A stable session id, generated once and then reused."""
        return self.search_session_id or "jarvis-local-session"

    def redacted(self) -> dict[str, object]:
        """A log-safe view of the configuration. Contains no secret values."""
        return {
            "groq_configured": self.groq_configured,
            "groq_model": self.groq_model,
            "groq_stt_model": self.groq_stt_model,
            "ollama_enabled": self.ollama_enabled,
            "search_enabled": self.search_enabled,
            "search_url": self.search_url,
            "tts_enabled": self.tts_enabled,
            "tts_voice": self.tts_voice,
            "tts_player": self.tts_player or "<console only>",
            "memory_window": self.memory_window,
            "agent_speak": self.agent_speak,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that constructing :class:`Settings` (and re-reading ``.env``)
    happens exactly once per process.
    """
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache and rebuild settings from the environment.

    Used by tests and after mutating the environment at runtime.
    """
    get_settings.cache_clear()
    return get_settings()
