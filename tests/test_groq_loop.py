"""Tests for the Groq tool-calling loop and orchestrator wiring.

These cover the behaviour that makes Sauti honest: the model must route through
the existing tool registry, and an empty or failed tool result must produce an
honest answer rather than a fabricated one.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.agent.groq_loop import GroqTurn, run_groq_turn
from backend.config.settings import Settings
from backend.integrations.groq.client import GroqClient
from backend.tools.base import PermissionLevel, Tool, ToolResult
from backend.tools.registry import ToolRegistry

FAKE_KEY = "gsk_TEST-not-a-real-key-000000000000"


def run(coro):
    """Run a coroutine to completion."""
    import asyncio

    return asyncio.run(coro)


def settings(**overrides) -> Settings:
    """Settings with a fake Groq key, overridable per test."""
    return Settings(groq_api_key=FAKE_KEY, groq_model="qwen/qwen3.8-27b", **overrides)


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


# ----------------------------------------------------------------------
# Fake tools
# ----------------------------------------------------------------------


class EchoTool(Tool):
    """A tool that returns a fixed payload."""

    name = "echo"
    description = "Echo a value back."
    permission = PermissionLevel.SAFE
    requires_network = False
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string", "maxLength": 100}},
        "required": ["value"],
    }

    async def run(self, arguments: dict) -> ToolResult:
        return ToolResult(tool=self.name, ok=True, data={"echoed": arguments.get("value")})


class EmptyTool(Tool):
    """A tool that legitimately finds nothing."""

    name = "empty_search"
    description = "Search that returns no results."
    permission = PermissionLevel.SAFE
    requires_network = False
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    async def run(self, arguments: dict) -> ToolResult:
        return ToolResult(
            tool=self.name,
            ok=True,
            data={"results": [], "resultCount": 0, "message": "No matching vendors."},
        )


class BrokenTool(Tool):
    """A tool that fails."""

    name = "broken"
    description = "A tool that always fails."
    permission = PermissionLevel.SAFE
    requires_network = False
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def run(self, arguments: dict) -> ToolResult:
        return ToolResult(tool=self.name, ok=False, error="upstream unavailable")


class Registry:
    """Exposes a single tool through the real registry contract."""

    def __init__(self, *tools: Tool):
        self._tools = {tool.name: tool for tool in tools}

    def describe(self):
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    async def execute(self, name: str, arguments):
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(tool=name, ok=False, error=f"Unknown tool: {name}")
        return await tool.run(arguments or {})

    def names(self):
        return sorted(self._tools)


def tool_call(name: str, arguments: dict) -> dict:
    """Build a tool-calling chat-completions response."""
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
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def final_answer(text: str) -> dict:
    """Build a final-answer chat-completions response."""
    return {
        "model": "qwen/qwen3.8-27b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


# ----------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------


class TestGroqLoop:
    """Tool requests flow through the registry, never the model."""

    def test_direct_answer_uses_no_tools(self, groq_http):
        groq_http(lambda request: httpx.Response(200, json=final_answer("Nairobi."), request=request))
        turn = run(
            run_groq_turn(
                settings=settings(),
                registry=Registry(EchoTool()),
                system="S",
                user_text="What is the capital of Kenya?",
            )
        )
        assert turn.ok is True
        assert turn.text == "Nairobi."
        assert turn.tool_results == []

    def test_tool_call_is_executed_through_the_registry(self, groq_http):
        """The model requests a tool; the registry runs it."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200, json=tool_call("echo", {"value": "mahindi"}), request=request
                )
            return httpx.Response(200, json=final_answer("Found it."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(),
                registry=Registry(EchoTool()),
                system="S",
                user_text="price of mahindi?",
            )
        )
        assert calls["n"] == 2
        assert turn.text == "Found it."
        assert [r.tool for r in turn.tool_results] == ["echo"]
        assert turn.tool_results[0].ok is True

    def test_tool_output_is_fed_back_to_the_model(self, groq_http):
        """The second call must carry the tool result, not a placeholder."""
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return httpx.Response(
                    200, json=tool_call("echo", {"value": "x"}), request=request
                )
            return httpx.Response(200, json=final_answer("done"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(), registry=Registry(EchoTool()), system="S", user_text="q"
            )
        )
        second = json.dumps(bodies[1])
        assert "TOOL RESULTS" in second
        assert "echoed" in second

    def test_unknown_tool_does_not_crash_the_loop(self, groq_http):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "TOOL RESULTS" not in json.dumps(body):
                return httpx.Response(
                    200, json=tool_call("nonexistent", {}), request=request
                )
            return httpx.Response(200, json=final_answer("I could not find it."), request=request)

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(),
                registry=Registry(EchoTool()),
                system="S",
                user_text="q",
            )
        )
        assert turn.ok is True
        assert turn.tool_results[0].ok is False

    def test_empty_tool_result_is_passed_to_the_model(self, groq_http):
        """No results must reach the model as an authoritative 'nothing found'."""
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return httpx.Response(
                    200, json=tool_call("empty_search", {"query": "y"}), request=request
                )
            return httpx.Response(
                200, json=final_answer("I found no matching vendors."), request=request
            )

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(),
                registry=Registry(EmptyTool()),
                system="S",
                user_text="Natafuta beans Yanza na niko Samburu.",
            )
        )
        assert turn.ok is True
        assert "no matching" in turn.text
        assert "resultCount" in json.dumps(bodies[1])

    def test_failed_tool_is_reported_not_hidden(self, groq_http):
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return httpx.Response(200, json=tool_call("broken", {}), request=request)
            return httpx.Response(
                200, json=final_answer("That search is unavailable right now."), request=request
            )

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(), registry=Registry(BrokenTool()), system="S", user_text="q"
            )
        )
        assert turn.tool_results[0].ok is False
        assert "unavailable" in json.dumps(bodies[1]).lower()

    def test_tool_rounds_are_bounded(self, groq_http):
        """A model that keeps calling tools must not loop forever."""
        import backend.integrations.groq.client as client_mod

        rounds = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            rounds["n"] += 1
            return httpx.Response(
                200, json=tool_call("echo", {"value": "again"}), request=request
            )

        groq_http(handler)
        turn = run(
            run_groq_turn(
                settings=settings(groq_max_tool_rounds=2),
                registry=Registry(EchoTool()),
                system="S",
                user_text="q",
            )
        )
        assert rounds["n"] == 2
        assert turn.rounds == 2

    def test_provider_failure_returns_a_user_safe_message(self, groq_http, monkeypatch):
        import backend.integrations.groq.client as client_mod

        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 1)
        groq_http(
            lambda request: httpx.Response(
                429, json={"error": {"message": "Rate limit reached for org_01secret"}}, request=request
            )
        )
        turn = run(
            run_groq_turn(
                settings=settings(), registry=Registry(EchoTool()), system="S", user_text="q"
            )
        )
        assert turn.ok is False
        assert "org_01secret" not in turn.error, "raw provider text must not be user-facing"
        assert turn.error_detail is not None, "the technical detail is kept for logs"
        assert "org_01secret" in turn.error_detail

    def test_missing_key_returns_a_clean_failure(self, monkeypatch):
        turn = run(
            run_groq_turn(
                settings=Settings(groq_api_key=""),
                registry=Registry(EchoTool()),
                system="S",
                user_text="q",
            )
        )
        assert turn.ok is False
        assert "not configured" in turn.error.lower()


# ----------------------------------------------------------------------
# Orchestrator wiring
# ----------------------------------------------------------------------


class TestOrchestratorWiring:
    """The orchestrator prefers the Groq path when Groq is configured."""

    def test_orchestrator_reports_groq_as_the_decider(self, monkeypatch, groq_http):
        from backend.agent.orchestrator import SautiOrchestrator

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        groq_http(
            lambda request: httpx.Response(200, json=final_answer("Nairobi."), request=request)
        )
        orchestrator = SautiOrchestrator(settings=settings())
        assert orchestrator.llm.uses_groq is True

        turn = run(orchestrator.handle(message="What is the capital of Kenya?"))
        assert turn.decided_by == "groq"
        assert turn.reply == "Nairobi."
        assert turn.conversation_id is None or isinstance(turn.conversation_id, str)

    def test_orchestrator_falls_back_to_gemini_without_groq(self, monkeypatch):
        from backend.agent.orchestrator import SautiOrchestrator

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        orchestrator = SautiOrchestrator(settings=Settings(gemini_api_key=FAKE_KEY))
        assert orchestrator.llm.uses_groq is False
        assert orchestrator.llm.uses_gemini is True

    def test_orchestrator_offline_without_any_key(self, monkeypatch):
        from backend.agent.orchestrator import SautiOrchestrator

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        orchestrator = SautiOrchestrator(settings=Settings())
        assert orchestrator.llm.is_live is False

    def test_turn_serialises_the_existing_response_contract(self, monkeypatch, groq_http):
        """The frontend contract must not change."""
        from backend.agent.orchestrator import SautiOrchestrator

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        groq_http(
            lambda request: httpx.Response(200, json=final_answer("Nairobi."), request=request)
        )
        orchestrator = SautiOrchestrator(settings=settings())
        body = run(orchestrator.handle(message="capital of Kenya?")).to_dict()

        for field in (
            "message", "response", "language", "reply_language", "intent",
            "used_tools", "sources", "activity", "conversation_id",
            "request_id", "duration_ms",
        ):
            assert field in body, f"missing contract field: {field}"


class TestConversationShape:
    """The request must not duplicate or reorder the conversation.

    Groq's free tier is capped on input tokens per minute, so a duplicated
    user turn does not just look untidy, it directly reduces how many
    questions a user can ask.
    """

    def test_user_question_is_sent_exactly_once(self, groq_http):
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return httpx.Response(
                    200, json=tool_call("echo", {"value": "x"}), request=request
                )
            return httpx.Response(200, json=final_answer("done"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(), registry=Registry(EchoTool()), system="S", user_text="THE QUESTION"
            )
        )
        assert len(bodies) == 2
        for body in bodies:
            contents = [m["content"] for m in body["messages"]]
            assert contents.count("THE QUESTION") == 1, "the question must appear once"
        # And it must come before the tool output, not after it.
        second = [m["content"] for m in bodies[1]["messages"]]
        assert second.index("THE QUESTION") < next(
            i for i, c in enumerate(second) if "TOOL RESULTS" in str(c)
        )

    def test_system_prompt_is_first(self, groq_http):
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=final_answer("ok"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(), registry=Registry(EchoTool()), system="SYSTEM PROMPT", user_text="q"
            )
        )
        assert bodies[0]["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT"}

    def test_history_is_forwarded_in_order(self, groq_http):
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=final_answer("ok"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(),
                registry=Registry(EchoTool()),
                system="S",
                user_text="new question",
                history=[
                    {"role": "user", "content": "old question"},
                    {"role": "assistant", "content": "old answer"},
                ],
            )
        )
        contents = [m["content"] for m in bodies[0]["messages"]]
        assert contents == ["S", "old question", "old answer", "new question"]

    def test_prompt_growth_is_bounded_by_tool_output(self, groq_http):
        """The second round must add only the tool result, nothing more."""
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return httpx.Response(
                    200, json=tool_call("echo", {"value": "v"}), request=request
                )
            return httpx.Response(200, json=final_answer("done"), request=request)

        groq_http(handler)
        run(
            run_groq_turn(
                settings=settings(), registry=Registry(EchoTool()), system="S", user_text="q"
            )
        )
        first, second = bodies
        assert len(second["messages"]) == len(first["messages"]) + 1
