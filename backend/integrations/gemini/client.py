"""Google Gemini client for SAUTI.

The API key is read from the server environment and is only ever sent to
Google. It is never logged, never returned by an endpoint, and never reaches
the browser.

Gemini is used for two things:
  1. generating the final answer, and
  2. deciding which tools to call, via its function-calling API.

Function calls are *requests*. They are validated and executed by the existing
tool registry, never by the model.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ...config.settings import Settings

logger = logging.getLogger(__name__)

#: Models tried in order when the configured model is unavailable. Google
#: retires model names, so the list is deliberately generous and the client
#: falls through on a not-found response rather than hard-failing.
FALLBACK_MODELS = (
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
)

#: Substrings that identify a model-not-found / permission error so the client
#: can fall through to the next model.
_MODEL_UNAVAILABLE = ("not found", "not supported", "is not found", "permission")


class GeminiError(RuntimeError):
    """A Gemini call failed. Safe to report to a user in summarised form."""

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
        self.user_message = user_message or "Sauti's AI service is unavailable right now."
        self.retryable = retryable


@dataclass
class GeminiFunctionCall:
    """A tool call requested by the model."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class GeminiResult:
    """One turn's worth of model output."""

    text: str = ""
    function_calls: list[GeminiFunctionCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    model: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    blocked: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.function_calls)


class GeminiClient:
    """Minimal async Gemini client built on httpx.

    Deliberately dependency-free: the request shape is small and stable, and
    avoiding the SDK keeps the install small and the key handling explicit.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.gemini_base_url.rstrip("/")
        self._verified_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key.strip())

    def _models(self) -> list[str]:
        configured = (self.settings.gemini_model or "").strip()
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

    async def _post(self, model: str, payload: dict) -> dict:
        """POST to Gemini and return the parsed body, or raise GeminiError."""
        url = f"{self.base_url}/models/{model}:generateContent"
        headers = {
            # Sent to Google only. Never logged, never returned.
            "x-goog-api-key": self.settings.gemini_api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.gemini_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise GeminiError(
                f"Gemini request timed out: {exc}",
                retryable=True,
                user_message="Sauti's AI service took too long to respond. Please try again.",
            ) from exc
        except httpx.HTTPError as exc:
            raise GeminiError(
                f"Gemini request failed: {exc}",
                retryable=True,
                user_message="Sauti could not reach its AI service. Please try again.",
            ) from exc

        if response.status_code >= 400:
            raise self._error_from_response(response)

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise GeminiError("Gemini returned a non-JSON response") from exc

    def _error_from_response(self, response: httpx.Response) -> GeminiError:
        status = response.status_code
        detail = ""
        try:
            body = response.json()
            detail = (body.get("error") or {}).get("message", "") or ""
        except Exception:  # noqa: BLE001
            detail = response.text[:200]

        # 400/403 with a key-shaped message means the key itself is wrong.
        if "API key not valid" in detail or "API_KEY_INVALID" in detail:
            return GeminiError(
                "Gemini rejected the configured API key",
                status=status,
                user_message="Sauti's AI service rejected its credentials.",
            )
        if status in {401, 403}:
            return GeminiError(
                f"Gemini denied the request: {detail}",
                status=status,
                user_message="Sauti's AI service denied the request.",
            )
        if status == 429:
            return GeminiError(
                f"Gemini rate limited: {detail}",
                status=status,
                retryable=True,
                user_message="Sauti's AI service is busy right now. Please try again shortly.",
            )
        if status >= 500:
            return GeminiError(
                f"Gemini server error: {detail}",
                status=status,
                retryable=True,
                user_message="Sauti's AI service is having trouble. Please try again.",
            )
        return GeminiError(
            f"Gemini returned HTTP {status}: {detail}",
            status=status,
            user_message="Sauti's AI service could not complete that request.",
        )

    def _is_model_unavailable(self, error: GeminiError) -> bool:
        if error.status not in {400, 403, 404}:
            return False
        lowered = error.message.lower()
        return any(marker in lowered for marker in _MODEL_UNAVAILABLE)

    def build_payload(
        self,
        system: str,
        contents: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Build a generateContent payload."""
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": (
                    self.settings.gemini_temperature if temperature is None else temperature
                ),
                "maxOutputTokens": (
                    self.settings.gemini_max_tokens if max_tokens is None else max_tokens
                ),
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = tools
        return payload

    async def generate(
        self,
        system: str,
        user_text: str,
        history: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> GeminiResult:
        """Call Gemini once and return text and/or function calls.

        Args:
            system: System instruction.
            user_text: The user's message.
            history: Prior turns as [{"role": "user"|"model", "content": str}].
            tools: Function declarations, if the model may call tools.

        Raises:
            GeminiError: On any failure, with a user-safe message attached.
        """
        if not self.configured:
            raise GeminiError(
                "GEMINI_API_KEY is not set on the server",
                user_message="Sauti's AI service is not configured.",
            )

        contents = self._to_contents(user_text, history)
        payload = self.build_payload(
            system, contents, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

        last_error: Optional[GeminiError] = None
        for model in self._models():
            try:
                body = await self._post(model, payload)
            except GeminiError as exc:
                last_error = exc
                if self._is_model_unavailable(exc):
                    logger.warning("Gemini model %s unavailable, trying next", model)
                    continue
                raise
            self._verified_model = model
            return self._parse(body, model)

        raise last_error or GeminiError("No usable Gemini model could be reached")

    @staticmethod
    def _to_contents(user_text: str, history: Optional[list[dict]]) -> list[dict]:
        """Convert chat history into Gemini's `contents` shape."""
        contents: list[dict] = []
        for turn in history or []:
            role = "model" if turn.get("role") == "assistant" else "user"
            text = (turn.get("content") or "").strip()
            if text:
                contents.append({"role": role, "parts": [{"text": text}]})
        contents.append({"role": "user", "parts": [{"text": user_text}]})
        return contents

    @staticmethod
    def _parse(body: dict, model: str) -> GeminiResult:
        """Extract text, function calls and usage from a Gemini response."""
        candidates = body.get("candidates") or []
        if not candidates:
            prompt_feedback = body.get("promptFeedback") or {}
            block_reason = prompt_feedback.get("blockReason")
            if block_reason:
                return GeminiResult(
                    text="", model=model, blocked=True,
                    finish_reason=f"blocked:{block_reason}",
                )
            raise GeminiError("Gemini returned no candidates")

        candidate = candidates[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []

        texts: list[str] = []
        calls: list[GeminiFunctionCall] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"])
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name"):
                args = call.get("args")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append(
                    GeminiFunctionCall(
                        name=str(call["name"]),
                        arguments=args if isinstance(args, dict) else {},
                    )
                )

        usage = body.get("usageMetadata") or {}
        return GeminiResult(
            text="\n".join(texts).strip(),
            function_calls=calls,
            finish_reason=candidate.get("finishReason"),
            model=model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/webm",
        language: Optional[str] = None,
    ) -> str:
        """Transcribe recorded audio.

        This is the server-side fallback for browsers where
        `SpeechRecognition` is unavailable or fails (Firefox, headless
        Chrome, blocked Google endpoints). The audio is sent to Gemini and the
        transcript comes back as plain text.

        Args:
            audio_bytes: Raw audio from the browser's MediaRecorder.
            mime_type: e.g. "audio/webm" or "audio/mp4".
            language: Optional hint such as "en", "sw" or "fr".

        Returns:
            The transcribed text.

        Raises:
            GeminiError: On any failure.
        """
        if not self.configured:
            raise GeminiError(
                "GEMINI_API_KEY is not set on the server",
                user_message="Voice input is not configured on this server.",
            )
        if not audio_bytes:
            raise GeminiError("No audio was captured", user_message="I didn't catch any audio.")

        # Keep the payload small: speech clips are short and a 20 MB inline
        # blob is rejected by the API.
        payload = audio_bytes[: self.settings.gemini_max_audio_bytes]
        hint = (
            " The user is speaking Kiswahili; transcribe in Kiswahili."
            if language == "sw"
            else " The user is speaking French; transcribe in French."
            if language == "fr"
            else ""
        )
        request = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(payload).decode("ascii"),
                            }
                        },
                        {
                            "text": (
                                "Transcribe this speech verbatim. Return only the "
                                "spoken words, no commentary, no quotes, no "
                                "translation." + hint
                            )
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 800},
        }

        last_error: Optional[GeminiError] = None
        for model in self._models():
            try:
                body = await self._post(model, request)
            except GeminiError as exc:
                last_error = exc
                if self._is_model_unavailable(exc):
                    continue
                if exc.status == 400 and "inline" in exc.message.lower():
                    raise GeminiError(
                        "Gemini rejected the audio payload",
                        status=400,
                        user_message="That recording could not be read. Please try again.",
                    ) from exc
                raise
            self._verified_model = model
            return self._parse(body, model).text

        raise last_error or GeminiError("No usable Gemini model could transcribe the audio")

    async def health(self) -> dict:
        """Report reachability without ever revealing the key."""
        if not self.configured:
            return {"available": False, "reason": "GEMINI_API_KEY is not set"}
        try:
            async with httpx.AsyncClient(timeout=min(15.0, self.settings.gemini_timeout)) as c:
                response = await c.get(
                    f"{self.base_url}/models",
                    headers={"x-goog-api-key": self.settings.gemini_api_key},
                )
        except httpx.HTTPError as exc:
            return {"available": False, "reason": f"network error: {type(exc).__name__}"}

        if response.status_code >= 400:
            try:
                message = (response.json().get("error") or {}).get("message", "")
            except Exception:  # noqa: BLE001
                message = ""
            return {
                "available": False,
                "status": response.status_code,
                "reason": message[:120] or "request rejected",
            }
        return {"available": True, "model": self._verified_model or self.settings.gemini_model}


def build_function_tools(tool_descriptors: list[dict]) -> list[dict]:
    """Convert tool registry descriptors into Gemini function declarations."""
    declarations = []
    for tool in tool_descriptors:
        declarations.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("input_schema")
                or {"type": "object", "properties": {}},
            }
        )
    return [{"functionDeclarations": declarations}] if declarations else []
