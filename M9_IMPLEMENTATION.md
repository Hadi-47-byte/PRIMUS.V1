# Milestone 9 — Voice Conversation Orchestration

## Status: COMPLETE

---

## 1. Architecture

```
                   Audio file
                      ↓
                  STTService
                      ↓
                 Transcript
                      ↓
                 TutorEngine
                      ↓
             Physics Curriculum
                      ↓
                      LLM
                      ↓
                TutorResponse
                 /          \
                ↓            ↓
              TTS        ExpressionMapper
                ↓            ↓
              Audio      RobotController
                            ↓
                         WebSocket
                            ↓
                           ESP32
```

- **TutorResponse** is the central semantic result.
- TTS and robot output are **sibling output channels**.
- Robot is optional. TTS is optional.
- One LLM call per conversation turn.
- No direct LLM → hardware path.

---

## 2. VoiceConversationResult

```python
class VoiceConversationResult(BaseModel):
    session_id: str
    transcript: str
    transcript_language: str | None
    tutor_response: TutorResponse
    audio_path: str | None          # None if TTS failed
    audio_format: str | None        # e.g. "mp3"
    robot_state: str | None         # Intended semantic state
    stt_latency_ms: int | None
    tutor_latency_ms: int | None
    tts_latency_ms: int | None
    robot_latency_ms: int | None
    total_latency_ms: int | None
```

---

## 3. VoiceConversationService

```python
class VoiceConversationService:
    def __init__(self, stt_service, engine, tts_service, robot_orchestrator): ...
    async def process_audio(self, audio_path, session_id, language=None) -> VoiceConversationResult: ...
```

The service is a **thin coordination layer** — it delegates to each component without duplicating their logic:

| Component | Role |
|-----------|------|
| STTService | Audio → text transcription |
| TutorEngine | Transcript → TutorResponse (one LLM call) |
| TTSService | TutorResponse text → audio file |
| RobotOrchestrator | TutorResponse → mapped robot state → ESP32 |

---

## 4. Failure Isolation

| Stage fails | Behavior |
|-------------|----------|
| **STT** | `STTProviderError` raised → no TutorEngine call |
| **Tutor** | `LLMProviderError` raised → no TTS/robot attempt |
| **TTS** | Non-fatal → `audio_path = None`, tutor response preserved, robot still executes |
| **Robot** | Non-fatal → `robot_state` may still be set (intended state), tutor + audio preserved |

The robot is **never** a critical dependency of the tutor.

---

## 5. Pipeline Order

```
1. STTService.transcribe(audio_path, language)     → STTResult
2. TutorEngine.chat(transcript, session_id)         → TutorResponse
3. TTSService.synthesize(response_text, language)   → TTSResult   (non-fatal)
4. RobotOrchestrator.dispatch(tutor_response)       → bool        (non-fatal)
```

No extra LLM calls. No language detection LLM. No robot expression LLM.

---

## 6. Session Continuity

The same `session_id` flows through the entire pipeline. Multi-turn voice conversations work via the existing session mechanism:

```
Audio 1: "What is force?"        → session "demo" created
Audio 2: "Explain it simply."    → session "demo" reused, context preserved
```

No second voice-specific memory system is introduced.

---

## 7. Language Handling

- Input language hint (`en`, `ur`, `auto`) is passed to `STTService`.
- STT result language is preserved in `VoiceConversationResult`.
- TutorEngine produces `TutorResponse.language`.
- TTS uses `TutorResponse.language` for voice selection.
- No translation logic is added.

---

## 8. Timing / Latency

Lightweight per-stage timing is recorded:

| Field | Description |
|-------|-------------|
| `stt_latency_ms` | STT transcription time |
| `tutor_latency_ms` | TutorEngine + LLM time |
| `tts_latency_ms` | TTS synthesis time |
| `robot_latency_ms` | Robot dispatch time |
| `total_latency_ms` | Wall-clock total |

---

## 9. CLI

```bash
# Full voice pipeline from a local audio file
python -m primus.voice.conversation audio.wav --session demo --language en

# Urdu
python -m primus.voice.conversation audio.mp3 -s demo -l ur

# Auto-detect language
python -m primus.voice.conversation audio.wav -s demo
```

**Output:**
```
Session:    demo

── STT ──
Transcript: What is Newton's second law?
Language:   en

── Tutor ──
Response:   Newton's second law states that force equals mass times acceleration...
Language:   en
State:      speaking
Topic:      newtons_second_law

── TTS ──
Audio:      data/audio/tts_a1b2c3d4e5f6.mp3
Format:     mp3

── Robot ──
State:      speaking
Connected:  no (no ESP32 in CLI mode)

── Timing ──
STT:        1250 ms
LLM:        890 ms
TTS:        450 ms
Robot:      2 ms
Total:      2594 ms
```

Uses `.env` for API keys (same as the server). Works without hardware.

---

## 10. POST /voice Endpoint

### Request (multipart/form-data)

```bash
curl -X POST http://localhost:8000/voice \
  -F "audio=@question.wav" \
  -F "session_id=demo-session" \
  -F "language=en"
```

### Success response (200)

```json
{
  "status": "ok",
  "session_id": "demo-session",
  "transcript": "What is Newton's second law?",
  "transcript_language": "en",
  "tutor_response": {
    "response_text": "Newton's second law states...",
    "language": "en",
    "interaction_state": "speaking",
    "topic": "newtons_second_law"
  },
  "audio_path": "data/audio/tts_abc123.mp3",
  "audio_url": "/audio/tts_abc123.mp3",
  "audio_format": "mp3",
  "robot_state": "speaking",
  "timings": {
    "stt_ms": 1250,
    "tutor_ms": 890,
    "tts_ms": 450,
    "robot_ms": 2,
    "total_ms": 2594
  }
}
```

### Error responses

| Status | Condition |
|--------|-----------|
| `400` | Unsupported audio format |
| `503` `stt_error` | STT transcription failed |
| `503` `llm_provider_unavailable` | LLM provider offline |

`/chat` remains fully backward compatible and unaffected.

---

## 11. Mock Integration Test

The full pipeline is testable without internet or hardware using fake providers:

```python
FakeSTTProvider     → returns predetermined transcript
MockLLMProvider     → returns structured JSON response
FakeTTSProvider     → writes a tiny fake audio file
RobotController     → default offline state
```

This proves all software pieces are connected correctly:

```
audio.wav
  → FakeSTT: "What is Newton's second law?"
  → TutorEngine (MockLLM): "F equals m times a."
  → FakeTTS: data/audio/tts_xxx.mp3
  → RobotOrchestrator: "speaking" (offline, non-fatal)
```

---

## 12. Test Strategy

**226 tests** (19 new for M9), all using mocks.

| Test area | Count |
|-----------|-------|
| End-to-end pipeline | 5 |
| Multi-turn session | 1 |
| Failure isolation | 5 |
| Timing instrumentation | 2 |
| POST /voice endpoint | 3 |
| Full software integration | 3 |

Test highlights:
- Full pipeline: audio → STT → Tutor → TTS → Robot
- STT text correctly reaches TutorEngine
- TTS receives tutor response text (not transcript)
- Robot state mapped deterministically from interaction_state
- Multi-turn: same session_id preserves context
- STT failure stops pipeline (no tutor call)
- Tutor failure stops downstream (no TTS/robot)
- TTS failure → tutor response still valid, audio_path = None
- Robot offline → tutor + audio still succeed
- Robot send failure → tutor + audio still succeed
- Timing data recorded for all stages
- POST /voice returns full pipeline result
- /chat remains backward compatible

---

## 13. Limitations

| Limitation | Mitigation |
|------------|------------|
| No physical microphone | Software pipeline ready; CLI accepts audio files |
| No streaming STT/TTS | Future milestone |
| TutorEngine.chat() is synchronous | Acceptable for single-process architecture |
| No real latency benchmarks yet | Timing infrastructure in place |
| Robot dispatch always offline in CLI | RobotOrchestrator.dispatch() never raises |

---

## 14. Future Hardware Connection

When the INMP441 microphone and ESP32 are available:

1. ESP32 captures audio from INMP441
2. Audio is transmitted to the backend (WebSocket or HTTP)
3. The audio bytes are passed to `VoiceConversationService.process_audio()`
4. The complete pipeline executes — **no code changes to the orchestrator needed**

Only the audio transport layer (not in M9 scope) will be new code.

---

## File Changes (M9 only)

```
primus/voice/orchestrator.py      — VoiceConversationResult + VoiceConversationService
primus/voice/conversation.py      — CLI entry point (python -m primus.voice.conversation)
primus/api/routes.py              — POST /voice endpoint
primus/api/app.py                 — Wire VoiceConversationService
tests/test_voice_orchestrator.py  — 19 orchestration tests
```
