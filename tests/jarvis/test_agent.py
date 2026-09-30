"""Tests for memory, persona and the agent's reliability guarantees."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis.agent import JarvisAgent
from jarvis.config import Settings
from jarvis.memory import ConversationMemory, Turn
from jarvis.persona import (
    build_evidence_block,
    format_citation_line,
    json_only,
    strip_citations,
)
from jarvis.providers.llm import ProviderReply
from jarvis.retry import ProviderError

from .conftest import StubLLM, StubSearch, failed_search, good_search


def run(coro):
    """Run a coroutine to completion."""
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Memory
# ----------------------------------------------------------------------


class TestMemory:
    """The sliding window and the rolling summary."""

    def test_window_is_bounded(self, settings: Settings):
        memory = ConversationMemory(settings)
        for index in range(25):
            memory.add("user", f"message {index}")
        assert len(memory.turns) == settings.memory_window

    def test_window_keeps_the_newest_turns(self, settings: Settings):
        memory = ConversationMemory(settings)
        for index in range(15):
            memory.add("user", f"message {index}")
        assert memory.turns[-1].content == "message 14"
        assert "message 14" in memory.context_block()

    def test_rejects_unknown_roles(self, settings: Settings):
        memory = ConversationMemory(settings)
        with pytest.raises(ValueError):
            memory.add("wizard", "abracadabra")

    def test_save_and_load_round_trip(self, settings: Settings, tmp_path: Path):
        path = tmp_path / "state.json"
        memory = ConversationMemory(settings)
        memory.add("user", "My preferred language is Kiswahili.")
        memory.add("assistant", "Noted, sir.")
        memory.summary = "The user prefers Kiswahili."
        assert memory.save(str(path)) is True

        restored = ConversationMemory(settings)
        assert restored.load(str(path)) is True
        assert restored.summary == "The user prefers Kiswahili."
        assert [t.content for t in restored.turns] == [
            "My preferred language is Kiswahili.",
            "Noted, sir.",
        ]

    def test_saved_file_is_valid_json(self, settings: Settings, tmp_path: Path):
        path = tmp_path / "state.json"
        memory = ConversationMemory(settings)
        memory.add("user", "hello")
        memory.save(str(path))
        assert json.loads(path.read_text())["turns"][0]["content"] == "hello"

    def test_corrupt_file_is_ignored(self, settings: Settings, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json")
        memory = ConversationMemory(settings)
        assert memory.load(str(path)) is False
        assert memory.turns == []

    def test_missing_file_is_not_an_error(self, settings: Settings, tmp_path: Path):
        memory = ConversationMemory(settings)
        assert memory.load(str(tmp_path / "absent.json")) is False

    def test_summary_refresh_compacts_evicted_turns(self, settings: Settings):
        calls: list[tuple] = []

        async def summariser(previous: str, turns: list[Turn]) -> str:
            calls.append((previous, turns))
            return "SUMMARY: the user is asking about prices."

        memory = ConversationMemory(settings, summariser=summariser)
        for index in range(20):
            memory.add("user", f"turn {index}")

        assert run(memory.refresh_summary(force=True)) is True
        assert memory.summary.startswith("SUMMARY:")
        assert calls, "the summariser must actually be called"

    def test_failed_summariser_leaves_memory_usable(self, settings: Settings):
        async def broken(previous: str, turns: list[Turn]) -> str:
            raise RuntimeError("model down")

        memory = ConversationMemory(settings, summariser=broken)
        for index in range(20):
            memory.add("user", f"turn {index}")
        assert run(memory.refresh_summary(force=True)) is False
        assert len(memory.turns) == settings.memory_window

    def test_context_block_includes_both_parts(self, settings: Settings):
        memory = ConversationMemory(settings)
        memory.summary = "Earlier: the user lives in Nairobi."
        memory.add("user", "And the weather?")
        block = memory.context_block()
        assert "Nairobi" in block
        assert "And the weather?" in block


# ----------------------------------------------------------------------
# Persona helpers
# ----------------------------------------------------------------------


class TestPersona:
    """JSON extraction and spoken-text hygiene."""

    def test_parses_bare_json(self):
        assert json_only('{"needs_search": true, "query": "x"}')["needs_search"] is True

    def test_parses_fenced_json(self):
        text = '```json\n{"needs_search": false, "query": ""}\n```'
        assert json_only(text)["needs_search"] is False

    def test_parses_json_wrapped_in_prose(self):
        text = 'Sure! Here you go: {"needs_search": true, "query": "btc"} done.'
        assert json_only(text)["query"] == "btc"

    def test_handles_braces_inside_strings(self):
        text = '{"query": "a } brace", "needs_search": true}'
        assert json_only(text)["query"] == "a } brace"

    def test_returns_none_for_prose_only(self):
        assert json_only("I think you should search for it.") is None
        assert json_only("") is None

    def test_strips_citations_and_markup(self):
        cleaned = strip_citations("Bitcoin is up [1] **today**, see https://x.com/page")
        assert "[" not in cleaned and "**" not in cleaned
        assert "https" not in cleaned
        assert "Bitcoin is up" in cleaned

    def test_citation_line_uses_at_most_two(self):
        line = format_citation_line(
            [
                {"title": "A", "url": "https://a"},
                {"title": "B", "url": "https://b"},
                {"title": "C", "url": "https://c"},
            ]
        )
        assert "A" in line and "B" in line and "C" not in line

    def test_evidence_block_marks_failures(self):
        block = build_evidence_block([{"tool": "web_search", "ok": False, "summary": "timeout"}])
        assert "FAILED" in block and "timeout" in block

    def test_evidence_block_includes_excerpts(self):
        block = build_evidence_block(
            [{"tool": "web_search", "ok": True, "summary": "s", "sources": good_search().sources}]
        )
        assert "84,131.65" in block


# ----------------------------------------------------------------------
# Agent behaviour
# ----------------------------------------------------------------------


def make_agent(settings: Settings, llm: StubLLM, search_result) -> JarvisAgent:
    """Build an agent wired to stubs."""
    agent = JarvisAgent(settings, llm=llm, search=StubSearch(search_result))
    agent.speaker = None  # speech is exercised separately
    return agent


class TestAgentRouting:
    """The model decides; nothing is hardcoded."""

    def test_routes_to_search_when_the_model_says_so(self, settings: Settings):
        llm = StubLLM('{"needs_search": true, "query": "Bitcoin price USD today"}')
        agent = make_agent(settings, llm, good_search())
        result = run(agent.ask("what's up with bitcoin"))
        assert result.searched is True
        assert result.used_tool == "web_search"

    def test_rewrites_the_query_before_searching(self, settings: Settings):
        llm = StubLLM('{"needs_search": true, "query": "Bitcoin price USD today"}')
        search = StubSearch(good_search())
        agent = JarvisAgent(settings, llm=llm, search=search)
        run(agent.ask("what's up with bitcoin"))
        assert search.calls == ["Bitcoin price USD today"]

    def test_skips_search_when_not_needed(self, settings: Settings):
        llm = StubLLM('{"needs_search": false, "query": ""}', "Photosynthesis is how plants make food.")
        search = StubSearch(good_search())
        agent = JarvisAgent(settings, llm=llm, search=search)
        result = run(agent.ask("explain photosynthesis"))
        assert result.searched is False
        assert search.calls == []

    def test_small_talk_never_searches(self, settings: Settings):
        llm = StubLLM('{"needs_search": true, "query": "hello reply"}', "At your service, sir.")
        search = StubSearch(good_search())
        agent = JarvisAgent(settings, llm=llm, search=search)
        result = run(agent.ask("hello"))
        assert result.searched is False
        assert search.calls == []

    def test_search_needs_a_query(self, settings: Settings):
        """A "search" decision with no query must not fire a request."""
        llm = StubLLM('{"needs_search": true, "query": ""}')
        search = StubSearch(good_search())
        agent = JarvisAgent(settings, llm=llm, search=search)
        result = run(agent.ask("something"))
        assert result.searched is False
        assert search.calls == []

    def test_router_failure_answers_directly(self, settings: Settings):
        """A broken router must not break the turn."""
        llm = StubLLM("", "I can still answer, sir.", fail=ProviderError("down"))
        search = StubSearch(good_search())
        agent = JarvisAgent(settings, llm=llm, search=search)
        result = run(agent.ask("what is two plus two"))
        assert result.searched is False
        assert result.ok is False or result.reply


class TestAgentCandour:
    """Honest reporting when things go wrong."""

    def test_reports_when_search_fails(self, settings: Settings):
        llm = StubLLM(
            '{"needs_search": true, "query": "Bitcoin price USD today"}',
            "I couldn't verify this online, sir.",
        )
        agent = make_agent(settings, llm, failed_search())
        result = run(agent.ask("what's up with bitcoin"))
        assert result.searched is True
        assert result.offline_reason
        assert "couldn't verify" in result.reply

    def test_no_sources_means_no_citations(self, settings: Settings):
        llm = StubLLM('{"needs_search": true, "query": "x"}', "Nothing found, sir.")
        agent = make_agent(settings, llm, failed_search())
        result = run(agent.ask("x"))
        assert result.sources == []

    def test_citations_are_capped_at_two(self, settings: Settings):
        result = good_search()
        result.sources = result.sources * 3  # six sources available
        llm = StubLLM('{"needs_search": true, "query": "btc"}', "It is up, sir.")
        agent = make_agent(settings, llm, result)
        turn = run(agent.ask("btc price"))
        assert len(turn.sources) == 2

    def test_generation_failure_is_recorded_not_raised(self, settings: Settings):
        llm = StubLLM('{"needs_search": false, "query": ""}', fail=RuntimeError("model exploded"))
        agent = make_agent(settings, llm, good_search())
        result = run(agent.ask("hello"))
        assert result.error
        assert result.ok is False

    def test_empty_message_is_rejected(self, settings: Settings):
        llm = StubLLM("{}", "unused")
        agent = make_agent(settings, llm, good_search())
        result = run(agent.ask("   "))
        assert result.ok is False


class TestAgentMemory:
    """Follow-ups resolve against history."""

    def test_history_is_sent_to_the_model(self, settings: Settings):
        llm = StubLLM('{"needs_search": false, "query": ""}', "Sure thing, sir.")
        agent = make_agent(settings, llm, good_search())
        run(agent.ask("My name is Ian."))
        run(agent.ask("What did I just say?"))
        # The second turn's prompt must contain the earlier turn.
        assert any("My name is Ian" in prompt for prompt in llm.prompts)

    def test_memory_grows_across_turns(self, settings: Settings):
        llm = StubLLM('{"needs_search": false, "query": ""}', "Understood, sir.")
        agent = make_agent(settings, llm, good_search())
        for _ in range(3):
            run(agent.ask("tell me something"))
        assert len(agent.memory.turns) == 6  # three user + three assistant

    def test_context_is_bounded_by_the_window(self, settings: Settings):
        llm = StubLLM('{"needs_search": false, "query": ""}', "Noted, sir.")
        agent = make_agent(settings, llm, good_search())
        for _ in range(20):
            run(agent.ask("chatter"))
        assert len(agent.memory.turns) == settings.memory_window
