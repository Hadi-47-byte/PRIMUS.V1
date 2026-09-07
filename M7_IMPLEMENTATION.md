# PRIMUS V1 — Milestone 7: Text-to-Speech (TTS)

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** Convert TutorResponse text to playable audio via provider-agnostic TTS  
> **Depends on:** M1–M6

---

## Purpose

Convert the tutor's text responses into playable audio files — supporting English and Urdu — while keeping the TTS layer fully independent from the robot hardware and the core tutoring pipeline.

```
TutorResponse.response_text
        ↓
      TTSService
        ↓
    TTSProvider (EdgeTTS)
        ↓
    MP3 audio file
        ↓
  data/audio/tts_*.mp3
```

Later, the same audio pipeline will be connected to the ESP32 speaker.

---

## Architecture

### New Files

```
primus/voice/
├── __init__.py            # Package marker
├── models.py              # TTSResult, TTSProviderError
├── provider.py            # TTSProvider ABC + VoiceRegistry
├── edge_tts_provider.py   # EdgeTTSProvider (edge-tts library)
├── service.py             # TTSService (orchestration) + preprocess_text()
└── __main__.py            # CLI entry point
```

### Modified Files

| File | Change |
|------|--------|
| `primus/api/routes.py` | +POST /tts endpoint, +GET /audio/{filename} |
| `primus/api/app.py` | Wire TTSService + EdgeTTSProvider |
| `.gitignore` | +data/audio/*.mp3, *.wav |
| `requirements.txt` | +edge-tts>=6.1.0 |
| `pyproject.toml` | +edge-tts dependency |

---

## TTSProvider Abstraction

```python
class TTSProvider(ABC):
    async def synthesize(self, text: str, voice: str, output_path: str) -> TTSResult:
        ...
```

The ABC pattern matches the existing `LLMProvider` design. Concrete providers are replaceable without changing `TTSService` or `TutorEngine`.

### Concrete Provider: `EdgeTTSProvider`

Uses the `edge-tts` library — free, no API key required, supports many languages.

| Feature | Value |
|---------|-------|
| Library | `edge-tts` |
| API key | Not required |
| Audio format | MP3 |
| English voice | `en-US-ChristopherNeural` |
| Urdu voice | `ur-PK-AsadNeural` |
| Async | Yes (native) |

---

## Language → Voice Mapping

| Language Code | Voice | Notes |
|---------------|-------|-------|
| `en` | `en-US-ChristopherNeural` | Default English |
| `ur` | `ur-PK-AsadNeural` | Pakistani Urdu |
| `roman_urdu` | `ur-PK-AsadNeural` | Same Urdu voice for Roman Urdu |
| Unknown | Falls back to English voice | Synthesis never fails due to language |

Voices are configurable via:
1. `VoiceRegistry(overrides={...})` constructor
2. Environment variables: `PRIMUS_TTS_VOICE_EN`, `PRIMUS_TTS_VOICE_UR`

---

## TTSResult Model

```python
class TTSResult(BaseModel):
    audio_path: str          # "data/audio/tts_a1b2c3d4e5f6.mp3"
    format: str              # "mp3"
    duration_ms: int | None  # synthesis time in ms
    language: str            # "en"
    voice: str               # "en-US-ChristopherNeural"
```

---

## TTSProviderError

Typed exception for all TTS failures:
- Provider unavailable
- Network failure
- Invalid credentials (if applicable)
- Empty/invalid text
- File generation failure
- Malformed provider response

```python
class TTSProviderError(Exception):
    """Raised when a TTS provider cannot complete synthesis."""
```

---

## Text Preprocessing

`preprocess_text()` applies minimal deterministic cleaning before synthesis:

| Transformation | Example |
|---------------|---------|
| Strip whitespace | `"  hello  "` → `"hello"` |
| Remove markdown bold | `**text**` → `text` |
| Remove markdown italic | `*text*` → `text` |
| Remove backticks | `` `F = ma` `` → `F = ma` |
| Remove headings | `## Title` → `Title` |
| Collapse whitespace | `"a    b"` → `"a b"` |

**Preserved:** Scientific notation (`F = ma`), formulas, numbers, equations.

**Not done:** Semantic rewriting, formula expansion, language translation.

---

## File Output

- Output directory: `data/audio/` (created automatically)
- Filename pattern: `tts_{uuid_hex_12}.mp3`
- Files are never overwritten (UUID-based unique names)
- `.gitignore` excludes `data/audio/*.mp3` and `*.wav`

---

## HTTP Endpoints

### `POST /tts`

```bash
curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "What is Newton'\''s second law?", "language": "en"}'
```

**Response (200):**
```json
{
  "status": "ok",
  "audio_path": "data/audio/tts_a1b2c3d4e5f6.mp3",
  "audio_url": "/audio/tts_a1b2c3d4e5f6.mp3",
  "language": "en",
  "voice": "en-US-ChristopherNeural",
  "format": "mp3",
  "duration_ms": 1200
}
```

| Status | Condition |
|--------|-----------|
| `200` | Audio generated successfully |
| `400` | Empty text / invalid input |
| `503` | TTS provider failure |

### `GET /audio/{filename}`

Streams the generated MP3 file. Path traversal is rejected.

---

## CLI Testing

```bash
python -m primus.voice "What is gravity?" --language en
python -m primus.voice "Gravity ek force hai" --language ur
python -m primus.voice "F = ma" --language en --voice en-US-JennyNeural
```

Output:
```
Audio file: data/audio/tts_abc123.mp3
Voice:      en-US-ChristopherNeural
Language:   en
Format:     mp3
Synthesis:  1234 ms
```

---

## Tutor Integration

TTS is **not** a hard dependency of `/chat`. The architecture remains:

```
TutorResponse
    ├── ExpressionMapper → RobotController → WebSocket → ESP32
    │
    └── TTSService → TTSProvider → audio file
```

TTS is an independent service that can be called separately:

| Scenario | Behaviour |
|----------|-----------|
| Tutor succeeds + TTS succeeds | Tutor response + generated audio |
| Tutor succeeds + TTS unavailable | Tutor response still succeeds, TTS failure logged |
| Tutor succeeds + TTS not called | Tutor response returned normally |

---

## Error Handling

| Failure | Handling |
|---------|----------|
| Empty text | `TTSProviderError` before any I/O |
| Provider unavailable | `TTSProviderError` → HTTP 503 |
| Network failure | `TTSProviderError` → HTTP 503 |
| File write failure | `TTSProviderError` → HTTP 503 |
| Unknown language | Falls back to English voice (no error) |

TTS failure NEVER breaks the `/chat` endpoint. The core tutor continues functioning regardless of TTS availability.

---

## Testing

### Test Results

```
176 passed in 6.45s
├── test_api.py         19 passed  (M2 + M3)
├── test_curriculum.py  26 passed  (M6)
├── test_engine.py      15 passed  (M1 + M3)
├── test_expressions.py 22 passed  (M5)
├── test_models.py      12 passed  (M1)
├── test_provider.py    11 passed  (M1)
├── test_robot.py       27 passed  (M4)
├── test_session.py     12 passed  (M1)
└── test_tts.py         32 passed  ← M7
    ├── TestTTSProviderABC    3
    ├── TestVoiceRegistry     6
    ├── TestPreprocessText    7
    ├── TestTTSService        8
    ├── TestTTSEndpoint       7
    └── TestTTSIsolation      1
```

---

## Assumptions

| # | Assumption | Rationale |
|---|-----------|-----------|
| 1 | `edge-tts` is the initial provider | Free, no API key, supports EN+UR, async |
| 2 | MP3 is the output format | Universal playback compatibility |
| 3 | `data/audio/` is the output directory | Separate from source code |
| 4 | UUID-based filenames prevent collisions | Multiple concurrent syntheses are safe |
| 5 | Voice defaults are acceptable | ChristopherNeural (EN) and AsadNeural (UR) are clear voices |
| 6 | TTS is independent from `/chat` | Not coupled to the tutor response contract |

---

## Known Limitations

1. **No streaming audio** — full synthesis before playback. Sentence-by-sentence TTS is deferred.
2. **No hardware integration** — audio files are generated locally; ESP32 speaker connection is a future milestone.
3. **No STT** — speech-to-text is not implemented in M7.
4. **Edge TTS requires internet** — the edge-tts library connects to Microsoft Edge's online service.
5. **No audio duration from file** — `duration_ms` measures synthesis time, not audio playback duration.
6. **No concurrent TTS batching** — one synthesis call per request.
7. **Roman Urdu uses Urdu voice** — pronunciation may not be perfect for Roman Urdu text.

---

## Files Added/Modified

### New Files (7)

| File | Purpose |
|------|---------|
| `primus/voice/__init__.py` | Package marker |
| `primus/voice/models.py` | `TTSResult` + `TTSProviderError` |
| `primus/voice/provider.py` | `TTSProvider` ABC + `VoiceRegistry` |
| `primus/voice/edge_tts_provider.py` | `EdgeTTSProvider` (edge-tts) |
| `primus/voice/service.py` | `TTSService` + `preprocess_text()` |
| `primus/voice/__main__.py` | CLI entry point |
| `tests/test_tts.py` | 32 automated tests |

### Modified Files (5)

| File | Change |
|------|--------|
| `primus/api/routes.py` | +POST /tts, +GET /audio/{filename} |
| `primus/api/app.py` | Wire TTSService + EdgeTTSProvider |
| `.gitignore` | +data/audio/*.mp3, *.wav |
| `requirements.txt` | +edge-tts>=6.1.0 |
| `pyproject.toml` | +edge-tts dependency |

---

## Next Milestones

- **M8:** Connect TTS audio to the ESP32 speaker (hardware audio integration).
- **M9:** STT (Speech-to-Text) for student voice input.
- **M10:** Streaming / sentence-by-sentence TTS for lower latency.
- **M11:** Production UI with audio playback controls.
