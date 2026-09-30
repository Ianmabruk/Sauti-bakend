"""JARVIS: a voice-first assistant built on free-tier services.

Stack:

- **LLM** — Groq, Llama 3.3 70B (free tier), streamed token by token with a
  local Ollama model as the rate-limit fallback.
- **Search** — Parallel's keyless hosted MCP server, with a keyless
  DuckDuckGo scrape behind it for redundancy.
- **STT** — Groq Whisper large-v3-turbo (free tier).
- **TTS** — Microsoft Edge TTS (free, no key).
- **VAD** — webrtcvad, with a dependency-free energy fallback.

Typical use::

    from jarvis import JarvisAgent

    agent = JarvisAgent()
    await agent.warm_up()
    result = await agent.ask("what is the weather in Nairobi?")
    print(result.reply, result.sources)
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = [
    "JarvisAgent",
    "TurnResult",
    "Settings",
    "get_settings",
    "main",
]


def __getattr__(name: str):
    """Import the public surface lazily.

    Keeps ``import jarvis`` cheap and free of side effects, and lets the CLI
    start even if an optional audio dependency is missing.
    """
    if name in {"JarvisAgent", "TurnResult"}:
        from . import agent as _agent

        return getattr(_agent, name)
    if name in {"Settings", "get_settings"}:
        from . import config as _config

        return getattr(_config, name)
    if name == "main":
        from .cli import main as _main

        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
