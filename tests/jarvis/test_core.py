"""Tests for the core plumbing: config, retry, cache, sentence splitting."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from jarvis.cache import LRUCache, TTLCache
from jarvis.config import Settings
from jarvis.net import first_speakable_chunk, split_sentences
from jarvis.retry import (
    ProviderError,
    RateLimited,
    RetryPolicy,
    classify,
    is_retryable,
    with_retry,
)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


class TestConfig:
    """Settings load from the environment and never leak secrets."""

    def test_secrets_are_not_in_the_log_view(self):
        settings = Settings(groq_api_key="sk-super-secret-value")
        rendered = repr(settings.redacted())
        assert "sk-super-secret-value" not in rendered
        assert settings.groq_configured is True

    def test_missing_key_is_reported_not_raised(self):
        assert Settings(groq_api_key="").groq_configured is False

    def test_env_overrides_defaults(self, monkeypatch):
        monkeypatch.setenv("TTS_VOICE", "en-GB-SoniaNeural")
        assert Settings().tts_voice == "en-GB-SoniaNeural"

    def test_invalid_sample_rate_is_rejected(self):
        with pytest.raises(ValueError):
            Settings(sample_rate=44100)

    def test_reply_cache_can_be_disabled(self):
        assert Settings(llm_cache_max=0).llm_cache_max == 0


# ----------------------------------------------------------------------
# Retry
# ----------------------------------------------------------------------


class TestRetry:
    """Backoff, classification and the retry budget."""

    def test_backoff_grows_and_is_capped(self):
        policy = RetryPolicy(base=1.0, maximum=4.0, jitter=0.0)
        assert policy.delay_for(1) == 1.0
        assert policy.delay_for(2) == 2.0
        assert policy.delay_for(3) == 4.0
        assert policy.delay_for(9) == 4.0

    def test_jitter_stays_within_bounds(self):
        policy = RetryPolicy(base=1.0, maximum=10.0, jitter=0.5)
        for attempt in range(1, 6):
            nominal = min(1.0 * 2 ** (attempt - 1), 10.0)
            delay = policy.delay_for(attempt)
            assert nominal * 0.5 <= delay <= nominal * 1.5

    def test_succeeds_after_transient_failures(self):
        attempts = {"n": 0}

        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ConnectError("boom")
            return "ok"

        policy = RetryPolicy(attempts=4, base=0.001, jitter=0.0)
        assert asyncio.run(with_retry(flaky, policy=policy, label="t")) == "ok"
        assert attempts["n"] == 3

    def test_gives_up_and_classifies(self):
        async def always_fails():
            raise httpx.HTTPStatusError(
                "429",
                request=httpx.Request("POST", "https://x"),
                response=httpx.Response(429),
            )

        policy = RetryPolicy(attempts=2, base=0.001, jitter=0.0)
        with pytest.raises(RateLimited):
            asyncio.run(with_retry(always_fails, policy=policy, label="t"))

    def test_non_retryable_fails_immediately(self):
        calls = {"n": 0}

        async def bad_request():
            calls["n"] += 1
            raise httpx.HTTPStatusError(
                "400",
                request=httpx.Request("POST", "https://x"),
                response=httpx.Response(400),
            )

        policy = RetryPolicy(attempts=5, base=0.001, jitter=0.0)
        with pytest.raises(ProviderError):
            asyncio.run(with_retry(bad_request, policy=policy, label="t"))
        assert calls["n"] == 1, "a 400 must not be retried"

    def test_429_is_retryable_but_400_is_not(self):
        def status(code: int) -> httpx.HTTPStatusError:
            return httpx.HTTPStatusError(
                "x",
                request=httpx.Request("POST", "https://x"),
                response=httpx.Response(code),
            )

        assert is_retryable(status(429)) is True
        assert is_retryable(status(503)) is True
        assert is_retryable(status(400)) is False
        assert is_retryable(status(401)) is False

    def test_classify_maps_429_to_rate_limited(self):
        exc = httpx.HTTPStatusError(
            "x",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(429),
        )
        assert isinstance(classify(exc), RateLimited)

    def test_timeout_budget_bounds_total_time(self):
        async def slow():
            await asyncio.sleep(0.05)
            raise httpx.ReadTimeout("slow")

        policy = RetryPolicy(attempts=10, base=0.2, jitter=0.0)
        started = time.monotonic()
        with pytest.raises(ProviderError):
            asyncio.run(with_retry(slow, policy=policy, label="t", timeout=0.25))
        assert time.monotonic() - started < 1.0


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


class TestCaches:
    """TTL expiry and LRU eviction."""

    def test_ttl_expires(self):
        cache = TTLCache(max_size=4, ttl=0.05)
        cache.set("k", "v")
        assert cache.get("k") == "v"
        time.sleep(0.07)
        assert cache.get("k") is None

    def test_ttl_evicts_oldest_when_full(self):
        cache = TTLCache(max_size=2, ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.get("a") is None
        assert cache.get("c") == 3

    def test_ttl_hit_refreshes_recency(self):
        cache = TTLCache(max_size=2, ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")
        cache.set("c", 3)
        assert cache.get("a") == 1, "recently used entries must survive"
        assert cache.get("b") is None

    def test_lru_evicts_least_recently_used(self):
        cache = LRUCache(max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")
        cache.set("c", 3)
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3

    def test_lru_tracks_hits_and_misses(self):
        cache = LRUCache(max_size=2)
        cache.set("a", 1)
        cache.get("a")
        cache.get("missing")
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_invalidate_removes_one_key(self):
        cache = LRUCache(max_size=4)
        cache.set("a", 1)
        assert cache.invalidate("a") is True
        assert cache.get("a") is None

    def test_rejects_invalid_sizes(self):
        with pytest.raises(ValueError):
            TTLCache(max_size=0)
        with pytest.raises(ValueError):
            LRUCache(max_size=0)


# ----------------------------------------------------------------------
# Sentence buffering
# ----------------------------------------------------------------------


class TestSentenceSplitting:
    """Deciding when speech can safely begin."""

    def test_splits_on_terminal_punctuation(self):
        text = "Bitcoin is up two percent. That is the short version, sir."
        assert len(split_sentences(text)) == 2

    def test_keeps_abbreviations_intact(self):
        assert split_sentences("Dr. Smith called at 5 p.m. about the invoice") == [
            "Dr. Smith called at 5 p.m. about the invoice"
        ]

    def test_keeps_decimals_intact(self):
        # "3.5" must not be treated as a sentence boundary.
        parts = split_sentences("It trades at 3.5 million right now. Volatile.")
        assert parts[0].startswith("It trades at 3.5 million right now.")

    def test_no_text_is_ever_lost(self):
        text = (
            "It trades at 3.5 million. Worth noting, sir. Yes. "
            "And that is the whole picture today."
        )
        assert " ".join(split_sentences(text)).split() == text.split()

    def test_merges_short_fragments_forward(self):
        # "Hi." alone is too short to speak, so it joins the next sentence.
        assert len(split_sentences("Hi. The meeting is at three tomorrow.")) == 1

    def test_first_chunk_waits_for_enough_text(self):
        assert first_speakable_chunk("Bitcoin is", min_chars=60)[0] is None

    def test_first_chunk_returns_chunk_and_remainder(self):
        text = (
            "Bitcoin is trading around eighty four thousand dollars today. "
            "It rose two percent over the last day."
        )
        chunk, remainder = first_speakable_chunk(text, min_chars=30)
        assert chunk is not None
        assert chunk.endswith("today.")
        assert remainder.startswith("It rose")


class TestSettingsReload:
    """Configuration can be rebuilt after the environment changes."""

    def test_reload_picks_up_new_environment(self, monkeypatch):
        from jarvis.config import reload_settings

        monkeypatch.setenv("TTS_VOICE", "en-GB-SoniaNeural")
        assert reload_settings().tts_voice == "en-GB-SoniaNeural"
        monkeypatch.setenv("TTS_VOICE", "en-US-JennyNeural")
        assert reload_settings().tts_voice == "en-US-JennyNeural"
