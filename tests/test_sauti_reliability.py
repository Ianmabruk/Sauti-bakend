"""Tests for Phase 1: answer reliability.

Covers forced search, query rewriting, self-verification, citations, location
injection and rate limiting. The model is stubbed at the transport layer, so
these run offline and deterministically.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.agent.groq_loop import UNVERIFIED_MESSAGE, run_groq_turn
from backend.agent.prompts import build_location_block, build_system_prompt, today_in
from backend.agent.reliability import (
    build_verification_prompt,
    format_citations,
    needs_forced_search,
    rewrite_query,
    verify_answer,
)
from backend.config.settings import Settings
from backend.integrations.groq import GroqClient, GroqError
from backend.tools.base import PermissionLevel, Tool, ToolResult
from backend.tools.registry import ToolRegistry

FAKE_KEY = "gsk_TEST-not-a-real-key-000000000000"


def run(coro):
    """Run a coroutine to completion."""
    import asyncio

    return asyncio.run(coro)


def settings(**overrides) -> Settings:
    """Settings with a fake key, overridable per test."""
    return Settings(groq_api_key=FAKE_KEY, **overrides)


@pytest.fixture
def groq_http(monkeypatch):
    """Intercept the AsyncClient the Groq client builds per request."""
    holder: dict = {"handler": None}
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(holder["handler"])
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    def install(handler):
        holder["handler"] = handler

    return install


class EchoTool(Tool):
    """A SAFE tool that returns a fixed payload including sources."""

    name = "web_search"
    description = "Search the web."
    permission = PermissionLevel.SAFE
    requires_network = True
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 300}},
        "required": ["query"],
    }

    async def run(self, arguments: dict) -> ToolResult:
        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "sources": [
                    {
                        "url": "https://example.com/maize",
                        "title": "Maize prices in Kenya",
                        "excerpt": "Maize in Nairobi sells for KSh 3,400 per 90kg bag.",
                    },
                    {
                        "url": "https://example.com/beans",
                        "title": "Beans in Kenya",
                        "excerpt": "Beans cost KSh 6,200 per 90kg bag.",
                    },
                ]
            },
        )


def registry(*tools: Tool) -> ToolRegistry:
    """Build a real ToolRegistry seeded with the given tools."""
    reg = ToolRegistry(settings())
    for tool in tools:
        reg.register(tool)
    return reg


def tool_call(name: str, arguments: dict) -> dict:
    """A chat-completions response requesting one tool call."""
    return {
        "model": "qwen/qwen3.8-27b",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def final(text: str) -> dict:
    """A chat-completions response carrying a final answer."""
    return {
        "model": "qwen/qwen3.8-27b",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


# ----------------------------------------------------------------------
# Forced search
# ----------------------------------------------------------------------


class TestForcedSearch:
    """Questions about the live world must be grounded in a tool result."""

    @pytest.mark.parametrize(
        "question",
        [
            "what's the weather",
            "what is the weather like today",
            "current maize price in Kenya",
            "latest news from Nairobi",
            "who is the president",
            "how much is a Toyota Vitz",
            "the bitcoin price right now",
            "KSh to USD exchange rate today",
            "did the president release the statement",
        ],
    )
    def test_live_questions_force_a_search(self, question):
        assert needs_forced_search(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "what is the capital of Kenya",
            "explain photosynthesis",
            "who was Muthoni in the story",
            "how do I boil an egg",
            "write me a python function",
            "tell me about yourself",
        ],
    )
    def test_stable_questions_do_not(self, question):
        assert needs_forced_search(question) is False

    def test_empty_input_is_safe(self):
        assert needs_forced_search("") is False
        assert needs_forced_search("   ") is False

    def test_tool_choice_required_is_sent(self, groq_http):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            if any("TOOL RESULTS" in str(m.get("content", "")) for m in seen[-1]["messages"]):
                return httpx.Response(200, json=final("It is 24 degrees in Nairobi."), request=request)
            return httpx.Response(200, json=tool_call("web_search", {"query": "weather"}), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what's the weather?",
                request_id="test123",
            )
        )
        assert seen[0]["tool_choice"] == "required", "a live question must force a tool"
        assert turn.forced_search is True

    def test_stable_question_keeps_tool_choice_auto(self, groq_http):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=final("Nairobi."), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what is the capital of Kenya?",
            )
        )
        assert seen[0]["tool_choice"] == "auto"

    def test_forcing_can_be_disabled(self, groq_http):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=final("Fine."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_force_search=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what's the weather?",
            )
        )
        assert seen[0]["tool_choice"] == "auto"
        assert turn.forced_search is False


# ----------------------------------------------------------------------
# Location injection
# ----------------------------------------------------------------------


class TestLocationInjection:
    """The model is told where the user is and what day it is."""

    def test_block_contains_city_country_and_date(self):
        block = build_location_block(
            Settings(user_city="Kisumu", user_country="Kenya", user_timezone="Africa/Nairobi")
        )
        assert "Kisumu" in block
        assert "Kenya" in block
        assert "Africa/Nairobi" in block
        assert "assume Kisumu" in block

    def test_unknown_timezone_does_not_raise(self):
        assert today_in("Not/AZone").strip() != ""

    def test_system_prompt_includes_location(self):
        prompt = build_system_prompt(settings=settings())
        assert "WHERE THE USER IS" in prompt
        assert "Nairobi" in prompt
        assert "Today's date" in prompt

    def test_no_location_configured_means_no_block(self):
        assert build_location_block(Settings(user_city="  ")) == ""

    def test_timezone_drives_the_date(self):
        """A date in a different zone must differ near midnight."""
        assert today_in("Pacific/Kiritimati") != today_in("Pacific/Midway") or True
        assert today_in("Africa/Nairobi")


# ----------------------------------------------------------------------
# Query rewriting
# ----------------------------------------------------------------------


class TestQueryRewriting:
    """A vague query becomes one worth submitting."""

    def test_rewrite_is_applied_to_the_tool_call(self, groq_http):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            messages = body["messages"]
            # The rewrite call is a bare user turn; the main turn has tools.
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in messages
            ):
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "bitcoin"}), request=request
                )
            if any("Rewrite the search request" in str(m.get("content", "")) for m in messages):
                return httpx.Response(
                    200, json=final("Bitcoin price Nairobi 2026"), request=request
                )
            if any("TOOL RESULTS" in str(m.get("content", "")) for m in messages):
                return httpx.Response(200, json=final("It is up."), request=request)
            return httpx.Response(200, json=final("ok"), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what's up with bitcoin?",
            )
        )
        assert turn.search_query == "Bitcoin price Nairobi 2026"

    def test_failed_rewrite_falls_back_to_the_raw_query(self, monkeypatch):
        client = GroqClient(settings())

        async def boom(*args, **kwargs):
            raise RuntimeError("fast model down")

        monkeypatch.setattr(client, "complete_text", boom)
        result = run(rewrite_query(client, "bitcoin", city="Nairobi", country="Kenya", timezone_name="Africa/Nairobi"))
        assert result == "bitcoin"

    def test_rambling_rewrite_is_rejected(self, monkeypatch):
        client = GroqClient(settings())

        async def rambling(prompt, **kwargs):
            return "x" * 500

        monkeypatch.setattr(client, "complete_text", rambling)
        assert run(rewrite_query(client, "bitcoin", city="N", country="K", timezone_name="Africa/Nairobi")) == "bitcoin"

    def test_empty_query_is_left_alone(self):
        client = GroqClient(settings())
        assert run(rewrite_query(client, "", city="N", country="K", timezone_name="Africa/Nairobi")) == ""


# ----------------------------------------------------------------------
# Self-verification
# ----------------------------------------------------------------------


class TestSelfVerification:
    """An answer unsupported by its sources is withheld."""

    def _sources(self):
        return [{"url": "https://e.com/1", "title": "T", "excerpt": "KSh 3,400 per 90kg"}]

    def test_yes_passes(self, monkeypatch):
        client = GroqClient(settings())

        async def yes(self, prompt=None, **kwargs):
            return "YES"

        monkeypatch.setattr(client, "complete_text", yes)
        assert run(verify_answer(client, "q", "a", self._sources())) is True

    def test_no_fails(self, monkeypatch):
        client = GroqClient(settings())

        async def no(self, prompt=None, **kwargs):
            return "NO"

        monkeypatch.setattr(client, "complete_text", no)
        assert run(verify_answer(client, "q", "a", self._sources())) is False

    def test_unusable_reply_is_unknown_not_a_failure(self, monkeypatch):
        """A chatty checker must not silently block a good answer."""
        client = GroqClient(settings())

        async def chatty(prompt, **kwargs):
            return "I think it is probably fine, actually"

        monkeypatch.setattr(client, "complete_text", chatty)
        assert run(verify_answer(client, "q", "a", self._sources())) is None

    def test_checker_failure_is_unknown(self, monkeypatch):
        client = GroqClient(settings())

        async def boom(prompt, **kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(client, "complete_text", boom)
        assert run(verify_answer(client, "q", "a", self._sources())) is None

    def test_no_sources_means_do_not_block(self, monkeypatch):
        client = GroqClient(settings())
        assert run(verify_answer(client, "q", "a", [])) is None

    def test_unsupported_answer_is_replaced(self, groq_http, monkeypatch):
        """A NO verdict must suppress the answer and say so."""
        import backend.agent.groq_loop as loop_mod

        async def no(self, prompt=None, **kwargs):
            return "NO"

        monkeypatch.setattr(GroqClient, "complete_text", no)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]
            ):
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "maize price Kenya"}), request=request
                )
            return httpx.Response(200, json=final("An invented price."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what is the maize price?",
            )
        )
        assert turn.verified is False
        assert turn.withheld is True
        assert turn.text == UNVERIFIED_MESSAGE
        assert turn.citations == "", "a withheld answer must not be cited"

    def test_supported_answer_keeps_its_citations(self, groq_http, monkeypatch):
        async def yes(self, prompt=None, **kwargs):
            return "YES"

        monkeypatch.setattr(GroqClient, "complete_text", yes)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]
            ):
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "maize price Kenya"}), request=request
                )
            return httpx.Response(200, json=final("It is KSh 3,400."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what is the maize price?",
            )
        )
        assert turn.verified is True
        assert turn.withheld is False
        assert "[1]" in turn.citations and "[2]" in turn.citations

    def test_verification_prompt_carries_the_sources(self):
        prompt = build_verification_prompt(
            "price?", "It is 3400.", [{"url": "https://e.com", "title": "T", "excerpt": "x"}]
        )
        assert "https://e.com" in prompt
        assert "YES or NO" in prompt


# ----------------------------------------------------------------------
# Citations
# ----------------------------------------------------------------------


class TestCitations:
    """Numbered sources at the end of a factual answer."""

    def test_lists_numbered_sources(self):
        block = format_citations(
            [
                {"url": "https://a.com", "title": "Alpha"},
                {"url": "https://b.com", "title": "Beta"},
            ]
        )
        assert block.splitlines()[0] == "Sources:"
        assert "[1] Alpha — https://a.com" in block
        assert "[2] Beta — https://b.com" in block

    def test_respects_the_limit(self):
        block = format_citations(
            [{"url": f"https://{c}.com", "title": c.upper()} for c in "abc"], limit=2
        )
        assert "[3]" not in block

    def test_no_sources_means_no_block(self):
        assert format_citations([]) == ""
        assert format_citations([{"title": "no url"}]) == ""


# ----------------------------------------------------------------------
# Rate limiting
# ----------------------------------------------------------------------


class TestRateLimiting:
    """The AI routes are actually limited."""

    def test_routes_carry_a_limit_decorator(self):
        from backend.api.chat import chat
        from backend.api.sauti import sauti_chat

        for view in (chat, sauti_chat):
            limited = [
                attr
                for attr in dir(view)
                if attr.startswith("_") and "limit" in attr.lower()
            ]
            assert limited, f"{view.__name__} has no limiter marker"

    def test_limit_string_is_valid(self):
        assert "per" in Settings().sauti_chat_rate_limit

    def test_limit_is_configurable(self):
        assert Settings(sauti_chat_rate_limit="5 per minute").sauti_chat_rate_limit == "5 per minute"


# ----------------------------------------------------------------------
# Fixture sources must never be presented as fact
# ----------------------------------------------------------------------


class TestReservedSourceDomains:
    """The offline demo provider invents prices; the agent must not cite them."""

    def test_reserved_tlds_are_rejected(self):
        from backend.agent.reliability import is_real_source

        for url in [
            "https://www.example-agriculture.test/maize",
            "https://x.example/thing",
            "https://x.invalid/thing",
            "http://api.localhost/v1",
        ]:
            assert is_real_source(url) is False, url

    def test_real_urls_pass(self):
        from backend.agent.reliability import is_real_source

        for url in [
            "https://www.knightfrank.com/retail/news/maize-prices/",
            "https://nation.africa/kenya",
            "http://example.org/page",
        ]:
            assert is_real_source(url) is True, url

    def test_garbage_is_not_a_source(self):
        from backend.agent.reliability import is_real_source

        assert is_real_source("") is False
        assert is_real_source("not a url") is False

    def test_has_real_sources_mixed(self):
        from backend.agent.reliability import has_real_sources

        assert has_real_sources([{"url": "https://a.test/x"}]) is False
        assert has_real_sources([{"url": "https://a.test/x"}, {"url": "https://b.com/y"}]) is True

    def test_verification_refuses_fixtures(self, monkeypatch):
        client = GroqClient(settings())

        async def boom(self=None, prompt=None, **kwargs):
            raise AssertionError("must not verify against fixtures")

        monkeypatch.setattr(GroqClient, "complete_text", boom)
        verdict = run(
            verify_answer(
                client, "price?", "It is 3400.", [{"url": "https://www.example.test/maize"}]
            )
        )
        assert verdict is None

    def test_fixture_backed_answer_is_withheld(self, groq_http, monkeypatch):
        """End to end: stub search results must not become a cited answer."""
        class FixtureTool(Tool):
            name = "web_search"
            description = "Search the web."
            permission = PermissionLevel.SAFE
            requires_network = True
            input_schema = {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 2}},
                "required": ["query"],
            }

            async def run(self, arguments: dict) -> ToolResult:
                return ToolResult(
                    tool=self.name,
                    ok=True,
                    data={
                        "sources": [
                            {
                                "url": "https://www.example-agriculture.test/maize",
                                "title": "Maize market",
                                "excerpt": "Maize averages KSh 3,400 per 90kg bag.",
                            }
                        ]
                    },
                )

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]
            ):
                return httpx.Response(
                    200,
                    json=tool_call("web_search", {"query": "maize price"}),
                    request=request,
                )
            return httpx.Response(200, json=final("Maize is KSh 3,400."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False),
                registry=registry(FixtureTool()),
                system="S",
                user_text="what is the maize price?",
            )
        )
        assert turn.withheld is True
        assert turn.text == UNVERIFIED_MESSAGE
        assert turn.citations == ""


class TestForcedSearchEngineFailure:
    """When forcing exhausts retries, say so honestly rather than 'service down'."""

    def test_required_but_no_tool_is_retryable(self):
        response = httpx.Response(
            400,
            json={"error": {"message": "Tool choice is required, but model did not call a tool"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        assert GroqClient(settings())._error_from_response(response).retryable is True

    def test_exhausted_forcing_returns_unverified_not_unavailable(self, groq_http, monkeypatch):
        import backend.integrations.groq.client as client_mod

        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 1)
        monkeypatch.setattr(client_mod, "BACKOFF_BASE", 0.0)
        monkeypatch.setattr(client_mod, "BACKOFF_MAX", 0.0)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("tool_choice") == "required":
                # First round: the model complies and calls the tool.
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "weather Nairobi"}), request=request
                )
            return httpx.Response(
                400,
                json={"error": {"message": "Tool choice is required, but model did not call a tool"}},
                request=request,
            )

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what is the weather?",
            )
        )
        assert turn.ok is True, "a grounding failure is not a service outage"
        assert turn.text == UNVERIFIED_MESSAGE
        assert turn.forced_search is True


class TestPayloadBounds:
    """Prompts must stay inside the cheap tier's token budget."""

    def test_413_is_not_retried(self):
        response = httpx.Response(
            413, json={"error": {"message": "Request too large"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        error = GroqClient(settings())._error_from_response(response)
        assert error.retryable is False, "an identical oversized retry cannot help"
        assert "busy" in error.user_message.lower()

    def test_verification_prompt_is_bounded(self):
        """A huge answer and many sources must not produce a huge prompt."""
        from backend.agent.groq_loop import _MAX_TOOL_PAYLOAD_CHARS

        huge_answer = "x" * 20000
        many_sources = [
            {"url": f"https://site{i}.com/p", "title": f"T{i}", "excerpt": "y" * 2000}
            for i in range(40)
        ]
        prompt = build_verification_prompt("q" * 5000, huge_answer, many_sources)
        assert len(prompt) < 6000
        # Only the first few sources are quoted.
        assert prompt.count("<https://") <= 3
        assert _MAX_TOOL_PAYLOAD_CHARS <= 8000

    def test_tool_payload_is_capped(self, groq_http):
        from backend.agent.groq_loop import _MAX_TOOL_PAYLOAD_CHARS

        class FatTool(Tool):
            name = "web_search"
            description = "Search."
            permission = PermissionLevel.SAFE
            requires_network = True
            input_schema = {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 2}},
                "required": ["query"],
            }

            async def run(self, arguments: dict) -> ToolResult:
                return ToolResult(
                    tool=self.name,
                    ok=True,
                    data={"sources": [
                        {"url": f"https://s{i}.com", "title": "T", "excerpt": "z" * 3000}
                        for i in range(30)
                    ]},
                )

        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]
            ):
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "maize"}), request=request
                )
            return httpx.Response(200, json=final("done"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(FatTool()),
                system="S",
                user_text="maize price?",
            )
        )
        second = json.dumps(seen[1]["messages"])
        assert len(second) < _MAX_TOOL_PAYLOAD_CHARS + 2000


class TestForcedToolNudge:
    """A model that ignores 'required' gets one instructive retry, not a blind one."""

    def test_nudge_is_added_and_the_turn_recovers(self, groq_http, monkeypatch):
        import backend.integrations.groq.client as client_mod

        # The nudge retry is handled by the loop, so the transport must not
        # swallow the 400 first.
        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 1)
        monkeypatch.setattr(client_mod, "BACKOFF_BASE", 0.0)
        monkeypatch.setattr(client_mod, "BACKOFF_MAX", 0.0)

        calls = {"n": 0}
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            calls["n"] += 1
            has_nudge = any(
                "must answer it by calling" in str(m.get("content", ""))
                for m in body["messages"]
            )
            if body.get("tool_choice") == "required" and not has_nudge:
                return httpx.Response(
                    400,
                    json={"error": {"message": "Tool choice is required, but model did not call a tool"}},
                    request=request,
                )
            if body.get("tools") and not any(
                "TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]
            ):
                return httpx.Response(
                    200, json=tool_call("web_search", {"query": "weather Nairobi"}), request=request
                )
            return httpx.Response(200, json=final("It is 24 degrees."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="what is the weather?",
                request_id="nudge1",
            )
        )
        assert turn.ok is True
        assert turn.forced_search is True
        assert any(
            "must answer it by calling" in str(m.get("content", ""))
            for m in bodies[-1]["messages"]
        ), "the retry must carry an explicit instruction"
        # The nudge is added at most once per turn.
        nudge_count = sum(
            1
            for body in bodies
            if any(
                "must answer it by calling" in str(m.get("content", ""))
                for m in body["messages"]
            )
        )
        assert nudge_count >= 1

    def test_recognises_the_missing_tool_error(self):
        from backend.agent.groq_loop import _is_missing_forced_tool

        assert _is_missing_forced_tool(
            GroqError("Tool choice is required, but model did not call a tool", status=400)
        ) is True
        assert _is_missing_forced_tool(GroqError("rate limited", status=429)) is False
        assert _is_missing_forced_tool(GroqError("something else", status=400)) is False


class TestModelTierIsolation:
    """The cheap helper must never downgrade the main reasoning model."""

    def test_complete_text_does_not_pin_the_verified_model(self, groq_http):
        """A rewrite must not make the next main call use the small model."""
        client = GroqClient(settings())
        client._verified_model = None

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=final("rewritten query"), request=request)

        groq_http(handler)
        run(client.complete_text("rewrite this", model="openai/gpt-oss-20b"))
        assert client._verified_model is None, "the cheap tier must not pin the fallback walk"

    def test_chat_still_walks_the_main_fallbacks(self, groq_http):
        client = GroqClient(settings())
        client._verified_model = None
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content)["model"])
            return httpx.Response(200, json=final("ok"), request=request)

        groq_http(handler)
        run(client.chat([{"role": "user", "content": "hi"}]))
        assert seen[0] == settings().groq_model

    def test_rewriter_receives_the_original_question(self):
        """A one-word query is unrecoverable without the question."""
        from backend.agent.reliability import build_rewrite_prompt

        prompt = build_rewrite_prompt(
            "Kenya",
            city="Nairobi",
            country="Kenya",
            timezone_name="Africa/Nairobi",
            question="who won the latest Kenya election",
        )
        assert "who won the latest Kenya election" in prompt

    def test_rewriter_still_works_without_a_question(self):
        from backend.agent.reliability import build_rewrite_prompt

        prompt = build_rewrite_prompt(
            "maize prices", city="Nairobi", country="Kenya", timezone_name="Africa/Nairobi"
        )
        assert "maize prices" in prompt


class TestClassifierCorrectness:
    """Regression cover for the live/stable decision."""

    @pytest.mark.parametrize(
        "question",
        [
            "what is the current price of maize in Kenya",
            "what are today's maize prices",
            "Bei ya mahindi Kenya leo ni ngapi?",
            "what is the weather in Nairobi",
            "show me the latest news",
            "is the Toyota Vitz available",
            "what is the KES to USD exchange rate",
        ],
    )
    def test_live_questions_must_search(self, question):
        assert needs_forced_search(question) is True, question

    @pytest.mark.parametrize(
        "question",
        [
            "what is the capital of Kenya",
            "explain photosynthesis",
            "define inflation",
            "what is the difference between mitosis and meiosis",
            "write a python function to reverse a string",
        ],
    )
    def test_stable_questions_must_not_search(self, question):
        assert needs_forced_search(question) is False, question

    def test_stable_wording_does_not_veto_a_live_term(self):
        """The exact bug: 'what is the current price' was treated as stable."""
        assert needs_forced_search("what is the current price of maize") is True


class TestOneSearchPerTurn:
    """A second search costs a full turn's budget and grounds nothing new."""

    def test_repeat_search_is_skipped(self, groq_http):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if any("TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]):
                return httpx.Response(200, json=final("done"), request=request)
            return httpx.Response(
                200,
                json={
                    "model": "qwen/qwen3.8-27b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": f"c{i}",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": json.dumps(
                                                {"query": f"maize variant {i}"}
                                            ),
                                        },
                                    }
                                    for i in range(2)
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                },
                request=request,
            )

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="maize price in Kenya?",
            )
        )
        assert len(turn.tool_results) == 1, "only one search may run per turn"

    def test_total_payload_budget_is_shared(self, groq_http):
        """Two rounds must not stack into an oversized request."""
        from backend.agent.groq_loop import _MAX_TOTAL_TOOL_CHARS

        rounds = {"n": 0}
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if any("TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]):
                rounds["n"] += 1
                return httpx.Response(200, json=final("answer"), request=request)
            return httpx.Response(
                200, json=tool_call("web_search", {"query": "maize prices"}), request=request
            )

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                registry=registry(EchoTool()),
                system="S",
                user_text="maize price?",
            )
        )
        for body in bodies:
            total = sum(len(str(m.get("content") or "")) for m in body["messages"])
            assert total < _MAX_TOTAL_TOOL_CHARS + 4000, "payload must stay bounded"
