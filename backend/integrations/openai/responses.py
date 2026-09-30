"""OpenAI Responses API integration for Sauti orchestration.

This is the live-model path. When `LLM_API_KEY` is set, Sauti asks the model to
decide which tools to call using the Responses API's function-tool mechanism
(and its built-in web search). The model proposes tool *requests*; this module
executes them through the registry and feeds the results back.

The key never leaves the server. When no key is configured the agent falls
back to the deterministic planner and grounded responder, so the product keeps
working without credentials.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ...config.settings import Settings
from ...tools.base import ToolResult
from ...tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

#: Model id used for orchestration.
DEFAULT_MODEL = "gpt-4o-mini"

#: Endpoint for the Responses API.
RESPONSES_PATH = "/responses"


class OpenAIResponsesError(RuntimeError):
    """The Responses API call failed or returned an unusable shape."""


def build_tool_definitions(registry: ToolRegistry) -> list[dict]:
    """Convert the tool registry into Responses API function-tool schemas.

    Only schema information is exported. Execution stays in this process.
    """
    definitions: list[dict] = []
    for tool in registry.describe():
        definitions.append(
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": _to_json_schema(tool["input_schema"]),
            }
        )
    return definitions


def _to_json_schema(schema: dict) -> dict:
    """Normalise a registry schema into a strict JSON Schema object."""
    properties = schema.get("properties", {}) or {}
    required = list(schema.get("required", []) or [])
    normalised: dict[str, Any] = {}
    for name, spec in properties.items():
        entry: dict[str, Any] = {"type": spec.get("type", "string")}
        if "description" in spec:
            entry["description"] = spec["description"]
        if "default" in spec:
            entry["default"] = spec["default"]
        if "enum" in spec:
            entry["enum"] = spec["enum"]
        if "minimum" in spec:
            entry["minimum"] = spec["minimum"]
        if "maximum" in spec:
            entry["maximum"] = spec["maximum"]
        if "minLength" in spec:
            entry["minLength"] = spec["minLength"]
        if "maxLength" in spec:
            entry["maxLength"] = spec["maxLength"]
        normalised[name] = entry
    return {
        "type": "object",
        "properties": normalised,
        "required": required,
        "additionalProperties": False,
    }


class SautiOpenAIClient:
    """Minimal async client for the OpenAI Responses API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = settings.llm_model or DEFAULT_MODEL

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    async def _post(self, path: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout) as client:
                response = await client.post(
                    f"{self.base_url}{path}", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise OpenAIResponsesError(f"OpenAI request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OpenAIResponsesError(f"OpenAI request failed: {exc}") from exc

        if response.status_code >= 400:
            raise OpenAIResponsesError(
                f"OpenAI returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise OpenAIResponsesError("OpenAI returned a non-JSON response") from exc

    async def respond(
        self,
        instructions: str,
        user_message: str,
        registry: ToolRegistry,
        history: Optional[list[dict]] = None,
        use_builtin_web_search: bool = True,
        tool_results: Optional[list[dict]] = None,
    ) -> dict:
        """Call the Responses API once.

        Args:
            instructions: The Sauti system prompt.
            user_message: The user's message, or a follow-up containing the
                results of previously requested tools.
            registry: Used to build the tool schema.
            history: Prior turns.
            use_builtin_web_search: Offer the provider's hosted web search too.
            tool_results: Serialised results of tools we already executed, fed
                back as input so the model can produce the final answer.

        Returns:
            The parsed response payload.
        """
        tools: list[dict] = build_tool_definitions(registry)
        if use_builtin_web_search:
            tools.append({"type": "web_search"})

        input_items: list[dict] = []
        for turn in (history or [])[-8:]:
            role = turn.get("role", "user")
            input_items.append(
                {"role": role, "content": [{"type": "input_text", "text": turn.get("content", "")}]}
            )

        input_items.append(
            {"role": "user", "content": [{"type": "input_text", "text": user_message}]}
        )

        if tool_results:
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "TOOL RESULTS (authoritative, retrieved just now). "
                                "Answer using only these. Never invent a vendor, "
                                "price, phone number or availability that is not "
                                "present here.\n\n"
                                + json.dumps(tool_results, default=str)[:20000]
                            ),
                        }
                    ],
                }
            )

        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "max_output_tokens": self.settings.llm_max_tokens,
        }
        if tool_results is None:
            payload["tool_choice"] = "auto"

        return await self._post(RESPONSES_PATH, payload)


def extract_text(response: dict) -> str:
    """Pull the final assistant text out of a Responses API payload."""
    if not isinstance(response, dict):
        return ""
    if "output_text" in response and isinstance(response["output_text"], str):
        return response["output_text"]

    parts: list[str] = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts).strip()


def extract_tool_calls(response: dict) -> list[dict]:
    """Pull function-tool calls out of a Responses API payload."""
    calls: list[dict] = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        raw_arguments = item.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except json.JSONDecodeError:
            logger.warning("Model returned unparseable tool arguments")
            continue
        calls.append({"name": item.get("name", ""), "arguments": arguments})
    return calls


def serialise_tool_results(results: list[ToolResult]) -> list[dict]:
    """Shape tool results for feeding back to the model."""
    payload: list[dict] = []
    for result in results:
        payload.append(
            {
                "tool": result.tool,
                "ok": result.ok,
                "data": result.data if result.ok else None,
                "error": None if result.ok else result.error,
            }
        )
    return payload
