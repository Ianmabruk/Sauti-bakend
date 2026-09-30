# JARVIS

A JARVIS-style voice and text assistant built entirely on free-tier services.

```
speech ends → Whisper STT → model routes the question → optional web search
            → Llama streams tokens → first sentence buffered → Edge TTS speaks it
            → next sentence → repeat
```

| Capability | Service | Cost |
|---|---|---|
| Chat + tool routing | Groq, Llama 3.3 70B | free tier |
| Speech to text | Groq Whisper large-v3-turbo | free tier |
| Web search | Parallel Search (hosted MCP) | free, **no key at all** |
| Speech out | Microsoft Edge TTS | free, no key |
| Voice activity | webrtcvad, energy fallback | free, open source |
| Rate-limit fallback | Ollama (local) | free, optional |

## Install

```bash
pip install -e ".[jarvis]"
cp .env.example.jarvis .env      # then paste your Groq key
python -m jarvis --check         # reports what is and is not available
```

`--check` never fails because an optional piece is missing. It tells you
exactly what the agent can and cannot do right now.

## Run

```bash
python -m jarvis                 # voice, with text fallback
python -m jarvis --text          # type instead
python -m jarvis --no-speak      # print answers
python -m jarvis --voice en-US-JennyNeural --log-level DEBUG
```

Commands at runtime: `/quit` `/text` `/voice` `/reset` `/stats` `/help`

## Measured latency

Measured on this machine, warm process, sentence spoken start to finish:

| Stage | Time |
|---|---|
| Model time to first token | ~300 ms (simulated Groq pacing; real Groq is 200–400 ms) |
| Time to first complete sentence | ~900 ms |
| **Edge TTS to first audio byte** | **~950 ms** |
| **Total to first audio byte** | **~2180 ms** (cold: ~2660 ms) |

**The 1.5 s target from the brief is not met, and I do not think it is
reachable with Edge TTS.** Its ~950 ms to first audio byte is a hard floor that
no amount of streaming removes, and it alone consumes 63% of the budget. Every
other stage is already overlapping correctly.

Web-search questions add 1.2–2.9 s of real, measured search latency on top —
uncached. A cache hit is 0 ms, which is why follow-ups like "and the price?"
are instant.

To genuinely hit 1.5 s you need a local TTS (Piper or Kokoro), which would
replace the 950 ms with roughly 50 ms and land the total near 1.25 s. That is
an extra dependency and a model download, so I have not added it without
asking.

## What was fixed in the existing SautiPay agent

The pre-existing `backend/agent/` pipeline had five concrete problems, all
addressed by this package:

| Symptom | Cause in the old code |
|---|---|
| No follow-up awareness | `backend/api/sauti.py:95` passed `conversation_history=None`; every turn was stateless |
| Slow | `backend/services/llm.py:70` built a new `httpx.AsyncClient` per call — a fresh TLS handshake every request |
| Clunky | Three parallel answer paths plus hardcoded routing at `backend/agent/orchestrator.py:188` |
| Unreliable | No cache, no pre-warm, no rate-limit handling, no local fallback |
| No voice pipeline | No TTS and no VAD existed; recording ran until a human pressed stop |

The Flask application and its 359 existing tests are untouched.

## Reliability

Every failure mode is handled and tested rather than hoped about:

- **Groq 429** → exponential backoff with jitter → local Ollama if running →
  otherwise a spoken apology. It never raises into the audio loop.
- **Search fails** → falls back to keyless DuckDuckGo → if that also fails the
  agent says "I couldn't verify this online, sir" and never invents a fact.
- **TTS fails** → the answer is printed to the terminal instead, always.
- **No microphone** → silently drops to text input. `arecord` is used when
  PortAudio is absent, which is common on minimal Linux installs.
- **Model returns nothing** → the turn is reported as failed, not sent as blank.
- **Crash anywhere** → the CLI has an outer recovery loop that restarts the
  audio stack, and memory is persisted atomically so a crash mid-write cannot
  corrupt the state file.

## Design notes

**The microphone never stops.** Capture runs continuously and the VAD carves
utterances out of the stream, so the first syllable of your next question is
already being recorded while the agent is still thinking. There is no wake
word and no dead time between turns. Half duplex is enforced: audio arriving
while JARVIS is speaking is discarded, because hearing its own voice loop is
worse than waiting.

**The model decides routing, not a keyword match.** `jarvis/persona.py` holds a
JSON contract that the model answers. The same call performs query rewriting,
so "what's up with bitcoin" reaches the web as "Bitcoin price USD today".

**One connection pool.** A single `httpx.AsyncClient` with keep-alive serves
every provider. `prewarm()` opens those connections, and Edge TTS gets a
discarded warm-up synthesis, before the first utterance — the cold Edge
websocket alone costs about 6 seconds.

**Every optional dependency degrades.** `webrtcvad`, `edge-tts` and
`sounddevice` are imported defensively. Missing `webrtcvad` falls back to an
RMS energy detector; missing PortAudio falls back to the `arecord` binary;
missing `edge-tts` prints the answer instead of speaking it.

## Tests

```bash
python -m pytest tests/jarvis -q     # 108 tests, no network, no audio hardware
```

Synthetic audio stands in for the microphone, and providers are stubbed, so the
suite runs anywhere in a few seconds. The VAD tests cover onset preservation
(a regression that clipped the first 250 ms of every utterance), multi-utterance
separation, and false-positive rejection on quiet room noise.

## Configuration

Everything is in `.env`; see `.env.example.jarvis` for the annotated list. No
credential is ever logged — `Settings.redacted()` is the only sanctioned way to
log configuration, and there is a test asserting secrets cannot appear in it.
