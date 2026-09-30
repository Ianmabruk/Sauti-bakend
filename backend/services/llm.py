"""LLM service for SAUTI.

Wraps the existing ``ModelProvider`` abstraction and adds:
- a real Groq client (primary engine) for chat and structured JSON
- a Gemini client, kept as a secondary engine so existing deployments that
  already have a working Gemini key are not broken by this change
- the pre-existing OpenAI-compatible provider
- a deterministic offline fallback so the agent is testable without any key

Engine order: Groq -> Gemini -> OpenAI-compatible -> deterministic offline.
Whichever engine answers, the tool registry and grounding rules are identical.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import httpx

from ai.factory import create_model_provider
from ai.providers import Message

from ..config.settings import Settings
from ..integrations.gemini import GeminiClient, GeminiError
from ..integrations.groq import GroqClient, GroqError
from ..tools.base import ToolResult

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class LLMUnavailable(RuntimeError):
    """The configured model could not be reached."""


class OpenAICompatibleProvider:
    """Minimal async client for any OpenAI-compatible /chat/completions API.

    Deliberately dependency-free: the request shape is small and stable.
    """

    provider_name = "openai_compatible"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = settings.llm_model

    async def chat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(f"Model request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Model request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMUnavailable(
                f"Model returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"Unexpected model response shape: {data}") from exc


class LLMService:
    """Generates SAUTI's answers and planner decisions.

    Engine order: Groq (when GROQ_API_KEY is set) -> Gemini ->
    OpenAI-compatible -> deterministic offline. Whichever engine answers, the
    tool registry and grounding rules are identical.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        self.groq = GroqClient(self.settings)
        self.gemini = GeminiClient(self.settings)

    # -- capability -----------------------------------------------------

    @property
    def is_live(self) -> bool:
        """True when a real model is reachable."""
        return (
            self.settings.groq_configured
            or self.settings.gemini_configured
            or bool(self.settings.llm_api_key)
        )

    @property
    def uses_groq(self) -> bool:
        """True when Groq is the primary engine."""
        return self.settings.groq_configured

    @property
    def uses_gemini(self) -> bool:
        """True when Groq is absent and Gemini should lead."""
        return self.settings.groq_configured is False and self.settings.gemini_configured

    def provider_name(self) -> str:
        if self.settings.groq_configured:
            return "groq"
        if self.settings.gemini_configured:
            return "gemini"
        if self.settings.llm_api_key:
            return "openai_compatible"
        return self.settings.model_provider or "mock"

    # -- generation -----------------------------------------------------

    async def complete(
        self,
        system: str,
        user: str,
        history: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a completion.

        Args:
            system: The system prompt.
            user: The user's message.
            history: Prior turns as [{"role", "content"}].
            temperature: Overrides the configured default.
            max_tokens: Overrides the configured default.

        Returns:
            The model's text output.

        Raises:
            LLMUnavailable: When every live engine fails.
        """
        temp = self.settings.groq_temperature if temperature is None else temperature
        tokens = self.settings.groq_max_tokens if max_tokens is None else max_tokens

        if self.uses_groq:
            try:
                result = await self.groq.generate(
                    system=system,
                    user_text=user,
                    history=history,
                    temperature=temp,
                    max_tokens=tokens,
                )
            except GroqError as exc:
                # Fall through to the next engine rather than failing the turn.
                logger.warning("Groq unavailable (%s), trying next engine", exc.status)
                if not (self.settings.gemini_configured or self.settings.llm_api_key):
                    raise LLMUnavailable(exc.user_message) from exc
            else:
                if result.text.strip():
                    return result.text
                # A reasoning model can return an empty visible answer when its
                # budget is consumed. Retry once with a larger allowance before
                # giving up, so the user never receives a blank reply.
                logger.warning("Groq returned an empty answer (model=%s)", result.model)
                try:
                    retry = await self.groq.generate(
                        system=system,
                        user_text=user,
                        history=history,
                        temperature=temp,
                        max_tokens=max(tokens * 2, 2000),
                    )
                except GroqError:
                    retry = None
                if retry is not None and retry.text.strip():
                    return retry.text
                raise LLMUnavailable(
                    "Sauti's AI service returned an empty answer. Please try again."
                )

        if self.settings.gemini_configured:
            gemini_history = [
                {"role": t.get("role"), "content": t.get("content")}
                for t in (history or [])[-self.settings.agent_history_limit:]
            ]
            try:
                result = await self.gemini.generate(
                    system=system,
                    user_text=user,
                    history=gemini_history,
                    temperature=temp,
                    max_tokens=tokens,
                )
            except GeminiError as exc:
                # Fall back rather than failing the whole turn.
                logger.warning("Gemini unavailable (%s), trying next engine", exc.status)
                if not self.settings.llm_api_key:
                    raise LLMUnavailable(exc.user_message) from exc
            else:
                if result.blocked:
                    raise LLMUnavailable(
                        "The request was blocked by the model's safety filters."
                    )
                if result.text.strip():
                    return result.text

        if self.is_live:
            client = OpenAICompatibleProvider(self.settings)
            messages: list[dict] = [{"role": "system", "content": system}]
            for turn in (history or [])[-self.settings.agent_history_limit:]:
                messages.append({"role": turn["role"], "content": turn["content"]})
            messages.append({"role": "user", "content": user})
            return await client.chat(messages, temperature=temp, max_tokens=tokens)

        return await self._offline_complete(system, user, history)

    async def complete_json(
        self, system: str, user: str, history: Optional[list[dict]] = None
    ) -> Optional[dict]:
        """Ask for a strict JSON object. Returns None when parsing fails."""
        if self.uses_groq:
            return await self.groq.generate_json(system, user, history)

        if self.settings.gemini_configured:
            # Gemini is asked for strict JSON, then parsed the same way as any
            # other engine's planner output.
            try:
                result = await self.gemini.generate(
                    system=system + (
                        "\n\nRespond with a single valid JSON object and nothing else."
                    ),
                    user_text=user,
                    temperature=0.0,
                    max_tokens=400,
                )
            except GeminiError as exc:
                logger.warning("Gemini planner call failed: %s", exc.status)
                return None
            return _parse_json_object(result.text)

        if self.is_live:
            client = OpenAICompatibleProvider(self.settings)
            messages: list[dict] = [{"role": "system", "content": system}]
            messages.append({"role": "user", "content": user})
            try:
                raw = await client.chat(
                    messages, temperature=0.0, max_tokens=400, json_mode=True
                )
            except LLMUnavailable as exc:
                logger.warning("Planner model call failed: %s", exc)
                return None
        else:
            return None

        match = _JSON_BLOCK.search(raw or "")
        if not match:
            logger.warning("Planner returned non-JSON output")
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("Planner returned invalid JSON")
            return None
        return parsed if isinstance(parsed, dict) else None

    # -- offline fallback ------------------------------------------------

    async def _offline_complete(
        self, system: str, user: str, history: Optional[list[dict]]
    ) -> str:
        """Deterministic, no-network answer generation.

        This keeps the whole agent testable without any credentials. It is
        clearly labelled in the response so it is never mistaken for a real
        model answer.
        """
        await asyncio.sleep(0)
        provider = create_model_provider("mock")
        messages = [Message(role="system", content=system)]
        for turn in (history or [])[-self.settings.agent_history_limit:]:
            messages.append(Message(role=turn["role"], content=turn["content"]))
        messages.append(Message(role="user", content=user))

        result = provider.generate_response(
            messages=messages,
            context=None,
            temperature=self.settings.llm_temperature,
            max_tokens=self.settings.llm_max_tokens,
        )
        return result.content


def _parse_json_object(raw: str) -> Optional[dict]:
    """Extract a JSON object from a model response, or return None."""
    if not raw:
        return None
    match = _JSON_BLOCK.search(raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def summarise_tool_result(result: ToolResult, max_chars: int = 2500) -> str:
    """Turn a tool result into a compact block for the model prompt."""
    if not result.ok:
        return f"TOOL {result.tool} FAILED: {result.error}"

    if result.tool == "web_search":
        payload = result.data or {}
        lines = [f"TOOL {result.tool} SUCCEEDED. Sources found: {len(payload.get('sources', []))}"]
        for source in payload.get("sources", [])[:6]:
            date = source.get("date") or "date unknown"
            lines.append(
                f"- {source.get('title')} | {source.get('domain')} | {date} | {source.get('url')}"
            )
        for extraction in payload.get("extractions", [])[:4]:
            excerpt = " ".join((extraction.get("excerpt") or "").split())[:400]
            if excerpt:
                lines.append(f"  excerpt: {excerpt}")
        for conflict in payload.get("conflicts", [])[:3]:
            lines.append(f"  CONFLICT: {conflict.get('note')}")
        return "\n".join(lines)[:max_chars]

    data = result.data
    if isinstance(data, dict) and "content" in data:
        content = " ".join(str(data["content"]).split())[:max_chars]
        return (
            f"TOOL {result.tool} SUCCEEDED. {data.get('title', '')} "
            f"({data.get('published_at') or 'date unknown'})\n{content}"
        )

    return f"TOOL {result.tool} SUCCEEDED: {json.dumps(data, default=str)[:max_chars]}"
