"""Voice input and output for JARVIS.

Three concerns, one module each:

- :mod:`jarvis.voice.mic` — capture frames from a microphone, with automatic
  backend selection and a clean "no microphone" signal.
- :mod:`jarvis.voice.vad` — decide when the user started and stopped speaking,
  so no fixed recording timer is ever needed.
- :mod:`jarvis.voice.tts` — speak text with Edge TTS, degrading to console
  output rather than failing silently.

The optional third-party dependencies (``webrtcvad``, ``edge-tts``,
``sounddevice``) are all imported defensively inside their modules, so
importing this package never fails on a machine that lacks one of them.
"""
from __future__ import annotations

from .mic import Microphone, MicrophoneUnavailable, MicStatus, detect_status
from .tts import Speaker, SpeechResult
from .vad import Segment, SpeechState, VoiceActivityDetector, apply_gain

__all__ = [
    "Microphone",
    "MicrophoneUnavailable",
    "MicStatus",
    "detect_status",
    "Speaker",
    "SpeechResult",
    "Segment",
    "SpeechState",
    "VoiceActivityDetector",
    "apply_gain",
]
