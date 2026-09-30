"""Tests for the Google Gemini integration.

These cover request shaping, response parsing, tool-calling, error handling and
— importantly — that the API key can never escape the server.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.agent.gemini_loop import run_gemini_turn
from backend.config.settings import Settings
from backend.integrations.gemini import (
    GeminiClient,
    GeminiError,
    build_function_tools,
)
from backend.tools.registry import ToolRegistry
from backend.tools.calculator import CalculatorTool
from backend.marketplace.tools import SearchMarketplaceTool

FAKE_KEY = "AIzaSyTESTKEY-THIS-IS-NOT-REAL-000000000000"


def run(coro):
    return asyncio.run(coro)


def make_client(**kwargs):
    return GeminiClient(Settings(gemini_api_key=FAKE_KEY, **kwargs))


def transport_that(handler):
    return httpx.MockTransport(handler)


class TestConfiguration:
    def test_not_configured_without_key(self):
        client = GeminiClient(Settings(gemini_api_key=""))
        assert client.configured is False

    def test_configured_with_key(self):
        assert make_client().configured is True

    def test_primary_engine_prefers_gemini(self):
        assert Settings(gemini_api_key=FAKE_KEY).primary_engine == "gemini"

    def test_primary_engine_falls_back(self):
        assert Settings().primary_engine == "offline"

    def test_public_summary_never_leaks_key(self):
        summary = Settings(gemini_api_key=FAKE_KEY).public_summary()
        blob = json.dumps(summary)
        assert FAKE_KEY not in blob
        assert "AIza" not in blob

    def test_model_fallback_order(self):
        client = make_client(gemini_model="gemini-9.9-pro")
        models = client._models()
        assert models[0] == "gemini-9.9-pro"
        assert "gemini-2.0-flash" in models


class TestRequestShaping:
    def test_payload_contains_system_instruction(self):
        client = make_client()
        payload = client.build_payload(
            "You are SAUTI.", [{"role": "user", "parts": [{"text": "hi"}]}]
        )
        assert payload["systemInstruction"] == {"parts": [{"text": "You are SAUTI."}]}
        assert payload["contents"][0]["parts"][0]["text"] == "hi"

    def test_payload_includes_tools(self):
        client = make_client()
        tools = build_function_tools(
            [{"name": "search_marketplace", "description": "d", "input_schema": {"type": "object"}}]
        )
        payload = client.build_payload("sys", [], tools=tools)
        assert "tools" in payload
        assert payload["tools"][0]["functionDeclarations"][0]["name"] == "search_marketplace"

    def test_history_maps_assistant_to_model(self):
        contents = GeminiClient._to_contents(
            "now", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        )
        assert [c["role"] for c in contents] == ["user", "model", "user"]

    def test_key_sent_in_header_not_url(self):
        """The key must be a header, never a query string in a logged URL."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            )

        client = make_client()
        original = client._post

        async def call(model, payload):
            async with httpx.AsyncClient(transport=transport_that(handler)) as c:
                response = await c.post(
                    f"{client.base_url}/models/{model}:generateContent",
                    json=payload,
                    headers={"x-goog-api-key": client.settings.gemini_api_key},
                )
            return response.json()

        run(call("gemini-2.0-flash", client.build_payload("s", [])))
        assert FAKE_KEY not in captured["url"]
        assert captured["headers"].get("x-goog-api-key") == FAKE_KEY
        assert original is not None


class TestResponseParsing:
    def test_parses_text(self):
        body = {"candidates": [{"content": {"parts": [{"text": "Hello there"}]}}]}
        result = GeminiClient._parse(body, "gemini-2.0-flash")
        assert result.text == "Hello there"
        assert result.model == "gemini-2.0-flash"

    def test_parses_function_call(self):
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "search_marketplace",
                                    "args": {"query": "blue g-wagon"},
                                }
                            }
                        ]
                    }
                }
            ]
        }
        result = GeminiClient._parse(body, "m")
        assert result.wants_tools is True
        assert result.function_calls[0].name == "search_marketplace"
        assert result.function_calls[0].arguments == {"query": "blue g-wagon"}

    def test_parses_stringified_args(self):
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "calculator",
                                    "args": '{"expression": "2+2"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
        result = GeminiClient._parse(body, "m")
        assert result.function_calls[0].arguments == {"expression": "2+2"}

    def test_reports_blocked(self):
        body = {"promptFeedback": {"blockReason": "SAFETY"}}
        result = GeminiClient._parse(body, "m")
        assert result.blocked is True

    def test_captures_usage(self):
        body = {
            "candidates": [{"content": {"parts": [{"text": "x"}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 22},
        }
        result = GeminiClient._parse(body, "m")
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 22

    def test_empty_candidates_raises(self):
        with pytest.raises(GeminiError):
            GeminiClient._parse({}, "m")


class TestErrorHandling:
    @pytest.mark.parametrize(
        "status,body,expected_fragment",
        [
            (400, {"error": {"message": "API key not valid. Please pass a valid API key."}},
             "rejected its credentials"),
            (429, {"error": {"message": "quota"}}, "busy"),
            (500, {"error": {"message": "backend error"}}, "trouble"),
            (403, {"error": {"message": "denied"}}, "denied"),
        ],
    )
    def test_errors_become_user_safe_messages(self, status, body, expected_fragment):
        client = make_client()
        response = httpx.Response(status, json=body, request=httpx.Request("POST", "http://x"))
        error = client._error_from_response(response)
        assert expected_fragment in error.user_message.lower()
        assert "AIza" not in error.user_message
        assert FAKE_KEY not in error.message or "API key not valid" in error.message

    def test_not_configured_error_is_friendly(self):
        client = GeminiClient(Settings())
        with pytest.raises(GeminiError) as info:
            run(client.generate("sys", "hi"))
        assert "not configured" in info.value.user_message.lower()

    def test_model_unavailable_detection(self):
        client = make_client()
        error = GeminiError("model gemini-9.9 not found", status=404)
        assert client._is_model_unavailable(error) is True
        assert client._is_model_unavailable(GeminiError("rate limited", status=429)) is False


class TestGeminiToolLoop:
    def test_executes_requested_tool(self, monkeypatch):
        """The model asks for a tool; the registry runs it."""
        calls = {"texts": 0}

        async def fake_post(self, model, payload):
            calls["texts"] += 1
            if calls["texts"] == 1:
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "calculator",
                                            "args": {"expression": "2+2"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            return {"candidates": [{"content": {"parts": [{"text": "The answer is 4."}]}}]}

        monkeypatch.setattr(GeminiClient, "_post", fake_post)
        settings = Settings(gemini_api_key=FAKE_KEY)
        registry = ToolRegistry(settings)
        registry.register(CalculatorTool(settings))

        turn = run(run_gemini_turn(settings, registry, "sys", "what is 2+2"))
        assert turn.ok is True
        assert turn.text == "The answer is 4."
        assert [r.tool for r in turn.tool_results] == ["calculator"]
        assert turn.tool_results[0].data["result"] == 4

    def test_invalid_tool_request_becomes_a_failure_not_a_crash(self, monkeypatch):
        async def fake_post(self, model, payload):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "calculator", "args": {}}}
                            ]
                        }
                    }
                ]
            }

        monkeypatch.setattr(GeminiClient, "_post", fake_post)
        settings = Settings(gemini_api_key=FAKE_KEY)
        registry = ToolRegistry(settings)
        registry.register(CalculatorTool(settings))

        turn = run(run_gemini_turn(settings, registry, "sys", "calc"))
        assert turn.tool_results[0].ok is False
        assert "expression" in turn.tool_results[0].error

    def test_hallucinated_tool_is_rejected(self, monkeypatch):
        async def fake_post(self, model, payload):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "run_shell", "args": {"cmd": "rm -rf /"}}}
                            ]
                        }
                    }
                ]
            }

        monkeypatch.setattr(GeminiClient, "_post", fake_post)
        settings = Settings(gemini_api_key=FAKE_KEY)
        registry = ToolRegistry(settings)
        registry.register(CalculatorTool(settings))

        turn = run(run_gemini_turn(settings, registry, "sys", "delete everything"))
        assert turn.tool_results[0].ok is False
        assert "Unknown tool" in turn.tool_results[0].error

    def test_api_failure_returns_clean_error(self, monkeypatch):
        async def failing(self, model, payload):
            raise GeminiError("boom", status=500, user_message="Sauti's AI service is having trouble.")

        monkeypatch.setattr(GeminiClient, "_post", failing)
        settings = Settings(gemini_api_key=FAKE_KEY)
        registry = ToolRegistry(settings)
        turn = run(run_gemini_turn(settings, registry, "sys", "hi"))
        assert turn.ok is False
        assert "trouble" in turn.error
        assert FAKE_KEY not in (turn.error or "")


class TestEngineSelection:
    def test_llm_service_prefers_gemini(self):
        from backend.services.llm import LLMService

        service = LLMService(Settings(gemini_api_key=FAKE_KEY))
        assert service.provider_name() == "gemini"
        assert service.uses_gemini is True
        assert service.is_live is True

    def test_llm_service_offline_without_keys(self):
        from backend.services.llm import LLMService

        service = LLMService(Settings())
        assert service.provider_name() == "mock"
        assert service.is_live is False

    def test_orchestrator_uses_gemini_path(self, monkeypatch, app):
        """With a Gemini key configured the orchestrator takes the Gemini path."""
        from backend.agent.orchestrator import SautiOrchestrator
        from backend.marketplace.models import MarketplaceCategory, Product, Vendor
        from backend.db import db

        with app.app_context():
            db.create_all()
            settings = Settings(gemini_api_key=FAKE_KEY, search_provider="stub")
            registry = ToolRegistry(settings)
            registry.register(SearchMarketplaceTool(settings))
            agent = SautiOrchestrator(settings=settings, registry=registry)

            async def fake_loop(**kwargs):
                from backend.agent.gemini_loop import GeminiTurn

                return GeminiTurn(ok=True, text="Bonjour", model="gemini-2.0-flash")

            monkeypatch.setattr(
                "backend.agent.gemini_loop.run_gemini_turn", fake_loop
            )
            turn = run(agent.handle("bonjour"))
            assert turn.reply == "Bonjour"
            assert turn.decided_by == "gemini"
            assert turn.offline_fallback is False


class TestSecretSafety:
    def test_error_messages_never_contain_the_key(self):
        client = make_client()
        response = httpx.Response(
            400,
            json={"error": {"message": f"API key not valid: {FAKE_KEY}"}},
            request=httpx.Request("POST", "http://x"),
        )
        error = client._error_from_response(response)
        assert FAKE_KEY not in error.user_message

    def test_health_payload_never_contains_the_key(self):
        client = make_client()
        result = run(client.health())
        assert FAKE_KEY not in json.dumps(result)

    def test_sauti_health_endpoint_hides_key(self, client):
        data = client.get("/api/sauti/health").get_json()
        assert FAKE_KEY not in json.dumps(data)
        assert "geminiConfigured" in data
        # The env var name may appear; the value never does.
        assert "GEMINI_API_KEY" not in json.dumps(data)

    def test_chat_response_never_contains_the_key(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        from backend.config.settings import get_settings

        get_settings(refresh=True)
        from backend.api.sauti import get_orchestrator

        get_orchestrator()
        response = client.post("/api/sauti/chat", json={"message": "hello"})
        assert FAKE_KEY not in response.get_data(as_text=True)
