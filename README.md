# PRIMUS V1 — AI Physics Tutor

> An AI-powered physics tutor for grades 6–10, built for the Alibaba Cloud AI Hackathon 2026.

PRIMUS is a text-first, single-process Python backend that delivers adaptive physics tutoring through structured LLM responses. It supports English, Urdu, and Roman Urdu, with a browser-based test UI, a clean HTTP API for external clients, and a physical ESP32 robot that expresses emotional states and speaks its responses aloud.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your API key

```bash
cp .env.example .env
# Edit .env and set your LLM provider key:
#   OPENAI_API_KEY=sk-...
#   OPENAI_BASE_URL=https://api.openai.com/v1   (or Qwen, Ollama, etc.)
#   OPENAI_MODEL=gpt-4o-mini
```

### 3. Start the server

```bash
uvicorn primus.api.app:create_app --factory --reload
```

The server starts on `http://localhost:8000` by default.

### 4. Open the browser UI

Navigate to **http://localhost:8000** — a minimal chat interface for testing.

### 5. Interactive API docs

Navigate to **http://localhost:8000/docs** (Swagger UI) or **/redoc**.

---

## API Usage

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### `POST /chat`

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-session",
    "message": "What is Newton'\''s second law?"
  }'
```

**Response (200):**
```json
{
  "session_id": "demo-session",
  "response_text": "Newton's second law states that force equals mass times acceleration (F = ma)...",
  "language": "en",
  "interaction_state": "speaking",
  "topic": "newtons_second_law"
}
```

Use the **same `session_id`** across requests to maintain conversation context.

---

## Error Model

PRIMUS distinguishes three failure classes:

| Failure Type | HTTP Status | Behavior |
|---|---|---|
| **Malformed LLM output** | `200` | Engine safe-fallback: valid `TutorResponse` with `interaction_state: "idle"`, usable raw text preserved |
| **LLM provider unavailable** | `503` | `LLMProviderError` → safe JSON: `{"error": {"code": "llm_provider_unavailable", ...}}` |
| **Unexpected server error** | `500` | Safe JSON: `{"error": {"code": "internal_error", ...}}` |

No API keys, stack traces, or internal details are ever exposed in error responses.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

**249 tests** covering models, session, engine, provider, API, browser UI, robot communication (including audio delivery), expression mapping, curriculum retrieval, text-to-speech, speech-to-text, and voice conversation orchestration. All tests use mocks — no real LLM calls, ESP32 hardware, TTS/STT API calls required.

---

## Terminal REPL

```bash
python -m primus.main
```

Commands: `/new` (new session), `/state` (inspect session), `/quit` (exit).

---

## Project Structure

```
PRIMUS.V1/
├── primus/
│   ├── api/            # FastAPI HTTP API (M2+)
│   │   ├── app.py      #   App factory + CORS
│   │   ├── routes.py   #   /health, /chat, /robot/state, static UI
│   │   └── schemas.py  #   Request/response schemas
│   ├── robot/          # Robot communication (M4+M5)
│   │   ├── protocol.py #   Message schema + validation
│   │   ├── controller.py # RobotController (async)
│   │   ├── expressions.py # ExpressionMapper + Orchestrator (M5)
│   │   └── ws.py       #   WebSocket handler
│   ├── curriculum/     # Physics curriculum (M6)
│   │   ├── models.py   #   CurriculumConcept (Pydantic)
│   │   ├── retriever.py #   ABC + DeterministicCurriculumRetriever
│   │   ├── formatter.py #   Context formatter
│   │   └── data/physics.json # 15 Physics concepts
│   ├── voice/          # Voice: TTS (M7) + STT (M8) + Orchestrator (M9)
│   │   ├── models.py   #   TTSResult + TTSProviderError
│   │   ├── provider.py #   TTSProvider ABC + VoiceRegistry
│   │   ├── edge_tts_provider.py # EdgeTTSProvider
│   │   ├── service.py  #   TTSService + preprocessing
│   │   ├── __main__.py #   TTS CLI entry point
│   │   ├── orchestrator.py # VoiceConversationService (M9)
│   │   ├── conversation.py # Voice pipeline CLI (M9)
│   │   └── stt/        # Speech-to-Text (M8)
│   │       ├── models.py       # STTResult + STTProviderError
│   │       ├── provider.py     # STTProvider ABC
│   │       ├── gemini_provider.py  # GeminiSTTProvider (active)
│   │       ├── whisper_provider.py # WhisperSTTProvider (legacy)
│   │       └── service.py      # STTService + validation
│   ├── web/            # Browser test UI (M3)
│   │   ├── index.html
│   │   ├── app.js
│   │   └── styles.css
│   ├── stt_cli.py      # STT CLI entry point (M8)
│   ├── engine.py       # TutorEngine (core)
│   ├── models.py       # Pydantic models
│   ├── provider.py     # LLM provider abstraction
│   ├── session.py      # In-memory session manager
│   ├── prompts.py      # System prompt + builder
│   ├── config.py       # Settings from .env
│   └── main.py         # Terminal REPL
├── firmware/           # ESP32 Arduino firmware (M4)
│   ├── config.example.h  # Firmware config template (copy to config.h)
│   ├── HARDWARE_GUIDE.md # Verified pin map, wiring, test procedures
│   └── primus_esp32/     # Arduino sketch folder
│       └── primus_esp32.ino
├── tests/              # pytest suite (249 tests)
├── .env.example        # Environment template
├── pyproject.toml      # Project config
├── requirements.txt    # Dependencies
├── README.md
├── M1_IMPLEMENTATION.md
├── M2_IMPLEMENTATION.md
├── M3_IMPLEMENTATION.md
├── M4_IMPLEMENTATION.md
├── M5_IMPLEMENTATION.md
├── M6_IMPLEMENTATION.md
├── M7_IMPLEMENTATION.md
├── M8_IMPLEMENTATION.md
└── M9_IMPLEMENTATION.md
```

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | **Yes** | — | LLM provider API key |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Override for Qwen, Ollama, etc. |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model identifier |
| `GEMINI_API_KEY` | Voice only | — | Gemini speech-to-text key ([Google AI Studio](https://aistudio.google.com/apikey)) |
| `GEMINI_STT_MODEL` | No | `gemini-3.5-transcribe` | Gemini transcription model |

---

## Supported Providers

Any OpenAI-compatible chat-completions endpoint:

- **OpenAI** (default)
- **Qwen / DashScope** — set `OPENAI_BASE_URL` to DashScope endpoint
- **Ollama** (local) — `http://localhost:11434/v1`
- **vLLM, LiteLLM**, etc.

---

## Robot Communication

PRIMUS communicates with the ESP32 robot via WebSocket using semantic state commands.

### `POST /robot/state`

```bash
curl -X POST http://localhost:8000/robot/state \
  -H "Content-Type: application/json" \
  -d '{"state": "thinking"}'
# {"status": "sent", "state": "thinking"}
```

Allowed states: `idle`, `listening`, `thinking`, `speaking`, `happy`, `encouraging`, `excited`.

| Response | Condition |
|---|---|
| `200` `{"status": "sent", "state": "..."}` | State delivered to ESP32 |
| `400` `{"status": "invalid_state", "allowed_states": [...]}` | Unknown state |
| `503` `{"status": "robot_offline"}` | No ESP32 connected |

### WebSocket Endpoint

The ESP32 connects as a client to `ws://<backend-ip>:8000/ws/robot`.  Messages:

```json
{
  "version": 1,
  "type": "robot_state",
  "state": "thinking"
}
```

### Robot Audio Playback

Every `/voice` turn also delivers the tutor's spoken response to the robot:

1. The backend converts the TTS MP3 to headerless 16-bit mono PCM at 16 kHz.
2. A `robot_audio` message references the file; the ESP32 streams it over HTTP
   and plays it through the MAX98357A speaker (sample-aligned streaming, no
   full-file RAM buffering).

```json
{
  "version": 1,
  "type": "robot_audio",
  "audio_url": "/audio/tts_<id>.pcm",
  "sample_rate": 16000,
  "format": "pcm16"
}
```

`POST /robot/audio` (dev/test) replays an existing file from `data/audio/`
without spending LLM quota:

```bash
curl -X POST http://localhost:8000/robot/audio \
  -H "Content-Type: application/json" \
  -d '{"filename": "tts_<id>.pcm"}'
```

Audio playback never blocks emotional-state updates, and a malformed audio
message leaves the current state untouched.

### ESP32 Firmware

Copy `firmware/config.example.h` → `firmware/primus_esp32/config.h` (next to the sketch), set Wi-Fi credentials and backend IP, then flash `firmware/primus_esp32/primus_esp32.ino` via the Arduino IDE. See [firmware/HARDWARE_GUIDE.md](firmware/HARDWARE_GUIDE.md) for the verified pin map and [M4_IMPLEMENTATION.md](M4_IMPLEMENTATION.md) for full details.

### Expression Mapping (M5)

Every `/chat` response automatically drives the robot via the `ExpressionMapper`:

```
TutorResponse.interaction_state  →  ExpressionMapper  →  RobotController  →  WebSocket  →  ESP32
```

The mapping is deterministic code (not LLM-generated). Robot delivery failures are non-fatal — the tutor response is always returned even when the ESP32 is offline.

---

## Physics Curriculum (M6)

Every `/chat` request automatically retrieves relevant Physics curriculum context:

```
Student message → CurriculumRetriever (keyword match) → context → LLM → TutorResponse
```

The retriever is modular — swap `DeterministicCurriculumRetriever` for an embedding-based retriever later via the `CurriculumRetriever` ABC. The initial dataset contains 15 representative Grade 6–10 Physics concepts covering Mechanics, Energy, and Waves.

---

## Text-to-Speech (M7)

Generate audio from any tutor text using the CLI or HTTP endpoint:

```bash
# CLI
python -m primus.voice "What is gravity?" --language en
python -m primus.voice "Gravity ek force hai" --language ur

# HTTP
curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "What is Newton'\''s second law?", "language": "en"}'
```

Audio files are stored in `data/audio/` (gitignored). English and Urdu voices are supported. The TTS provider is replaceable via the `TTSProvider` ABC.

| Language | Voice |
|----------|-------|
| English (`en`) | `en-US-ChristopherNeural` |
| Urdu (`ur`) | `ur-PK-AsadNeural` |
| Roman Urdu | Uses Urdu voice |

See [M7_IMPLEMENTATION.md](M7_IMPLEMENTATION.md) for full details.

---

## Speech-to-Text (M8)

Transcribe audio files to text using the CLI or HTTP endpoint:

```bash
# CLI
python -m primus.stt_cli audio.wav --language en
python -m primus.stt_cli audio.mp3 -l ur
python -m primus.stt_cli audio.wav  # auto-detect

# HTTP
curl -X POST http://localhost:8000/stt \
  -F "audio=@audio.wav" \
  -F "language=en"
```

Supported formats: `.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac`, `.webm` (max 25 MB). The STT provider is replaceable via the `STTProvider` ABC. Uses Google Gemini STT via the native GenAI SDK with a dedicated `GEMINI_API_KEY` (independent of the LLM key — see `.env.example`).

| Language | Behavior |
|----------|----------|
| `en` | English transcription |
| `ur` | Urdu transcription |
| `auto` / omitted | Automatic language detection |

STT output feeds directly into TutorEngine:

```
audio → STT → text → TutorEngine → TutorResponse → TTS + ExpressionMapper
```

See [M8_IMPLEMENTATION.md](M8_IMPLEMENTATION.md) for full details.

---

## Voice Conversation Pipeline (M9)

Run the complete voice interaction pipeline from a local audio file:

```bash
# Full pipeline: audio → STT → Tutor → TTS + Robot
python -m primus.voice.conversation audio.wav --session demo --language en

# HTTP
curl -X POST http://localhost:8000/voice \
  -F "audio=@audio.wav" \
  -F "session_id=demo-session" \
  -F "language=en"
```

The pipeline coordinates all existing services:

```
audio → STT → transcript → TutorEngine → TutorResponse
                                    ├→ TTS → generated audio
                                    └→ ExpressionMapper → RobotController
```

Failure isolation: STT/tutor failures stop the pipeline; TTS/robot failures are non-fatal (tutor response is always preserved). Session continuity works across multiple voice turns.

See [M9_IMPLEMENTATION.md](M9_IMPLEMENTATION.md) for full details.

---

## Milestones

| Milestone | Scope | Status |
|---|---|---|
| M1 | Core tutor engine + terminal REPL | Complete |
| M2 | FastAPI HTTP API | Complete |
| M3 | Error handling + browser test UI | Complete |
| M4 | Robot WebSocket communication (ESP32) | Complete |
| M5 | Expression mapping (TutorResponse → Robot) | Complete |
| M6 | Physics curriculum layer (deterministic) | Complete |
| M7 | Text-to-Speech (TTS) pipeline | Complete |
| M8 | Speech-to-Text (STT) pipeline | Complete |
| M9 | Voice conversation orchestration | Complete |
| — | Robot audio playback (raw PCM → speaker) | Complete |
| M10+ | Quiz, animation | Planned |
