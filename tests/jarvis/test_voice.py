"""Tests for the voice layer: VAD segmentation, mic selection, TTS fallback."""
from __future__ import annotations

import asyncio
import wave

import pytest

from jarvis.config import Settings
from jarvis.voice.mic import MicrophoneUnavailable, detect_status, frame_size_bytes
from jarvis.voice.tts import Speaker
from jarvis.voice.vad import VoiceActivityDetector, apply_gain

from .conftest import noise_frames, silence_frames, speech_frames, stream


def collect(detector: VoiceActivityDetector, frames: list[bytes]) -> list:
    """Run the segmenter over a frame list and return the segments."""

    async def run():
        return [seg async for seg in detector.segments(stream(frames))]

    return asyncio.run(run())


class TestVad:
    """Speech boundary detection, on both engines."""

    def test_detects_one_utterance(self):
        detector = VoiceActivityDetector(Settings())
        frames = (
            silence_frames(10)
            + speech_frames(25)
            + silence_frames(60)
        )
        segments = collect(detector, frames)
        assert len(segments) == 1
        assert segments[0].speech_ms > 0

    def test_preserves_the_onset(self):
        """The first frames of speech must survive the start threshold.

        Dropping them would clip roughly a quarter of every real utterance and
        quietly degrade transcription accuracy.
        """
        detector = VoiceActivityDetector(Settings())
        speech = speech_frames(25)
        frames = silence_frames(10) + speech + silence_frames(60)
        segments = collect(detector, frames)
        assert len(segments) == 1
        captured = segments[0].pcm
        # The burst must start where the speech actually started, not later.
        assert captured[:2] == speech[0][:2]

    def test_separates_consecutive_utterances(self):
        detector = VoiceActivityDetector(Settings())
        frames = silence_frames(10)
        for _ in range(3):
            frames += speech_frames(25) + silence_frames(50)
        segments = collect(detector, frames)
        assert len(segments) == 3

    def test_silence_produces_nothing(self):
        detector = VoiceActivityDetector(Settings())
        assert collect(detector, silence_frames(100)) == []

    def test_quiet_noise_produces_no_false_positives(self):
        detector = VoiceActivityDetector(Settings())
        detector._vad = None  # exercise the energy engine
        detector.engine = "energy"
        # Low-amplitude broadband noise, well below the speech threshold.
        frames = noise_frames(60, amplitude=400, seed=3)
        assert collect(detector, frames) == []

    def test_quiet_noise_produces_no_false_positives_with_webrtc(self):
        detector = VoiceActivityDetector(Settings())
        assert collect(detector, noise_frames(60, amplitude=400, seed=3)) == []

    def test_energy_engine_detects_speech(self):
        """The dependency-free engine must work, not merely exist."""
        detector = VoiceActivityDetector(Settings())
        detector._vad = None
        detector.engine = "energy"
        frames = silence_frames(10) + speech_frames(25) + silence_frames(60)
        segments = collect(detector, frames)
        assert len(segments) == 1
        assert segments[0].engine == "energy"

    def test_discards_utterances_below_the_minimum(self):
        detector = VoiceActivityDetector(Settings())
        # 60 ms of speech is below the 400 ms floor and must be rejected.
        frames = silence_frames(10) + speech_frames(3) + silence_frames(60)
        assert collect(detector, frames) == []

    def test_segment_produces_a_valid_wav(self):
        detector = VoiceActivityDetector(Settings())
        frames = silence_frames(10) + speech_frames(25) + silence_frames(60)
        segment = collect(detector, frames)[0]
        with wave.open(__import__("io").BytesIO(segment.to_wav())) as handle:
            assert handle.getframerate() == 16000
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getnframes() > 0

    def test_frame_size_matches_configuration(self):
        settings = Settings(sample_rate=16000, vad_frame_ms=20)
        assert frame_size_bytes(settings) == 16000 * 2 * 20 // 1000


class TestGain:
    """Input gain adjustment."""

    def test_scales_amplitude(self):
        raw = speech_frames(2)
        pcm = b"".join(raw)
        louder = apply_gain(pcm, 1.5)
        assert louder != pcm
        assert len(louder) == len(pcm)

    def test_clips_rather_than_wrapping(self):
        import array

        pcm = array.array("h", [30000, -30000]).tobytes()
        scaled = array.array("h")
        scaled.frombytes(apply_gain(pcm, 4.0))
        assert max(scaled) <= 32767
        assert min(scaled) >= -32768

    def test_unit_gain_is_a_noop(self):
        pcm = b"".join(speech_frames(2))
        assert apply_gain(pcm, 1.0) == pcm


class TestMicSelection:
    """Backend selection without opening a device."""

    def test_status_is_reported(self):
        status = detect_status(Settings())
        assert status.backend in {"sounddevice", "arecord", "none"}
        assert status.frame_bytes > 0

    def test_unavailable_mic_raises_clearly(self):
        from jarvis.voice import mic as mic_mod

        original = mic_mod.shutil.which
        mic_mod._SOUNDDEVICE_AVAILABLE = False
        mic_mod.shutil.which = lambda name: None
        try:
            microphone = mic_mod.Microphone(Settings())
            with pytest.raises(MicrophoneUnavailable):
                asyncio.run(microphone.start())
        finally:
            mic_mod.shutil.which = original

    def test_missing_mic_is_reported_as_unusable(self):
        from jarvis.voice import mic as mic_mod

        original = mic_mod.shutil.which
        mic_mod._SOUNDDEVICE_AVAILABLE = False
        mic_mod.shutil.which = lambda name: None
        try:
            assert detect_status(Settings()).usable is False
        finally:
            mic_mod.shutil.which = original


class TestSpeakerFallback:
    """TTS degrades to the console instead of failing."""

    def test_disabled_tts_prints_to_stdout(self, capsys):
        speaker = Speaker(Settings(tts_enabled=False, tts_player=""))
        result = asyncio.run(speaker.speak("All systems nominal, sir."))
        assert result.ok is True
        assert result.mode == "console"
        assert "All systems nominal" in capsys.readouterr().out

    def test_empty_text_is_a_noop(self):
        speaker = Speaker(Settings(tts_enabled=False))
        assert asyncio.run(speaker.speak("   ")).ok is True

    def test_missing_edge_tts_prints_instead(self, capsys):
        from jarvis.voice import tts as tts_mod

        original = tts_mod._EDGE_AVAILABLE
        tts_mod._EDGE_AVAILABLE = False
        try:
            speaker = Speaker(Settings(tts_enabled=True, tts_player=""))
            result = asyncio.run(speaker.speak("Systems are degraded."))
            assert result.ok is True
            assert result.mode == "console"
            assert "Systems are degraded" in capsys.readouterr().out
        finally:
            tts_mod._EDGE_AVAILABLE = original

    def test_unknown_player_degrades_to_console_only(self):
        speaker = Speaker(Settings(tts_player="definitely-not-a-player"))
        assert speaker.can_play is False


class TestLatencyInstrumentation:
    """The numbers used to judge latency must be real, not proxies."""

    def test_first_byte_is_measured_not_total(self, monkeypatch, tmp_path):
        """first_byte_ms must reflect when audio started, not when it ended."""
        import asyncio as aio

        speaker = Speaker(Settings(tts_enabled=True, tts_player="ffplay"))
        assert speaker.can_play is True

        # Speech is cached on disk by content hash under a shared temp dir, and
        # a cache hit returns the file before streaming is attempted. Point the
        # cache at a private directory so this test measures the streaming path
        # on a warm run as well as a cold one.
        speaker._cache_dir = tmp_path / "tts-cache"

        class FakeCommunicate:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self):
                for _ in range(4):
                    yield {"type": "audio", "data": b"\x00" * 512}
                    await aio.sleep(0.05)

        import jarvis.voice.tts as tts_mod

        monkeypatch.setattr(tts_mod, "_EDGE_AVAILABLE", True)
        monkeypatch.setattr(tts_mod.edge_tts, "Communicate", FakeCommunicate, raising=False)

        result = aio.run(speaker.speak("Measuring the first byte, sir."))
        assert result.ok is True
        assert result.mode == "stream"
        # The first byte arrives after one chunk, well before the call ends.
        assert 0 < result.first_byte_ms < result.duration_ms

    def test_warm_up_never_raises(self, monkeypatch):
        """A failed warm-up must not stop the agent from starting."""
        import asyncio as aio

        import jarvis.voice.tts as tts_mod

        class Exploding:
            def __init__(self, *args, **kwargs):
                pass

            def stream(self):
                async def gen():
                    raise RuntimeError("edge is down")
                    yield  # pragma: no cover

                return gen()

        monkeypatch.setattr(tts_mod, "_EDGE_AVAILABLE", True)
        monkeypatch.setattr(tts_mod.edge_tts, "Communicate", Exploding, raising=False)

        speaker = Speaker(Settings())
        aio.run(speaker.warm_up())  # must not raise
