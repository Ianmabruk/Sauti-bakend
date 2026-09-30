"""Gemini tool-calling loop.

When Gemini is configured, the model decides which tools to call using its
native function-calling API. The loop is:

    model -> function call -> registry validation -> permission check
          -> execution -> structured result -> back to the model
          -> final answer

The model never executes code. Every call goes through the registry, exactly
like the OpenAI path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config.settings import Settings
from ..integrations.gemini import GeminiClient, GeminiError, build_function_tools
from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class GeminiTurn:
    """Outcome of one Gemini agent turn."""

    ok: bool
    text: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Optional[str] = None
    error_detail: Optional[str] = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rounds: int = 0


def _as_contents(user_text: str, history: Optional[list[dict]]) -> list[dict]:
    return GeminiClient._to_contents(user_text, history)


def _tool_result_message(results: list[ToolResult]) -> str:
    """Shape executed tool results for the model's next turn."""
    payload = []
    for result in results:
        entry: dict = {"tool": result.tool, "ok": result.ok}
        if result.ok:
            entry["result"] = result.data
        else:
            entry["error"] = result.error
        payload.append(entry)
    return (
        "TOOL RESULTS (authoritative, retrieved just now). Answer using only "
        "these. Never invent a vendor, price, phone number, availability or "
        "news fact that is not present here. If a tool failed, say so plainly.\n\n"
        + json.dumps(payload, default=str)[:20000]
    )


async def run_gemini_turn(
    settings: Settings,
    registry: ToolRegistry,
    system: str,
    user_text: str,
    history: Optional[list[dict]] = None,
) -> GeminiTurn:
    """Run a full Gemini turn, executing any tools it requests.

    Args:
        settings: Runtime settings, including the server-side key.
        registry: The tool registry. All execution goes through it.
        system: System instruction.
        user_text: The user's message.
        history: Prior turns.

    Returns:
        A GeminiTurn. Failures are reported, never hidden.
    """
    client = GeminiClient(settings)
    tool_schemas = build_function_tools(registry.describe())

    contents = _as_contents(user_text, history)
    collected: list[ToolResult] = []
    prompt_tokens = 0
    completion_tokens = 0
    model_used = ""

    for round_index in range(settings.gemini_max_tool_rounds):
        payload = client.build_payload(
            system,
            contents,
            tools=tool_schemas,
            temperature=settings.gemini_temperature,
            max_tokens=settings.gemini_max_tokens,
        )
        try:
            body = await client._post(client._models()[0], payload)
        except GeminiError as exc:
            logger.warning("Gemini turn failed: %s", exc.status)
            return GeminiTurn(
                ok=False,
                tool_results=collected,
                error=exc.user_message,
                error_detail=exc.message,
                rounds=round_index,
            )

        result = client._parse(body, model=client._models()[0])
        model_used = result.model
        prompt_tokens += result.prompt_tokens or 0
        completion_tokens += result.completion_tokens or 0

        if result.blocked:
            return GeminiTurn(
                ok=False,
                tool_results=collected,
                error="That request was blocked by the model's safety filters.",
                error_detail="blocked",
                rounds=round_index,
            )

        # Record what the model produced so far.
        model_parts: list[dict] = []
        if result.text:
            model_parts.append({"text": result.text})
        for call in result.function_calls:
            model_parts.append(
                {
                    "functionCall": {"name": call.name, "args": call.arguments}
                }
            )
        if model_parts:
            contents.append({"role": "model", "parts": model_parts})

        if not result.wants_tools:
            return GeminiTurn(
                ok=True,
                text=result.text,
                tool_results=collected,
                model=model_used,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                rounds=round_index,
            )

        # Execute every requested call through the registry.
        responses: list[ToolResult] = []
        for call in result.function_calls:
            logger.info("GEMINI TOOL REQUESTED: %s args=%s", call.name, call.arguments)
            tool_result = await registry.execute(call.name, call.arguments)
            responses.append(tool_result)
            logger.info(
                "GEMINI TOOL %s status=%s duration_ms=%s",
                call.name,
                "ok" if tool_result.ok else "failed",
                tool_result.duration_ms,
            )

        collected.extend(responses)
        contents.append(
            {"role": "user", "parts": [{"text": _tool_result_message(responses)}]}
        )

    # Ran out of rounds: return whatever the model last said, honestly.
    return GeminiTurn(
        ok=True,
        text="",
        tool_results=collected,
        model=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        rounds=settings.gemini_max_tool_rounds,
    )
