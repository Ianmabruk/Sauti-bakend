"""Shared fixtures and helpers for the JARVIS test suite.

Every test runs without network access and without audio hardware. Providers
are replaced with deterministic stubs, and synthetic PCM stands in for the
microphone, so the suite is fast and reproducible on any machine.
"""
from __future__ import annotations

import array
import math
import random
from typing import AsyncIterator, Optional

import pytest

from jarvis.config import Settings
from jarvis.providers.llm import ProviderReply
from jarvis.providers.search import SearchResult

RATE = 16000


# ----------------------------------------------------------------------
# Synthetic audio
# ----------------------------------------------------------------------


def silence_frames(count: int, rate: int = RATE) -> list[bytes]:
    """Build ``count`` frames of digital silence."""
    frame = int(rate * 2 * 20 / 1000)
    return [b"\x00" * frame] * count


def speech_frames(
    count: int, rate: int = RATE, floor: int = 600, seed: int = 7
) -> list[bytes]:
    """Build ``count`` frames of synthetic speech-like audio.

    Uses amplitude-modulated harmonics over a noise floor, which is close
    enough to real speech for both the webrtcvad and energy engines to detect
    it, without shipping audio fixtures.
    """
    rng = random.Random(seed)
    frame = int(rate * 2 * 20 / 1000)
    total = count * frame // 2
    samples = array.array("h")
    for index in range(total):
        t = index / rate
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 3.5 * t)
        value = floor * rng.uniform(-1, 1) + int(
            7000
            * envelope
            * (
                0.6 * math.sin(2 * math.pi * 150 * t)
                + 0.3 * math.sin(2 * math.pi * 450 * t)
            )
        )
        samples.append(max(-32000, min(32000, int(value))))

    pcm = samples.tobytes()
    return [pcm[i * frame : (i + 1) * frame] for i in range(count)]


async def stream(frames: list[bytes]) -> AsyncIterator[bytes]:
    """Turn a frame list into an async iterator."""
    for frame in frames:
        yield frame


def noise_frames(count: int, amplitude: int = 400, seed: int = 3, rate: int = RATE) -> list[bytes]:
    """Build ``count`` frames of low-amplitude broadband noise.

    Used to prove the VAD does not fire on a quiet room between utterances.
    """
    rng = random.Random(seed)
    frame = int(rate * 2 * 20 / 1000)
    samples = array.array(
        "h", [int(rng.uniform(-1, 1) * amplitude) for _ in range(frame // 2)]
    )
    pcm = samples.tobytes()
    return [pcm for _ in range(count)]


# ----------------------------------------------------------------------
# Provider stubs
# ----------------------------------------------------------------------


class StubLLM:
    """A scripted LLM router.

    Args:
        route: JSON string returned by :meth:`complete`, used for routing.
        answer: Text emitted in small deltas by :meth:`stream`.
        fail: When set, every method raises this exception.
    """

    def __init__(self, route: str, answer: str = "", fail: Optional[Exception] = None):
        self.route = route
        self.answer = answer
        self.fail = fail
        self.prompts: list[str] = []
        self.streamed = 0

    async def complete(self, messages, temperature=None, **kwargs) -> ProviderReply:
        """Return the scripted routing JSON."""
        if self.fail:
            raise self.fail
        self.prompts.append(messages[-1]["content"])
        return ProviderReply(text=self.route, model="stub")

    async def stream(self, messages, temperature=None, **kwargs):
        """Emit the scripted answer in small deltas."""
        if self.fail:
            raise self.fail
        self.prompts.append(messages[-1]["content"])
        self.streamed += 1
        for index in range(0, len(self.answer), 12):
            yield self.answer[index : index + 12]


class StubSearch:
    """A scripted search provider.

    Args:
        result: The :class:`SearchResult` to return.
        calls: Populated with every query received.
    """

    def __init__(self, result: SearchResult):
        self.result = result
        self.calls: list[str] = []

    async def search(self, query, *, objective="", limit=6) -> SearchResult:
        """Record the query and return the scripted result."""
        self.calls.append(query)
        return self.result


def good_search() -> SearchResult:
    """A successful search result with two citable sources."""
    return SearchResult(
        ok=True,
        query="Bitcoin price USD today",
        sources=[
            {
                "title": "CoinMarketCap Bitcoin",
                "url": "https://coinmarketcap.com/currencies/bitcoin/",
                "excerpt": "The live Bitcoin price today is $84,131.65 USD.",
                "date": "",
            },
            {
                "title": "CoinDesk Bitcoin",
                "url": "https://www.coindesk.com/price/bitcoin",
                "excerpt": "Live Bitcoin price movements from all markets.",
                "date": "",
            },
        ],
        provider="stub",
    )


def failed_search() -> SearchResult:
    """A failed search result, for the candour path."""
    return SearchResult(ok=False, query="x", reason="network unreachable")


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings with speech, caching and disk state disabled."""
    return Settings(
        agent_speak=False,
        tts_enabled=False,
        llm_cache_max=0,
        memory_state_path=str(tmp_path / "state.json"),
        search_enabled=False,
        ollama_enabled=False,
    )
