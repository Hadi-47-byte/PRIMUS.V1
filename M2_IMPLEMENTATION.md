# PRIMUS V1 — Milestone 2: HTTP API

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** FastAPI HTTP layer exposing the existing TutorEngine to external clients  
> **Depends on:** Milestone 1 (core tutor engine)

---

## Objective

Expose the existing `TutorEngine` through a minimal FastAPI HTTP API so that external clients (browser UI, phone interface, robot services) can communicate with PRIMUS over HTTP.

**No changes to the M1 core.** The API is a thin transport layer:

```
HTTP Client
  ↓
FastAPI (POST /chat, GET /health)
  ↓
TutorEngine.chat()        ← unchanged from M1
  ↓
SessionManager            ← unchanged from M1
  ↓
LLMProvider               ← unchanged from M1
  ↓
TutorResponse             ← unchanged from M1
```

---

## Architecture

### App Factory Pattern

`primus/api/app.py` exports a `create_app(provider=None)` factory:

- When `provider` is `None`, it builds one from `.env` via `primus.config`.
- When `provider` is supplied, it uses that directly (testing, custom providers).

This eliminates module-level side effects and makes the API fully testable without a real API key.

### Dependency Wiring

```
create_app(provider)
  ├─ SessionManager()
  ├─ TutorEngine(provider, session_manager)
  ├─ app.state.engine = engine
  ├─ CORSMiddleware (local dev origins)
  └─ include_router(router)
```

Routes access the engine via `request.app.state.engine` — no global singletons.

---

## Project Structure (M2 Additions)

```
PRIMUS.V1/
├── primus/
│   ├── api/                     ← NEW in M2
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI factory + CORS
│   │   ├── routes.py            # /health, /chat endpoints
│   │   └── schemas.py           # ChatRequest, ChatResponse, HealthResponse
│   ├── engine.py                # unchanged from M1
│   ├── models.py                # unchanged from M1
│   ├── provider.py              # unchanged from M1
│   ├── session.py               # unchanged from M1
│   └── ...
├── tests/
│   ├── test_api.py              ← NEW in M2 (13 tests)
│   ├── test_engine.py           # unchanged from M1
│   └── ...
├── pyproject.toml               # +fastapi, uvicorn
└── requirements.txt             # +fastapi, uvicorn, httpx
```

---

## Endpoints

### `GET /health`

Lightweight health check. No LLM call.

**Response 200:**
```json
{ "status": "ok" }
```

### `POST /chat`

Send a student message to the tutor engine.

**Request:**
```json
{
  "session_id": "demo-session",
  "message": "What is Newton's second law?"
}
```

| Field        | Type     | Required | Validation       |
|--------------|----------|----------|------------------|
| `session_id` | `string` | Yes      | Non-empty        |
| `message`    | `string` | Yes      | Non-empty        |

**Response 200:**
```json
{
  "session_id": "demo-session",
  "response_text": "Newton's second law states F = ma...",
  "language": "en",
  "interaction_state": "speaking",
  "topic": "newtons_second_law"
}
```

**Error responses:**

| Status | Condition                                  | Body                              |
|--------|--------------------------------------------|-----------------------------------|
| 400    | Empty/whitespace message or session_id     | `{"detail": "Message must not be empty."}` |
| 422    | Missing required fields (Pydantic)         | Standard FastAPI validation error |
| 500    | Unexpected internal error                  | `{"detail": "An internal error occurred..."}` |

**Note:** LLM provider errors are caught by `TutorEngine`'s safe-fallback and returned as a normal 200 response with `interaction_state: "idle"`.

---

## Session Behavior

- Sessions are **in-memory only** (unchanged from M1).
- The same `session_id` preserves conversation context across requests.
- Different `session_id` values create independent sessions.
- No persistence, no database, no long-term memory.

---

## CORS Configuration

Local development origins are whitelisted:

```
http://localhost:3000   (React / Next.js)
http://localhost:5173   (Vite)
http://localhost:8000   (FastAPI self)
http://localhost:8080   (generic dev server)
http://127.0.0.1:*      (same ports)
```

All methods and headers allowed. Credentials enabled.

---

## Testing

**13 new tests** in `tests/test_api.py`, using `MockLLMProvider` + FastAPI's `TestClient`.

| Test Class              | Count | What's Tested                                            |
|-------------------------|-------|----------------------------------------------------------|
| `TestHealth`            | 1     | Health endpoint returns `{"status": "ok"}`               |
| `TestChat`              | 3     | Successful chat, session context preserved, independent sessions |
| `TestChatValidation`    | 5     | Empty message (400), empty session_id (400), missing fields (422), empty body (422) |
| `TestChatProviderFailure` | 2   | Provider error → safe fallback (200), malformed LLM output → safe fallback (200) |
| `TestSecurity`          | 2     | No API key leakage in responses, CORS headers present    |

### Full Suite Results

```
63 passed in 6.90s
├── test_api.py      13 passed  ← M2
├── test_engine.py   15 passed  ← M1
├── test_models.py   12 passed  ← M1
├── test_provider.py 11 passed  ← M1
└── test_session.py  12 passed  ← M1
```

All M1 tests continue passing without modification.

---

## Running the API Server

### Prerequisites

```bash
# Copy the template and add your API key
cp .env.example .env
# Edit .env: set OPENAI_API_KEY
```

### Start the Server

```bash
# With hot reload (development)
uvicorn primus.api.app:create_app --factory --reload

# With custom host/port
uvicorn primus.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

### Interactive API Docs

Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Example: curl

```bash
# Health check
curl http://localhost:8000/health

# Chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo", "message": "What is gravity?"}'

# Follow-up (same session)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo", "message": "Give me an example."}'
```

### Example: Python httpx

```python
import httpx

base = "http://localhost:8000"

# Health
r = httpx.get(f"{base}/health")
print(r.json())  # {"status": "ok"}

# Chat
r = httpx.post(f"{base}/chat", json={
    "session_id": "student-1",
    "message": "Explain Newton's first law.",
})
print(r.json())
```

---

## New Dependencies (M2)

| Package    | Version    | Purpose                          |
|------------|------------|----------------------------------|
| `fastapi`  | `>=0.115.0`| HTTP framework                   |
| `uvicorn`  | `>=0.30.0` | ASGI server                      |
| `httpx`    | `>=0.27.0` | HTTP client (for FastAPI TestClient) |

---

## Design Decisions

| #  | Decision                                                   | Rationale                                                     |
|----|------------------------------------------------------------|---------------------------------------------------------------|
| 1  | App factory pattern (`create_app`)                         | Testable without real API key; no module-level side effects   |
| 2  | Engine via `app.state.engine`                              | Simple, testable, avoids FastAPI dependency injection overhead for M2 |
| 3  | `ChatResponse` as a separate API schema (not `TutorResponse`) | Adds `session_id` without modifying the core model           |
| 4  | Pydantic `min_length=1` on request fields                  | FastAPI auto-generates 422 validation errors                  |
| 5  | Additional whitespace-only validation in route handler     | Catches `"   "` which passes `min_length`                     |
| 6  | No module-level `app` instance                             | Prevents import-time failure when `.env` is missing           |
| 7  | CORS whitelist of localhost ports                          | Sufficient for local dev; production should restrict further  |
| 8  | `--factory` flag required for uvicorn                      | Cleaner than lazy singletons; standard FastAPI pattern        |

---

## Known Limitations (M2)

1. **No authentication** — the API is open. Auth should be added before any non-local deployment.
2. **No rate limiting** — a single client can flood the endpoint.
3. **No request logging middleware** — M1's logging module covers engine-level logs, but HTTP access logs are not yet structured.
4. **No async provider** — `LLMProvider.complete()` is synchronous. FastAPI runs it in a thread pool. This is fine for V1 but should be upgraded to async for production.
5. **CORS is hardcoded** — origins should be configurable via env vars for non-local deployments.
6. **No WebSocket support** — needed for real-time robot communication in later milestones.

---

## Next Milestone Starting Points

- **M3 (Tutoring Behavior):** Adaptive explanation depth, language detection, prompt quality improvements.
- **M8 (Web UI):** A minimal browser-based chat interface that calls this API.
- **M12 (ESP32 WebSocket):** WebSocket endpoint on this backend for robot command delivery.
