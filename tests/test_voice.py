"""Tests for server-side speech-to-text and the voice fallback.

Covers the transcription endpoint, the Gemini audio path, and the guarantee
that the API key never leaks through voice.
"""
from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path

import pytest

from backend.config.settings import Settings
from backend.integrations.gemini import GeminiClient, GeminiError

FAKE_KEY = "AIzaSyTEST-TRANSCRIPTION-KEY-NOT-REAL-0000000000"

#: A one-second silent 16 kHz mono WAV, the smallest valid audio we accept.
def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def tone_wav(seconds: float = 1.0, rate: int = 16000, freq: float = 220.0) -> bytes:
    """A quiet but non-silent tone, standing in for real captured speech.

    The endpoint rejects digital silence before calling a provider, so tests
    that exercise the provider path must supply audio that is actually audible.
    """
    import math
    import struct

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(int(rate * seconds)):
            value = int(6000 * math.sin(2 * math.pi * freq * index / rate))
            frames += struct.pack("<h", value)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def run(coro):
    return asyncio.run(coro)


async def _ok(text: str):
    """A stand-in for a successful provider transcription.

    Referenced by several fakes below; previously it was referenced but never
    defined, which only stayed hidden because those code paths were
    unreachable.
    """
    return text


class TestTranscriptionEndpoint:
    def test_requires_audio(self, client):
        assert client.post("/api/sauti/transcribe", data={}).status_code == 400

    def test_rejects_unsupported_type(self, client):
        response = client.post(
            "/api/sauti/transcribe",
            data={"audio": (io.BytesIO(b"nope"), "clip.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 415

    def test_reports_when_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        from backend.config.settings import get_settings

        get_settings(refresh=True)
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 503
            assert response.get_json()["configured"] is False
        finally:
            get_settings(refresh=True)

    def test_rejects_oversized_audio(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        from backend.config.settings import get_settings

        get_settings(refresh=True)
        monkeypatch.setattr(
            "backend.integrations.gemini.GeminiClient.transcribe",
            lambda self, **kw: _ok(""),
        )
        try:
            # A 9 MB payload exceeds the 8 MB default cap.
            big = b"\x00" * (9 * 1024 * 1024)
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(big), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 413
        finally:
            get_settings(refresh=True)

    def test_transcribes_and_returns_text(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        from backend.config.settings import get_settings

        get_settings(refresh=True)
        captured = {}

        async def fake(self, audio_bytes, mime_type="audio/webm", language=None):
            captured["bytes"] = len(audio_bytes)
            captured["mime"] = mime_type
            captured["language"] = language
            return "Natafuta maharagwe ya Yanza huko Samburu."

        monkeypatch.setattr(
            "backend.integrations.gemini.GeminiClient.transcribe", fake
        )
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={
                    "audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav"),
                    "language": "sw",
                },
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            assert "Yanza" in response.get_json()["text"]
            assert captured["language"] == "sw"
            assert captured["bytes"] > 0
        finally:
            get_settings(refresh=True)

    def test_gemini_failure_is_clean(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        from backend.config.settings import get_settings

        get_settings(refresh=True)

        async def failing(self, **kwargs):
            raise GeminiError("boom", status=429, user_message="Sauti is busy. Try again.")

        monkeypatch.setattr(
            "backend.integrations.gemini.GeminiClient.transcribe", failing
        )
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 502
            body = response.get_data(as_text=True)
            assert "busy" in body
            assert FAKE_KEY not in body
            assert "Traceback" not in body
        finally:
            get_settings(refresh=True)


class TestGeminiAudioRequest:
    def test_sends_inline_audio(self, monkeypatch):
        captured = {}

        async def fake_post(self, model, payload):
            captured["payload"] = payload
            return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

        monkeypatch.setattr(GeminiClient, "_post", fake_post)
        client = GeminiClient(Settings(gemini_api_key=FAKE_KEY))
        result = run(client.transcribe(silent_wav(), "audio/wav", "sw"))
        assert result == "hello"

        part = captured["payload"]["contents"][0]["parts"][0]
        assert part["inline_data"]["mime_type"] == "audio/wav"
        assert part["inline_data"]["data"]
        # The language hint is included so Kiswahili is not translated.
        assert "Kiswahili" in captured["payload"]["contents"][0]["parts"][1]["text"]

    def test_empty_audio_raises(self):
        client = GeminiClient(Settings(gemini_api_key=FAKE_KEY))
        with pytest.raises(GeminiError) as info:
            run(client.transcribe(b"", "audio/wav"))
        assert "didn't catch any audio" in info.value.user_message

    def test_unconfigured_client_raises_friendly(self):
        client = GeminiClient(Settings())
        with pytest.raises(GeminiError) as info:
            run(client.transcribe(silent_wav(), "audio/wav"))
        assert "not configured" in info.value.user_message.lower()

    def test_key_never_in_error(self):
        client = GeminiClient(Settings(gemini_api_key=FAKE_KEY))
        error = GeminiError(f"failed for key {FAKE_KEY}", status=500, user_message="Busy.")
        assert FAKE_KEY not in error.user_message


class TestVoiceFallbackContract:
    # These two assert that the frontend client keeps calling this endpoint
    # correctly, by reading the frontend's own source. The frontend lives in a
    # separate repository, so these only run when both are present in one
    # checkout; in a standalone backend clone they are skipped rather than
    # failed, because there is nothing for them to inspect.
    _frontend_root = Path("sautipay/src")

    @pytest.mark.skipif(
        not (_frontend_root / "components" / "SautiVoiceModal.jsx").is_file(),
        reason="frontend sources not in this checkout (separate repository)",
    )
    def test_frontend_prefers_server_recording(self):
        """Recording is preferred so voice works without SpeechRecognition."""
        source = (
            self._frontend_root / "components" / "SautiVoiceModal.jsx"
        ).read_text(encoding="utf-8")
        assert "recorderSupported" in source
        assert "startRecording" in source
        # The network error must fall back rather than dead-end.
        assert "'network'" in source
        assert "startRecording()" in source

    @pytest.mark.skipif(
        not (_frontend_root / "services" / "marketplace.js").is_file(),
        reason="frontend sources not in this checkout (separate repository)",
    )
    def test_client_calls_transcribe_endpoint(self):
        source = (
            self._frontend_root / "services" / "marketplace.js"
        ).read_text(encoding="utf-8")
        assert "/api/sauti/transcribe" in source
        assert "FormData" in source

    def test_endpoint_is_registered(self, app):
        rules = {str(r) for r in app.url_map.iter_rules()}
        assert "/api/sauti/transcribe" in rules


class TestSilenceGuard:
    """Silent clips are rejected locally, before spending an API call."""

    def test_silence_is_not_sent_to_a_provider(self, client, monkeypatch):
        from backend.config.settings import get_settings

        monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        get_settings(refresh=True)

        called = {"n": 0}

        def fake(self, **kwargs):
            called["n"] += 1
            return _ok("Thank you.")

        monkeypatch.setattr("backend.integrations.groq.GroqClient.transcribe", fake)
        monkeypatch.setattr("backend.integrations.gemini.GeminiClient.transcribe", fake)
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(silent_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            assert response.get_json()["text"] == ""
            assert called["n"] == 0, "silence must not reach a provider"
        finally:
            get_settings(refresh=True)

    def test_audible_audio_does_reach_the_provider(self, client, monkeypatch):
        from backend.config.settings import get_settings

        monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        get_settings(refresh=True)

        called = {"n": 0}

        def fake(self, **kwargs):
            called["n"] += 1
            return _ok("Nairobi")

        monkeypatch.setattr("backend.integrations.groq.GroqClient.transcribe", fake)
        monkeypatch.setattr("backend.integrations.gemini.GeminiClient.transcribe", fake)
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            assert response.get_json()["text"] == "Nairobi"
            assert called["n"] == 1
        finally:
            get_settings(refresh=True)

    def test_groq_is_the_preferred_transcription_engine(self, client, monkeypatch):
        from backend.config.settings import get_settings

        monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        get_settings(refresh=True)

        used = []

        def groq_fake(self, **kwargs):
            used.append("groq")
            return _ok("habari")

        def gemini_fake(self, **kwargs):
            used.append("gemini")
            return _ok("should not run")

        monkeypatch.setattr("backend.integrations.groq.GroqClient.transcribe", groq_fake)
        monkeypatch.setattr("backend.integrations.gemini.GeminiClient.transcribe", gemini_fake)
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert used == ["groq"]
            assert response.get_json()["engine"] == "groq"
        finally:
            get_settings(refresh=True)

    def test_gemini_is_used_when_groq_fails(self, client, monkeypatch):
        from backend.config.settings import get_settings
        from backend.integrations.groq import GroqError

        monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        get_settings(refresh=True)

        def groq_fake(self, **kwargs):
            raise GroqError("boom", user_message="Groq down.")

        def gemini_fake(self, **kwargs):
            return _ok("from gemini")

        monkeypatch.setattr("backend.integrations.groq.GroqClient.transcribe", groq_fake)
        monkeypatch.setattr("backend.integrations.gemini.GeminiClient.transcribe", gemini_fake)
        try:
            response = client.post(
                "/api/sauti/transcribe",
                data={"audio": (io.BytesIO(tone_wav()), "clip.wav", "audio/wav")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            assert response.get_json()["text"] == "from gemini"
        finally:
            get_settings(refresh=True)

    def test_health_reports_groq_without_the_key(self, client, monkeypatch):
        from backend.config.settings import get_settings

        monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
        get_settings(refresh=True)
        try:
            body = client.get("/api/sauti/health").get_json()
            assert body["groqConfigured"] is True
            assert body["model"] is not None
            assert FAKE_KEY not in client.get("/api/sauti/health").get_data(as_text=True)
        finally:
            get_settings(refresh=True)
