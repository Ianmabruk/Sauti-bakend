"""Groq client for SAUTI.

Groq serves both inference needs from one key:

  1. generating the final answer, and
  2. deciding which tools to call, via the OpenAI-compatible ``tools`` API.

The API is read from the server environment and is only ever sent to Groq. It
is never logged, never returned by an endpoint, and never reaches the browser.

Deliberately dependency-free, exactly like the Gemini client: the request
shape is small and stable, and avoiding a vendor SDK keeps the install small
and the key handling explicit and auditable.

Function calls are *requests*. They are validated and executed by the existing
tool registry, never by the model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ...config.settings import Settings

logger = logging.getLogger(__name__)

#: Models tried in order when the configured model is unavailable. Groq
#: retires model names and accounts differ in what they can reach, so the list
#: is deliberately generous and the client falls through on a model-not-found
#: response rather than hard-failing. All of these are OpenAI-compatible chat
#: models that support tool calling.
FALLBACK_MODELS = (
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
)

#: Substrings that identify a model-not-found / permission error so the client
#: can fall through to the next model.
_MODEL_UNAVAILABLE = (
    "model_not_found",
    "not found",
    "not supported",
    "is not available",
    "decommissioned",
    "no such model",
)

#: HTTP statuses worth retrying. The Groq free tier is request- and
#: token-limited per minute, so a 429 is an expected, transient condition
#: rather than a failure, and must be backed off rather than surfaced.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})

#: A 400 that is really a sampling artifact. Some models occasionally emit a
#: malformed function call, which Groq reports as 400 "failed_generation".
#: The same request usually succeeds on retry because the model samples a
#: different, well-formed call, so these are retried. A genuine bad request
#: (unknown field, malformed body) is *not* matched and is not retried.
_RETRYABLE_400 = (
    "failed to call a function",
    "failed_generation",
    "failed to generate",
    # A model asked to call a tool sometimes answers directly instead. That is
    # a sampling artifact, and the retry usually produces a real tool call.
    "tool choice is required",
)


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back on bad input."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on bad input."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


#: Retry policy for transient provider failures. Kept short so a user is not
#: left waiting: the agent must still answer within its own request budget.
MAX_ATTEMPTS = max(1, _env_int("GROQ_MAX_ATTEMPTS", 3))
BACKOFF_BASE = max(0.0, _env_float("GROQ_BACKOFF_BASE", 1.0))
BACKOFF_MAX = max(BACKOFF_BASE, _env_float("GROQ_BACKOFF_MAX", 8.0))


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter, so retries do not resync.

    Args:
        attempt: The attempt number that just failed (1-based).

    Returns:
        Seconds to wait before the next attempt.
    """
    raw = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_MAX)
    return random.uniform(raw * 0.5, raw * 1.5)


class GroqError(RuntimeError):
    """A Groq call failed. Safe to report to a user in summarised form."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        user_message: Optional[str] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        #: A short, non-technical message suitable for the frontend.
        self.user_message = (
            user_message or "Sauti's AI service is unavailable right now."
        )
        self.retryable = retryable


@dataclass
class GroqFunctionCall:
    """A tool call requested by the model."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class GroqResult:
    """One turn's worth of model output."""

    text: str = ""
    function_calls: list[GroqFunctionCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    model: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    blocked: bool = False

    @property
    def wants_tools(self) -> bool:
        """True when the model asked for at least one tool."""
        return bool(self.function_calls)


class GroqClient:
    """Minimal async Groq client built on httpx."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.groq_base_url.rstrip("/")
        self._verified_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        """True when a Groq key is configured on the server."""
        return bool(self.settings.groq_api_key.strip())

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers. Sent to Groq only. Never logged, never returned."""
        return {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }

    def _models(self) -> list[str]:
        """Model ids to try, configured model first."""
        configured = (self.settings.groq_model or "").strip()
        ordered: list[str] = []
        if configured:
            ordered.append(configured)
        for name in FALLBACK_MODELS:
            if name not in ordered:
                ordered.append(name)
        if self._verified_model and self._verified_model in ordered:
            ordered.remove(self._verified_model)
            ordered.insert(0, self._verified_model)
        return ordered

    async def _post(self, payload: dict, timeout: Optional[float] = None) -> dict:
        """POST a chat-completions payload, or raise GroqError.

        Transient failures (rate limits, timeouts, 5xx) are retried with
        exponential backoff and full jitter. Permanent failures (bad key,
        unknown model) raise immediately so the caller can fall back to the
        next engine without wasting the user's time.
        """
        url = f"{self.base_url}/chat/completions"
        budget = timeout if timeout is not None else self.settings.groq_timeout
        last: Optional[GroqError] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=budget) as client:
                    response = await client.post(url, json=payload, headers=self.headers)
            except httpx.TimeoutException as exc:
                last = GroqError(
                    f"Groq request timed out: {exc}",
                    retryable=True,
                    user_message="Sauti's AI service took too long to respond. Please try again.",
                )
            except httpx.HTTPError as exc:
                last = GroqError(
                    f"Groq request failed: {exc}",
                    retryable=True,
                    user_message="Sauti could not reach its AI service. Please try again.",
                )
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except json.JSONDecodeError as exc:
                        raise GroqError("Groq returned a non-JSON response") from exc
                last = self._error_from_response(response)
                if not last.retryable:
                    raise last

            if attempt >= MAX_ATTEMPTS:
                break
            delay = _backoff_delay(attempt)
            logger.warning(
                "Groq request failed (status=%s), retrying in %.2fs (attempt %d/%d)",
                last.status, delay, attempt, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)

        raise last or GroqError("Groq request could not be completed")

    def _error_from_response(self, response: httpx.Response) -> GroqError:
        """Translate an HTTP failure into a GroqError with a safe message.

        Only the provider's own message is inspected; the key is never echoed
        back to the caller.
        """
        status = response.status_code
        detail = ""
        try:
            body = response.json()
            detail = (body.get("error") or {}).get("message", "") or ""
        except Exception:  # noqa: BLE001
            detail = response.text[:200]

        # 401 with a key-shaped message means the key itself is wrong.
        if status == 401 or "invalid_api_key" in detail.lower():
            return GroqError(
                "Groq rejected the configured API key",
                status=status,
                user_message="Sauti's AI service rejected its credentials.",
            )
        if status == 403:
            return GroqError(
                f"Groq denied the request: {detail}",
                status=status,
                user_message="Sauti's AI service denied the request.",
            )
        if status == 413:
            # The request is larger than the tier's input-token allowance.
            # Retrying the identical payload cannot help, so it is reported
            # immediately and the caller decides what to shrink.
            return GroqError(
                "Request exceeded the model input-token allowance",
                status=status,
                user_message="Sauti's AI service is busy right now. Please try again shortly.",
            )
        if status == 429:
            return GroqError(
                f"Groq rate limited: {detail}",
                status=status,
                retryable=True,
                user_message="Sauti's AI service is busy right now. Please try again shortly.",
            )
        if status >= 500:
            return GroqError(
                f"Groq server error: {detail}",
                status=status,
                retryable=True,
                user_message="Sauti's AI service is having trouble. Please try again.",
            )
        if status == 400 and any(marker in detail.lower() for marker in _RETRYABLE_400):
            # The model produced a malformed tool call. Retrying resamples it.
            return GroqError(
                f"Groq function-call generation failed: {detail}",
                status=status,
                retryable=True,
                user_message="Sauti's AI service is having trouble. Please try again.",
            )
        return GroqError(
            f"Groq returned HTTP {status}: {detail}",
            status=status,
            user_message="Sauti's AI service could not complete that request.",
        )

    def _is_model_unavailable(self, error: GroqError) -> bool:
        """True when the error means "try a different model"."""
        if error.status not in {400, 403, 404}:
            return False
        lowered = error.message.lower()
        return any(marker in lowered for marker in _MODEL_UNAVAILABLE)

    def build_payload(
        self,
        system: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tool_choice: Optional[str] = None,
    ) -> dict:
        """Build a chat-completions payload.

        Args:
            system: System instruction.
            messages: Chat history as [{"role": ..., "content": ...}].
            tools: Function tools, if the model may call tools.
            temperature: Optional override of the configured default.
            max_tokens: Optional override of the configured default.
            tool_choice: ``"auto"`` lets the model decide, ``"required"``
                forces it to call at least one tool. Ignored when no tools are
                supplied, because the API rejects that combination.

        Returns:
            The request body.
        """
        payload: dict[str, Any] = {
            "messages": (
                ([{"role": "system", "content": system}] + messages) if system else messages
            ),
            "temperature": (
                self.settings.groq_temperature if temperature is None else temperature
            ),
            "max_tokens": (
                self.settings.groq_max_tokens if max_tokens is None else max_tokens
            ),
        }
        if tools:
            payload["tools"] = tools
            # "required" is the guard that stops a confident model from
            # answering a live question out of memory. It is only legal when
            # tools are present.
            payload["tool_choice"] = tool_choice or "auto"
        return payload

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        tool_choice: Optional[str] = None,
        timeout: Optional[float] = None,
        remember: bool = True,
    ) -> dict:
        """Call the chat-completions endpoint with a complete message list.

        This is the primitive the tool-calling loop uses. Unlike
        :meth:`generate` it does not append the user message, so the caller
        controls conversation order exactly. The Groq free tier is limited by
        input tokens per minute, so re-sending a duplicated turn is not merely
        untidy: it roughly halves the number of requests a user can make.

        Args:
            messages: The full conversation, without the system prompt.
            tools: Function tools, if the model may call tools.
            temperature: Optional override of the configured default.
            max_tokens: Optional override of the configured default.
            model: Optional explicit model, used by the fallback walk.
            tool_choice: ``"auto"`` or ``"required"``. ``"required"`` forces
                the model to call a tool instead of answering from memory.
            timeout: Optional per-call override, used to give the cheap tier a
                tighter budget than the main model.
            remember: Whether a successful model is remembered as the
                starting point for later fallback walks. Helpers such as
                :meth:`complete_text` pass False so a cheap rewrite cannot
                silently downgrade subsequent reasoning calls.

        Returns:
            The parsed response body.

        Raises:
            GroqError: On any failure.
        """
        if not self.configured:
            raise GroqError(
                "GROQ_API_KEY is not set on the server",
                user_message="Sauti's AI service is not configured.",
            )

        candidates = [model] if model else self._models()
        last_error: Optional[GroqError] = None

        for candidate in candidates:
            payload = self.build_payload(
                "",
                messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
            )
            payload["model"] = candidate
            try:
                body = await self._post(payload, timeout=timeout)
            except GroqError as exc:
                last_error = exc
                if self._is_model_unavailable(exc):
                    logger.warning("Groq model %s unavailable, trying next", candidate)
                    continue
                raise
            if remember:
                self._verified_model = candidate
            return body

        raise last_error or GroqError("No usable Groq model could be reached")

    async def complete_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """Run a single cheap completion and return plain text.

        Used for the narrow, mechanical jobs — query rewriting and grounding
        checks — which do not justify the main reasoning model. Falls back to
        the main model when no separate fast model is configured, so the
        feature degrades rather than disappearing.

        This deliberately does not update ``_verified_model``. That attribute
        is the fallback walk's starting point for *main* turns; letting a
        cheap helper rewrite pin it would silently downgrade every subsequent
        reasoning call to the small model.

        Args:
            prompt: The user-turn instruction.
            temperature: Sampling temperature.
            max_tokens: Cap on the reply.
            model: Explicit model; defaults to the configured fast model.

        Returns:
            The model's text, or an empty string on failure.
        """
        target = model or self.settings.groq_fast_model or self.settings.groq_model
        body = await self.chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens or self.settings.groq_fast_max_tokens,
            model=target,
            timeout=self.settings.groq_fast_timeout,
            remember=False,
        )
        return self._parse(body, target).text

    async def generate(
        self,
        system: str,
        user_text: str,
        history: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> GroqResult:
        """Call Groq once and return text and/or tool calls.

        Args:
            system: System instruction.
            user_text: The user's message.
            history: Prior turns as [{"role": "user"|"assistant", "content": str}].
            tools: Function tools, if the model may call tools.
            temperature: Optional override of the configured default.
            max_tokens: Optional override of the configured default.

        Returns:
            A GroqResult.

        Raises:
            GroqError: On any failure, with a user-safe message attached.
        """
        if not self.configured:
            raise GroqError(
                "GROQ_API_KEY is not set on the server",
                user_message="Sauti's AI service is not configured.",
            )

        messages = self._to_messages(user_text, history)
        payload = self.build_payload(
            system, messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

        last_error: Optional[GroqError] = None
        for model in self._models():
            payload["model"] = model
            try:
                body = await self._post(payload)
            except GroqError as exc:
                last_error = exc
                if self._is_model_unavailable(exc):
                    logger.warning("Groq model %s unavailable, trying next", model)
                    continue
                raise
            self._verified_model = model
            return self._parse(body, model)

        raise last_error or GroqError("No usable Groq model could be reached")

    async def generate_json(
        self, system: str, user_text: str, history: Optional[list[dict]] = None
    ) -> Optional[dict]:
        """Ask for a strict JSON object. Returns None when parsing fails.

        The planner uses this to decide whether a tool is needed, so a bad
        reply must degrade to "no tool" rather than break the turn.
        """
        try:
            result = await self.generate(
                system=system
                + "\n\nRespond with a single valid JSON object and nothing else.",
                user_text=user_text,
                history=history,
                temperature=0.0,
                max_tokens=600,
            )
        except GroqError as exc:
            logger.warning("Groq planner call failed: %s status=%s", exc.message, exc.status)
            return None
        return _parse_json_object(result.text)

    @staticmethod
    def _to_messages(user_text: str, history: Optional[list[dict]]) -> list[dict]:
        """Convert chat history into Groq/OpenAI chat messages."""
        messages: list[dict] = []
        for turn in history or []:
            role = "assistant" if turn.get("role") in {"assistant", "model"} else "user"
            text = (turn.get("content") or "").strip()
            if text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def _parse(body: dict, model: str) -> GroqResult:
        """Extract text, tool calls and usage from a chat-completions response."""
        choices = body.get("choices") or []
        if not choices:
            raise GroqError("Groq returned no choices")

        message = choices[0].get("message") or {}
        # Some reasoning models split the answer across `content` and
        # `reasoning`; the user-facing text is always `content`.
        text = (message.get("content") or "").strip()

        calls: list[GroqFunctionCall] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            raw_args = function.get("arguments")
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    raw_args = {}
            calls.append(
                GroqFunctionCall(
                    name=str(name),
                    arguments=raw_args if isinstance(raw_args, dict) else {},
                )
            )

        usage = body.get("usage") or {}
        return GroqResult(
            text=text,
            function_calls=calls,
            finish_reason=choices[0].get("finish_reason"),
            model=body.get("model") or model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/webm",
        language: Optional[str] = None,
    ) -> str:
        """Transcribe recorded audio using Groq Whisper.

        This is the server-side fallback for browsers where
        `SpeechRecognition` is unavailable or fails (Firefox, headless Chrome,
        blocked Google endpoints). The audio is sent to Groq and the transcript
        comes back as plain text.

        Args:
            audio_bytes: Raw audio from the browser's MediaRecorder.
            mime_type: e.g. "audio/webm" or "audio/mp4".
            language: Optional hint such as "en", "sw" or "fr".

        Returns:
            The transcribed text.

        Raises:
            GroqError: On any failure.
        """
        if not self.configured:
            raise GroqError(
                "GROQ_API_KEY is not set on the server",
                user_message="Voice input is not configured on this server.",
            )
        if not audio_bytes:
            raise GroqError("No audio was captured", user_message="I didn't catch any audio.")

        # Keep the payload small: speech clips are short and a 20 MB inline
        # blob is rejected by the API.
        payload = audio_bytes[: self.settings.groq_max_audio_bytes]

        suffix = {
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/flac": "flac",
        }.get(mime_type.split(";")[0].strip(), "webm")

        data = {"model": self.settings.groq_stt_model, "response_format": "json"}
        if language:
            data["language"] = language

        try:
            async with httpx.AsyncClient(timeout=self.settings.groq_timeout) as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    files={"file": (f"clip.{suffix}", payload, mime_type)},
                    data=data,
                    headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise GroqError(
                f"Groq transcription timed out: {exc}",
                retryable=True,
                user_message="Voice input took too long. Please try again.",
            ) from exc
        except httpx.HTTPError as exc:
            raise GroqError(
                f"Groq transcription failed: {exc}",
                retryable=True,
                user_message="Voice input is unavailable right now. Please type instead.",
            ) from exc

        if response.status_code >= 400:
            raise self._error_from_response(response)

        try:
            return (response.json().get("text") or "").strip()
        except json.JSONDecodeError as exc:
            raise GroqError("Groq returned a non-JSON transcription") from exc

    async def health(self) -> dict:
        """Report reachability without ever revealing the key."""
        if not self.configured:
            return {"available": False, "reason": "GROQ_API_KEY is not set"}
        try:
            async with httpx.AsyncClient(timeout=min(15.0, self.settings.groq_timeout)) as c:
                response = await c.get(
                    f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.settings.groq_api_key}"}
                )
        except httpx.HTTPError as exc:
            return {"available": False, "reason": f"network error: {type(exc).__name__}"}

        if response.status_code >= 400:
            if response.status_code in (401, 403):
                return {
                    "available": False,
                    "status": response.status_code,
                    "reason": "credentials rejected",
                }
            return {
                "available": False,
                "status": response.status_code,
                "reason": "request rejected",
            }

        # Confirm the configured model is actually reachable, so a
        # misconfigured GROQ_MODEL is reported here rather than mid-answer.
        model = self._verified_model or self.settings.groq_model
        try:
            available = {m.get("id") for m in (response.json().get("data") or [])}
        except Exception:  # noqa: BLE001
            available = set()
        if available and model not in available:
            return {
                "available": True,
                "model": model,
                "reason": f"configured model {model} is not in this account's model list",
            }
        return {"available": True, "model": model}


def build_function_tools(tool_descriptors: list[dict]) -> list[dict]:
    """Convert tool registry descriptors into Groq/OpenAI function tools.

    Args:
        tool_descriptors: Output of ``ToolRegistry.describe()``.

    Returns:
        Tools in the OpenAI ``tools`` array shape Groq expects.
    """
    tools: list[dict] = []
    for tool in tool_descriptors:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool.get("input_schema")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return tools


def _parse_json_object(raw: str) -> Optional[dict]:
    """Extract the first JSON object from a model response, or return None."""
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None
