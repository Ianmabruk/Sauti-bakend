"""Tests for the Groq integration.

The suite is hermetic: no network, no real key. Provider behaviour is
simulated at the httpx transport layer so request shapes, error handling and
the tool-calling loop are all exercised exactly as they run in production.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.config.settings import Settings
from backend.integrations.groq import (
    GroqClient,
    GroqError,
    build_function_tools,
)

FAKE_KEY = "gsk_TEST-not-a-real-key-000000000000"

#: A representative chat-completions response with a tool call.
TOOL_RESPONSE = {
    "id": "chatcmpl-1",
    "model": "qwen/qwen3.8-27b",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_marketplace",
                            "arguments": '{"query": "mahindi price Kenya", "category": "maize"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30},
}

#: A representative final-answer response.
TEXT_RESPONSE = {
    "id": "chatcmpl-2",
    "model": "qwen/qwen3.8-27b",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Bei ya mahindi ni KSh 3,400."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 200, "completion_tokens": 40},
}


def run(coro):
    """Run a coroutine to completion."""
    import asyncio

    return asyncio.run(coro)


def transport(handler) -> httpx.MockTransport:
    """Wrap a handler function into an httpx transport."""
    return httpx.MockTransport(handler)


def settings_with(**kwargs) -> Settings:
    """Settings carrying a fake key and no other provider."""
    return Settings(groq_api_key=FAKE_KEY, **kwargs)


@pytest.fixture
def groq_http(monkeypatch):
    """Intercept the AsyncClient the Groq client builds per request.

    The production client intentionally constructs its own
    ``httpx.AsyncClient`` so no pooled socket outlives a request. Tests
    therefore substitute the transport at the constructor. ``monkeypatch``
    restores the real class after each test.

    Returns:
        A callable that installs a request handler for the current test.
    """
    holder: dict = {"handler": None}
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(holder["handler"])
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    def install(handler):
        """Register the handler that will receive every request."""
        holder["handler"] = handler

    return install


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


class TestConfiguration:
    """Groq settings are read from the environment, never hard-coded."""

    def test_reads_key_and_model_from_env(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
        monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
        settings = Settings()
        assert settings.groq_configured is True
        assert settings.groq_model == "openai/gpt-oss-120b"

    def test_model_is_configurable_not_hard_coded(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
        assert Settings().groq_model == "qwen/qwen3.8-27b"
        monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
        assert Settings().groq_model == "openai/gpt-oss-20b"

    def test_base_url_is_the_documented_endpoint(self):
        assert Settings().groq_base_url == "https://api.groq.com/openai/v1"

    def test_groq_is_the_primary_engine(self):
        assert settings_with().primary_engine == "groq"

    def test_gemini_remains_the_fallback(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        settings = Settings(gemini_api_key=FAKE_KEY)
        assert settings.primary_engine == "gemini"

    def test_no_keys_means_offline(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert Settings().primary_engine == "offline"

    def test_empty_key_is_not_configured(self):
        assert Settings(groq_api_key="   ").groq_configured is False

    def test_health_reports_configured_without_the_key(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret_value")
        summary = Settings().public_summary()
        rendered = json.dumps(summary)
        assert summary["groq_configured"] is True
        assert "gsk_super_secret_value" not in rendered

    def test_engine_selection(self, monkeypatch):
        from backend.services.llm import LLMService

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        service = LLMService(settings_with())
        assert service.uses_groq is True
        assert service.uses_gemini is False
        assert service.provider_name() == "groq"
        assert service.is_live is True


# ----------------------------------------------------------------------
# Request shape
# ----------------------------------------------------------------------


class TestRequestShape:
    """Requests follow the OpenAI-compatible contract Groq expects."""

    def test_builds_an_openai_payload(self):
        client = GroqClient(settings_with())
        payload = client.build_payload("SYSTEM", [{"role": "user", "content": "hi"}])
        assert payload["messages"][0] == {"role": "system", "content": "SYSTEM"}
        assert payload["messages"][1]["role"] == "user"
        assert "temperature" in payload and "max_tokens" in payload
        assert "tools" not in payload

    def test_tools_are_omitted_when_not_supplied(self):
        client = GroqClient(settings_with())
        assert "tools" not in client.build_payload("S", [])

    def test_tool_choice_is_auto_when_tools_present(self):
        client = GroqClient(settings_with())
        payload = client.build_payload("S", [{"role": "user", "content": "x"}], tools=[{"a": 1}])
        assert payload["tool_choice"] == "auto"

    def test_authorization_header_is_a_bearer_token(self):
        client = GroqClient(settings_with())
        assert client.headers["Authorization"] == f"Bearer {FAKE_KEY}"

    def test_build_function_tools_matches_registry_shape(self):
        descriptors = [
            {
                "name": "search_marketplace",
                "description": "Search the marketplace.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ]
        tools = build_function_tools(descriptors)
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "search_marketplace"
        assert tools[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"

    def test_build_function_tools_handles_missing_schema(self):
        tools = build_function_tools([{"name": "x", "description": "d"}])
        assert tools[0]["function"]["parameters"]["type"] == "object"

    def test_history_maps_assistant_role(self):
        messages = GroqClient._to_messages(
            "now", [{"role": "assistant", "content": "before"}, {"role": "user", "content": "then"}]
        )
        assert [m["role"] for m in messages] == ["assistant", "user", "user"]


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------


class TestResponseParsing:
    """Tool calls and text are read out of Groq's response shape."""

    def test_parses_a_tool_call(self):
        result = GroqClient._parse(TOOL_RESPONSE, "qwen/qwen3.8-27b")
        assert result.wants_tools is True
        assert result.function_calls[0].name == "search_marketplace"
        assert result.function_calls[0].arguments["category"] == "maize"
        assert result.prompt_tokens == 120

    def test_parses_text(self):
        result = GroqClient._parse(TEXT_RESPONSE, "qwen/qwen3.8-27b")
        assert result.text.startswith("Bei ya mahindi")
        assert result.wants_tools is False

    def test_reasoning_content_is_not_treated_as_the_answer(self):
        """Reasoning models emit a separate field; only content is the answer."""
        body = json.loads(json.dumps(TEXT_RESPONSE))
        body["choices"][0]["message"]["reasoning"] = "internal chain of thought"
        result = GroqClient._parse(body, "m")
        assert "chain of thought" not in result.text

    def test_tool_arguments_as_object_are_accepted(self):
        body = json.loads(json.dumps(TOOL_RESPONSE))
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = {"query": "x"}
        assert GroqClient._parse(body, "m").function_calls[0].arguments == {"query": "x"}

    def test_malformed_tool_arguments_do_not_crash(self):
        body = json.loads(json.dumps(TOOL_RESPONSE))
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
        assert GroqClient._parse(body, "m").function_calls[0].arguments == {}

    def test_no_choices_raises(self):
        with pytest.raises(GroqError):
            GroqClient._parse({"choices": []}, "m")

    def test_json_object_extraction(self):
        from backend.integrations.groq.client import _parse_json_object

        assert _parse_json_object('{"a": 1}') == {"a": 1}
        assert _parse_json_object('prose {"a": 1} more') == {"a": 1}
        assert _parse_json_object('{"a": "brace } inside"}') == {"a": "brace } inside"}
        assert _parse_json_object("no json") is None
        assert _parse_json_object("") is None


# ----------------------------------------------------------------------
# Error handling
# ----------------------------------------------------------------------


class TestErrorHandling:
    """Failures become safe, classified errors."""

    def test_missing_key_is_reported(self):
        client = GroqClient(Settings(groq_api_key=""))
        assert client.configured is False
        with pytest.raises(GroqError) as exc:
            run(client.generate(system="s", user_text="u"))
        assert "not configured" in exc.value.user_message.lower()

    def test_invalid_key_maps_to_credentials_message(self):
        response = httpx.Response(
            401,
            json={"error": {"message": "Invalid API Key"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        error = GroqClient(settings_with())._error_from_response(response)
        assert error.status == 401
        assert "credentials" in error.user_message.lower()
        assert "sk_" not in error.user_message

    def test_rate_limit_is_retryable(self):
        response = httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        error = GroqClient(settings_with())._error_from_response(response)
        assert error.retryable is True
        assert error.status == 429

    def test_server_error_is_retryable(self):
        response = httpx.Response(
            503,
            json={"error": {"message": "upstream"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        assert GroqClient(settings_with())._error_from_response(response).retryable is True

    def test_client_error_is_not_retryable(self):
        response = httpx.Response(
            400,
            json={"error": {"message": "bad request"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        assert GroqClient(settings_with())._error_from_response(response).retryable is False

    def test_error_messages_never_contain_the_key(self):
        for status in (400, 401, 403, 429, 500):
            response = httpx.Response(
                status,
                json={"error": {"message": f"failure {status}"}},
                request=httpx.Request("POST", "https://api.groq.com"),
            )
            error = GroqClient(settings_with())._error_from_response(response)
            assert FAKE_KEY not in error.message
            assert FAKE_KEY not in error.user_message

    def test_unavailable_model_is_detected(self):
        response = httpx.Response(
            404,
            json={"error": {"message": "model_not_found"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        error = GroqClient(settings_with())._error_from_response(response)
        assert GroqClient(settings_with())._is_model_unavailable(error) is True

    def test_rate_limit_is_retried_then_raises(self, monkeypatch, groq_http):
        """A persistent 429 must be retried, then surfaced cleanly."""
        import backend.integrations.groq.client as client_mod

        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 3)
        monkeypatch.setattr(client_mod, "BACKOFF_BASE", 0.0)
        monkeypatch.setattr(client_mod, "BACKOFF_MAX", 0.0)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})

        groq_http(handler)
        client = GroqClient(settings_with())

        with pytest.raises(GroqError) as exc:
            run(client.generate(system="s", user_text="u"))
        assert calls["n"] == 3, "must retry a rate limit"
        assert "busy" in exc.value.user_message.lower() or "try again" in exc.value.user_message.lower()

    def test_model_fallback_tries_the_next_model(self, monkeypatch, groq_http):
        """An unknown configured model must fall through to one that works."""
        import backend.integrations.groq.client as client_mod

        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 1)
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            model = payload["model"]
            seen.append(model)
            if model == "does/not-exist":
                return httpx.Response(404, json={"error": {"message": "model_not_found"}})
            return httpx.Response(200, json=TEXT_RESPONSE)

        groq_http(handler)
        client = GroqClient(settings_with(groq_model="does/not-exist"))
        result = run(client.generate(system="s", user_text="u"))
        assert seen[0] == "does/not-exist"
        assert len(seen) > 1, "must try a fallback model"
        assert result.text


# ----------------------------------------------------------------------
# Security
# ----------------------------------------------------------------------


class TestSecurity:
    """The key never escapes the server."""

    def test_key_is_never_in_a_user_facing_message(self):
        client = GroqClient(settings_with())
        response = httpx.Response(
            500,
            json={"error": {"message": f"internal failure referencing {FAKE_KEY}"}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )
        error = client._error_from_response(response)
        # The raw detail may contain it, but the user-facing message must not.
        assert FAKE_KEY not in error.user_message

    def test_unavailable_message_drops_internal_detail(self):
        from backend.agent.response import engine_unavailable_message

        leaky = (
            "Rate limit reached for model `qwen/qwen3.8-27b` in organization "
            "`org_01m3kvv4nregcvdat981erq473` service tier `on_demand` on "
            "input tokens per minute: Limit 7000, Used 6427"
        )
        message = engine_unavailable_message(leaky, "en")
        assert "org_01m3kvv4nregcvdat981erq473" not in message
        assert "qwen/qwen3.8-27b" not in message
        assert "7000" not in message
        assert "temporarily" in message.lower() or "isn't available" in message.lower()

    def test_unavailable_message_is_localized(self):
        from backend.agent.response import engine_unavailable_message

        assert "indisponible" in engine_unavailable_message(None, "fr")
        assert "haipatikani" in engine_unavailable_message(None, "sw")

    def test_health_never_returns_the_key(self, groq_http):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"id": "qwen/qwen3.8-27b"}]},
                request=request,
            )

        groq_http(handler)
        client = GroqClient(settings_with())
        result = run(client.health())
        assert result["available"] is True
        assert FAKE_KEY not in json.dumps(result)

    def test_health_reports_unreachable_without_the_key(self):
        result = run(GroqClient(Settings(groq_api_key="")).health())
        assert result["available"] is False
        assert "GROQ_API_KEY" in result["reason"]

    def test_health_warns_when_configured_model_is_unavailable(self, groq_http):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "some/other-model"}]}, request=request)

        groq_http(handler)
        client = GroqClient(settings_with())
        result = run(client.health())
        assert result["available"] is True
        assert "not in this account" in result["reason"]


# ----------------------------------------------------------------------
# Transcription
# ----------------------------------------------------------------------


class TestTranscription:
    """Voice input uses the same key as chat."""

    def test_missing_key_is_reported(self):
        client = GroqClient(Settings(groq_api_key=""))
        with pytest.raises(GroqError) as exc:
            run(client.transcribe(b"RIFF", mime_type="audio/wav"))
        assert "not configured" in exc.value.user_message.lower()

    def test_empty_audio_is_reported(self):
        with pytest.raises(GroqError) as exc:
            run(GroqClient(settings_with()).transcribe(b"", mime_type="audio/wav"))
        assert "didn't catch" in exc.value.user_message.lower()

    def test_uses_the_configured_stt_model(self, groq_http):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["called"] = True
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"text": "habari"}, request=request)

        groq_http(handler)
        client = GroqClient(settings_with())
        text = run(client.transcribe(b"RIFFWAVE", mime_type="audio/wav", language="sw"))
        assert text == "habari"
        assert seen["called"] is True
        assert seen["url"].endswith("/audio/transcriptions")
        assert seen["auth"] == f"Bearer {FAKE_KEY}"

    def test_stt_model_is_configurable(self, monkeypatch):
        monkeypatch.setenv("GROQ_STT_MODEL", "whisper-large-v3")
        assert Settings().groq_stt_model == "whisper-large-v3"


class TestFunctionCallGenerationFailures:
    """Malformed tool calls are a sampling artifact, so they are retried."""

    def _bad_request(self, message: str) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": message}},
            request=httpx.Request("POST", "https://api.groq.com"),
        )

    def test_failed_to_call_a_function_is_retryable(self):
        error = GroqClient(settings_with())._error_from_response(
            self._bad_request("Failed to call a function. Please adjust your prompt.")
        )
        assert error.retryable is True

    def test_failed_generation_is_retryable(self):
        error = GroqClient(settings_with())._error_from_response(
            self._bad_request("failed_generation detail")
        )
        assert error.retryable is True

    def test_a_genuine_bad_request_is_not_retryable(self):
        """An unknown field must not be hammered three times."""
        error = GroqClient(settings_with())._error_from_response(
            self._bad_request("unknown field 'foo' in payload")
        )
        assert error.retryable is False

    def test_malformed_call_then_success_is_recovered(self, monkeypatch, groq_http):
        import backend.integrations.groq.client as client_mod

        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 3)
        monkeypatch.setattr(client_mod, "BACKOFF_BASE", 0.0)
        monkeypatch.setattr(client_mod, "BACKOFF_MAX", 0.0)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    400, json={"error": {"message": "Failed to call a function."}}, request=request
                )
            return httpx.Response(200, json=TEXT_RESPONSE, request=request)

        groq_http(handler)
        result = run(GroqClient(settings_with()).generate(system="s", user_text="u"))
        assert calls["n"] == 2
        assert result.text.startswith("Bei ya mahindi")
