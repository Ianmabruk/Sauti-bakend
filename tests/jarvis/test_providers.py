"""Tests for provider reliability: rate limits, failover, caching, parsing."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from jarvis.cache import TTLCache
from jarvis.config import Settings
from jarvis.providers.llm import (
    GroqProvider,
    LLMRouter,
    OfflineProvider,
    OllamaProvider,
    RateLimited,
)
from jarvis.providers.search import (
    CachedSearchProvider,
    DuckDuckGoProvider,
    ParallelMcpProvider,
    SearchProvider,
    SearchResult,
    _extract_excerpt,
)
from jarvis.providers.stt import WhisperTranscriber, wav_duration_seconds
from jarvis.retry import ProviderError

from .conftest import good_search


def run(coro):
    """Run a coroutine to completion."""
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Search parsing and failover
# ----------------------------------------------------------------------


class TestParallelParsing:
    """Normalising Parallel's MCP payloads."""

    def test_parses_excerpts_list(self):
        payload = """{"results": [{"url": "https://a", "title": "A",
            "excerpts": ["first", "second"], "publish_date": "2026-01-01"}]}"""
        sources = ParallelMcpProvider._parse_results(payload)
        assert len(sources) == 1
        assert sources[0]["excerpt"] == "first second"
        assert sources[0]["date"] == "2026-01-01"

    def test_parses_single_excerpt_string(self):
        payload = '{"results": [{"url": "https://a", "title": "A", "excerpt": "solo"}]}'
        assert ParallelMcpProvider._parse_results(payload)[0]["excerpt"] == "solo"

    def test_ignores_entries_without_a_url(self):
        payload = '{"results": [{"title": "no url"}, {"url": "https://b", "title": "B"}]}'
        sources = ParallelMcpProvider._parse_results(payload)
        assert [s["url"] for s in sources] == ["https://b"]

    def test_survives_json_embedded_in_prose(self):
        payload = 'Here are the results:\n{"results": [{"url": "https://a", "title": "A"}]}'
        assert len(ParallelMcpProvider._parse_results(payload)) == 1

    def test_returns_empty_for_garbage(self):
        assert ParallelMcpProvider._parse_results("no json here") == []
        assert ParallelMcpProvider._parse_results("") == []

    def test_excerpt_falls_back_across_field_names(self):
        assert _extract_excerpt({"excerpts": ["a", "b"]}) == "a b"
        assert _extract_excerpt({"excerpt": "c"}) == "c"
        assert _extract_excerpt({"content": "d"}) == "d"
        assert _extract_excerpt({}) == ""


class TestSearchFailover:
    """Primary, secondary and the cache."""

    def _stack(self, primary_result, secondary_result, **kwargs):
        class Fake(SearchProvider):
            def __init__(self, name, result):
                self.name = name
                self._result = result
                self.calls: list[str] = []

            @property
            def available(self):
                return True

            async def search(self, query, *, objective="", limit=6):
                self.calls.append(query)
                return self._result

        primary = Fake("primary", primary_result)
        secondary = Fake("secondary", secondary_result)
        cache = TTLCache(max_size=8, ttl=60)
        return CachedSearchProvider(primary, secondary, cache), primary, secondary

    def test_uses_the_primary(self):
        stack, primary, secondary = self._stack(good_search(), good_search())
        result = run(stack.search("btc"))
        assert result.ok is True
        assert primary.calls == ["btc"]
        assert secondary.calls == []

    def test_falls_back_when_the_primary_fails(self):
        broken = SearchResult(ok=False, reason="502 Bad Gateway")
        stack, primary, secondary = self._stack(broken, good_search())
        result = run(stack.search("btc"))
        assert result.ok is True
        assert primary.calls == ["btc"]
        assert secondary.calls == ["btc"]

    def test_reports_failure_when_both_fail(self):
        broken = SearchResult(ok=False, reason="down")
        stack, _, _ = self._stack(broken, broken)
        result = run(stack.search("btc"))
        assert result.ok is False
        assert result.reason

    def test_second_call_is_served_from_cache(self):
        stack, primary, _ = self._stack(good_search(), good_search())
        run(stack.search("btc"))
        result = run(stack.search("btc"))
        assert result.cached is True
        assert primary.calls == ["btc"], "a cache hit must not hit the network"

    def test_failures_are_not_cached(self):
        """A total outage must be retried on the very next question."""
        broken = SearchResult(ok=False, reason="down")
        stack, primary, _ = self._stack(broken, broken)
        run(stack.search("btc"))
        run(stack.search("btc"))
        assert primary.calls == ["btc", "btc"]

    def test_recovered_results_are_cached(self):
        """Once the secondary answers, the result is worth caching."""
        broken = SearchResult(ok=False, reason="502")
        stack, primary, _ = self._stack(broken, good_search())
        run(stack.search("btc"))
        result = run(stack.search("btc"))
        assert result.cached is True
        assert primary.calls == ["btc"]

    def test_cache_key_is_case_insensitive(self):
        stack, primary, _ = self._stack(good_search(), good_search())
        run(stack.search("Bitcoin"))
        run(stack.search("bitcoin"))
        assert primary.calls == ["Bitcoin"]

    def test_duckduckgo_unwraps_redirect_links(self):
        url = DuckDuckGoProvider._unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx")
        assert url == "https://example.com/x"


# ----------------------------------------------------------------------
# LLM failover
# ----------------------------------------------------------------------


def rate_limited_error() -> httpx.HTTPStatusError:
    """Build a 429 the way a real provider would raise it."""
    return httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=httpx.Request("POST", "https://api.groq.com/x"),
        response=httpx.Response(429),
    )


class DummyProvider:
    """A scripted LLM provider for chain tests."""

    def __init__(self, name, text="hello", fail=None, available=True):
        self.name = name
        self.text = text
        self.fail = fail
        self._available = available
        self.calls = 0

    @property
    def available(self):
        return self._available

    async def stream(self, messages, *, temperature=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        for word in self.text.split(" "):
            yield word + " "

    async def complete(self, messages, *, temperature=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        from jarvis.providers.llm import ProviderReply

        return ProviderReply(text=self.text, model=self.name)


class TestRouterFailover:
    """Groq to Ollama to offline."""

    def test_falls_back_when_groq_is_rate_limited(self, settings: Settings):
        router = LLMRouter(settings=settings)
        groq = DummyProvider("groq", fail=rate_limited_error())
        ollama = DummyProvider("ollama", "local answer")
        router.groq = groq
        router.ollama = ollama
        router.offline = DummyProvider("offline", "offline answer")

        reply = run(router.complete([{"role": "user", "content": "hi"}]))
        assert "local answer" in reply.text
        assert groq.calls == 1 and ollama.calls == 1

    def test_falls_back_to_offline_when_all_fail(self, settings: Settings):
        router = LLMRouter(settings=settings)
        router.groq = DummyProvider("groq", fail=rate_limited_error())
        router.ollama = DummyProvider("ollama", fail=ProviderError("down"))
        router.offline = OfflineProvider(settings)

        reply = run(router.complete([{"role": "user", "content": "hi"}]))
        assert reply.ok is True
        assert "unavailable" in reply.text.lower()
        assert reply.degraded is True

    def test_unavailable_providers_are_skipped(self, settings: Settings):
        router = LLMRouter(settings=settings)
        groq = DummyProvider("groq", "groq answer", available=False)
        ollama = DummyProvider("ollama", "ollama answer", available=True)
        router.groq = groq
        router.ollama = ollama

        reply = run(router.complete([{"role": "user", "content": "hi"}]))
        assert "ollama answer" in reply.text
        assert groq.calls == 0, "an unavailable provider must not be called"

    def test_offline_always_answers(self, settings: Settings):
        reply = run(OfflineProvider(settings).complete([{"role": "user", "content": "hi"}]))
        assert reply.text
        assert "sir" in reply.text.lower()

    def test_offline_never_claims_a_fact(self, settings: Settings):
        """The offline message must not look like an answer to the question."""
        reply = run(OfflineProvider(settings).complete([{"role": "user", "content": "btc price?"}]))
        assert "unavailable" in reply.text.lower()


class TestGroqRateLimit:
    """Rate limiting opens a local cooldown."""

    def test_cooldown_marks_the_provider_unavailable(self, settings: Settings):
        provider = GroqProvider(Settings(groq_api_key="sk-test", llm_max_retries=0))
        assert provider.available is True
        provider._note_rate_limit(rate_limited_error())
        assert provider.available is False

    def test_no_key_means_not_available(self, settings: Settings):
        assert GroqProvider(Settings(groq_api_key="")).available is False


class TestOllamaProbe:
    """The local fallback must not stall a turn when absent."""

    def test_unreachable_ollama_is_reported_fast(self, settings: Settings):
        provider = OllamaProvider(
            Settings(ollama_base_url="http://127.0.0.1:1", ollama_probe_timeout=0.1)
        )
        assert provider.available is False

    def test_probe_result_is_cached(self, settings: Settings, monkeypatch):
        """A missing Ollama must not cost a TCP connect on every turn."""
        from jarvis.providers import llm as llm_mod

        probes = {"n": 0}

        def fake_probe(host, port, timeout=0.25):
            probes["n"] += 1
            return False

        monkeypatch.setattr(llm_mod, "is_port_open", fake_probe)
        provider = OllamaProvider(Settings(ollama_probe_timeout=0.1))
        assert provider.available is False
        assert provider.available is False
        assert probes["n"] == 1, "the probe result must be cached"


# ----------------------------------------------------------------------
# Speech to text
# ----------------------------------------------------------------------


class TestTranscriber:
    """STT degrades instead of raising."""

    def test_missing_key_is_reported(self, settings: Settings):
        transcriber = WhisperTranscriber(Settings(groq_api_key=""))
        result = run(transcriber.transcribe(b"RIFF....WAVE"))
        assert result.ok is False
        assert "key" in result.reason.lower()

    def test_empty_audio_is_rejected(self, settings: Settings):
        transcriber = WhisperTranscriber(Settings(groq_api_key="sk-test"))
        assert run(transcriber.transcribe(b"")).ok is False

    def test_transport_failure_is_caught(self, settings: Settings, monkeypatch):
        transcriber = WhisperTranscriber(
            Settings(groq_api_key="sk-test", llm_max_retries=0, llm_timeout=1.0)
        )

        async def boom(*args, **kwargs):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(transcriber, "_post_once", boom)
        result = run(transcriber.transcribe(b"RIFF....WAVEfmt "))
        assert result.ok is False
        assert result.reason

    def test_wav_duration_is_parsed(self):
        import io
        import wave as wav_mod

        buffer = io.BytesIO()
        with wav_mod.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 16000)
        assert wav_duration_seconds(buffer.getvalue()) == pytest.approx(1.0)

    def test_bad_wav_returns_zero(self):
        assert wav_duration_seconds(b"not a wav") == 0.0
