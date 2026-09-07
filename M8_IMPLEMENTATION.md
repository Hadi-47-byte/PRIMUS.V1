# Milestone 8 — Speech-to-Text (STT)

## Status: COMPLETE

---

## 1. Architecture

```
Audio file (.wav/.mp3/.m4a/...)
        ↓
   STTService  (validation + orchestration)
        ↓
   STTProvider  (ABC — replaceable)
        ↓
   WhisperSTTProvider  (OpenAI Whisper API)
        ↓
   STTResult
        ↓
   transcribed text
        ↓
   TutorEngine.chat(text, session_id)
        ↓
   TutorResponse
```

### Future physical pipeline

```
INMP441 microphone
   ↓
ESP32 audio capture
   ↓
audio input
   ↓
STTService
   ↓
TutorEngine
```

**M8 does NOT implement** physical microphone capture, ESP32 audio firmware, WebSocket audio streaming, or streaming STT.

---

## 2. Provider Abstraction

```python
# primus/voice/stt/provider.py
class STTProvider(ABC):
    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> STTResult: ...
```

The rest of PRIMUS depends on this ABC, never on a concrete provider.

---

## 3. Concrete Provider — OpenAI Whisper

**Chosen provider**: `WhisperSTTProvider` using the `openai` SDK's `audio.transcriptions.create()`.

### Why Whisper

| Criterion | Whisper |
|-----------|---------|
| Already installed | `openai` SDK present for LLM provider |
| No new dependencies | Zero additional packages |
| Multilingual | English, Urdu, and 90+ languages |
| Provider-agnostic | Any OpenAI-compatible endpoint can serve Whisper |
| Replaceable | Swap to any `STTProvider` subclass |
| Current SDK | Uses latest OpenAI Python SDK |

### Language handling

| Input | Behavior |
|-------|----------|
| `"en"` | English transcription hint sent to Whisper |
| `"ur"` | Urdu transcription hint sent to Whisper |
| `"auto"` / `None` | Omitted — Whisper auto-detects |
| Roman Urdu | No dedicated Whisper code; best handled via `auto` or `ur` |

Whisper accepts ISO-639-1 codes. The `"auto"` keyword is not a valid code, so `STTService` converts it to `None` before calling the provider.

---

## 4. STTResult Model

```python
class STTResult(BaseModel):
    text: str                    # Transcribed text (critical field)
    language: str | None         # Detected or hinted language
    duration_ms: int | None      # Audio duration (if reported)
    confidence: float | None     # Confidence score (if reported)
    provider: str | None         # Provider identifier
```

Confidence is only populated when the provider supplies it — never fabricated.

---

## 5. Audio Input Validation

Validation occurs in `STTService.transcribe()` **before** calling the provider:

| Check | Behavior |
|-------|----------|
| File exists | `STTProviderError` if missing |
| Is a file | Rejects directories |
| Supported extension | `.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac`, `.webm` |
| Non-empty | Rejects 0-byte files |
| Size limit | Max 25 MB (`MAX_FILE_SIZE`) |
| Case-insensitive | `.WAV` and `.wav` both accepted |

### HTTP endpoint security

Uploaded files are saved to temporary files via `tempfile.mkstemp()` and cleaned up in a `finally` block. File extension is validated against `SUPPORTED_EXTENSIONS` before any processing.

---

## 6. STTService

```python
class STTService:
    def __init__(self, provider: STTProvider): ...
    async def transcribe(self, audio_path, language=None) -> STTResult: ...
```

Responsibilities:
1. Validate audio input
2. Normalise language parameter (`"auto"` → `None`, strip whitespace, lowercase)
3. Delegate to provider
4. Post-validate: reject empty transcription text
5. Return `STTResult` or raise `STTProviderError`

---

## 7. Error Handling

All STT errors use `STTProviderError`:

- Provider unavailable → caught and wrapped
- Network failure → caught and wrapped
- Invalid API credentials → caught, no key exposed
- Unsupported format → deterministic validation
- Empty/invalid audio → deterministic validation
- Provider failure → caught and wrapped
- Empty transcription → post-validation

**Never exposed**: API keys, credentials, raw auth errors, stack traces.

**STT failure does NOT corrupt** tutor/session state. `/chat` remains fully operational when STT is broken.

---

## 8. CLI

```bash
# Transcribe an English audio file
python -m primus.stt_cli audio.wav --language en

# Auto-detect language
python -m primus.stt_cli audio.wav

# Transcribe Urdu
python -m primus.stt_cli audio.mp3 -l ur
```

Output:
```
Transcript: What is Newton's second law?
Language:   en
Duration:   2340 ms
Provider:   whisper
Latency:    1250 ms
```

---

## 9. HTTP Endpoint

### POST /stt

**Request**: `multipart/form-data`

```bash
curl -X POST http://localhost:8000/stt \
  -F "audio=@test.wav" \
  -F "language=en"
```

**Success response** (200):
```json
{
  "status": "ok",
  "text": "What is acceleration?",
  "language": "en",
  "confidence": null,
  "duration_ms": 2340,
  "provider": "whisper"
}
```

**Invalid format** (400):
```json
{"status": "invalid_format", "detail": "Unsupported format '.txt'. ..."}
```

**STT failure** (503):
```json
{"status": "stt_error", "detail": "Transcription failed."}
```

---

## 10. Tutor Integration

The integration point is clean and separate:

```python
result = await stt_service.transcribe(audio_path, language="en")
response = engine.chat(result.text, session_id="voice-session")
```

- `/chat` remains text-based and does NOT automatically invoke STT.
- STT output feeds the existing `TutorEngine.chat()` contract unchanged.
- Future voice pipeline: `audio → STT → text → TutorEngine → TutorResponse → TTS + ExpressionMapper`.

---

## 11. TTS Compatibility

STT output flows naturally into the existing stack:

```
audio → STT → text → TutorEngine → TutorResponse
                                      ├── TTS (voice output)
                                      └── ExpressionMapper (robot state)
```

STT is **not coupled** to TTS. Each is independently usable.

---

## 12. Test Strategy

**207 tests** (31 new for M8), all using mocks — no real STT API calls.

| Test area | Count |
|-----------|-------|
| STTProvider ABC | 3 |
| STTResult model | 3 |
| Audio validation | 9 |
| STTService | 9 |
| POST /stt endpoint | 4 |
| STT independence | 3 |

Test highlights:
- ABC cannot be instantiated directly
- Successful English and Urdu transcription
- `language="auto"` normalised to `None` for provider
- Missing/empty/unsupported files rejected before provider call
- Provider failures propagate as `STTProviderError`
- Empty transcription text rejected post-provider
- STT works without any robot controller
- STT result feeds directly into TutorEngine
- `/chat` remains operational when STT is broken
- All M1–M7 tests continue passing

---

## 13. Benchmark Samples

Located in `data/benchmark_samples/`:

| ID | Language | Reference | Category |
|----|----------|-----------|----------|
| en_01 | en | What is Newton's second law? | English |
| en_02 | en | Explain acceleration with a simple example. | Pakistani-accented English |
| ur_01 | ur | نیوٹن کا دوسرا قانون کیا ہے؟ | Urdu script |
| roman_ur_01 | auto | Newton ka doosra qanoon kya hai? | Roman Urdu |
| mixed_01 | auto | Newton's second law ko simple Urdu mein explain karo. | Mixed |
| mixed_02 | auto | Force aur acceleration ka kya relation hai? | Roman Urdu + English |

Audio files (`.wav`) are gitignored. Record manually and compare using `benchmark_results.json`.

---

## 14. Latency Observations

The CLI reports transcription latency. For a 2–3 second English audio clip:

- Whisper API round-trip: typically 1–3 seconds
- `verbose_json` response includes audio duration and language
- Non-blocking: uses `async` with deferred `openai` import

---

## 15. Limitations

| Limitation | Mitigation |
|------------|------------|
| No physical microphone in M8 | Software pipeline ready for INMP441 integration |
| No streaming STT | Future milestone |
| Roman Urdu has no Whisper language code | Use `auto` or `ur`; document real behavior |
| OpenAI API key required | Uses same key as LLM provider; mock in tests |
| Confidence not reported by Whisper API | `STTResult.confidence` stays `None` |

---

## 16. Future INMP441/ESP32 Integration

The architecture is designed so that when the physical microphone is available:

1. ESP32 captures audio from INMP441
2. Audio is transmitted to the backend (WebSocket or HTTP)
3. The audio bytes are written to a temp file
4. `STTService.transcribe(temp_path)` is called — **no code changes needed**

The only new code required will be the audio transport layer (not in M8 scope).

---

## File Changes (M8 only)

```
primus/voice/stt/__init__.py       — Package marker
primus/voice/stt/models.py         — STTResult, STTProviderError
primus/voice/stt/provider.py       — STTProvider ABC
primus/voice/stt/whisper_provider.py — WhisperSTTProvider
primus/voice/stt/service.py        — STTService + validate_audio()
primus/stt_cli.py                  — CLI entry point
primus/api/routes.py               — POST /stt endpoint
primus/api/app.py                  — Wire STTService + WhisperSTTProvider
data/benchmark_samples/            — Sample definitions + results template
tests/test_stt.py                  — 31 STT tests
```
