# PRIMUS V1 — Milestone 3: Error Handling & Browser UI

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** Corrected error-handling boundary + minimal browser test UI  
> **Depends on:** M1 (core engine), M2 (HTTP API)

---

## Objective

1. **Correct the error-handling chain** — distinguish malformed LLM output (HTTP 200) from provider failures (HTTP 503) and unexpected errors (HTTP 500).
2. **Add a minimal browser test UI** — plain HTML/CSS/JS interface served from FastAPI.
3. **Keep the existing architecture simple** — no core M1/M2 refactoring.

---

## Part 1 — Error Handling Chain

### Problem (M2 behavior)

M2 treated all provider failures as HTTP 200 with a safe `TutorResponse`. This was incorrect: a network/auth/timeout failure should not look like a successful tutor response.

### Solution: Three Failure Classes

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  Malformed LLM output                               │
│    → TutorEngine._safe_fallback()                   │
│    → Valid TutorResponse (interaction_state: idle)  │
│    → HTTP 200                                       │
│                                                     │
│  LLM provider/API failure                           │
│    → LLMProviderError raised by provider            │
│    → TutorEngine re-raises (does NOT catch)         │
│    → FastAPI catches → HTTP 503                     │
│                                                     │
│  Unexpected server error                            │
│    → Any non-LLMProviderError exception             │
│    → FastAPI catches → HTTP 500                     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Implementation

#### `LLMProviderError` (primus/provider.py)

```python
class LLMProviderError(Exception):
    """Raised when the LLM provider cannot complete a request."""
```

- Defined in the provider module alongside `LLMProvider` ABC.
- Wraps connection errors, timeouts, auth failures, rate limits.

#### `OpenAICompatibleProvider.complete()`

- Attempt 1: call with `response_format=json_object`.
  - If format-related error → retry without `response_format`.
  - If genuine API error (auth, network, timeout) → raise `LLMProviderError`.
- Attempt 2: call without `response_format`.
  - Any exception → raise `LLMProviderError`.
- `_is_retryable_format_error()` heuristic distinguishes format errors from real failures.

#### `TutorEngine.chat()`

```python
try:
    raw_response = self._provider.complete(messages)
except LLMProviderError:
    raise                          # propagate to API layer
except Exception:
    return self._safe_fallback()   # non-provider error → safe response
```

#### `routes.py /chat`

```python
try:
    tutor_response = engine.chat(...)
except LLMProviderError:
    return JSONResponse(status_code=503, content={
        "error": {
            "code": "llm_provider_unavailable",
            "message": "PRIMUS AI service is temporarily unavailable.",
        }
    })
except Exception:
    return JSONResponse(status_code=500, content={
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred. Please try again.",
        }
    })
```

### Security Guarantee

No error response ever exposes:
- API keys or credentials
- Stack traces
- Internal exception messages
- Provider-specific error details

---

## Part 2 — Browser Test UI

### Architecture

```
primus/web/
├── index.html    # Chat UI shell
├── app.js        # Frontend logic (fetch /chat, render messages)
└── styles.css    # Dark theme, responsive layout
```

Served from FastAPI via three `FileResponse` routes:

| Route           | File                | Notes                       |
|-----------------|---------------------|-----------------------------|
| `GET /`         | `web/index.html`    | Main chat UI                |
| `GET /app.js`   | `web/app.js`        | Frontend logic              |
| `GET /styles.css`| `web/styles.css`   | Styles                      |

All three routes use `include_in_schema=False` to keep the Swagger docs clean.

### Frontend Features

- **Session management:** auto-generates a `session_id` on page load, persists across messages.
- **Enter to send**, **Shift+Enter for newline.**
- **Send button disabled** while waiting for a response.
- **Error display:** shows user-friendly messages for 503/500/400 errors.
- **Metadata display:** shows interaction state, topic, and language for each response.
- **Zero dependencies:** plain HTML/CSS/JS, no frameworks.

### Design

- Dark theme with CSS custom properties.
- Mobile-friendly flex layout.
- Chat bubbles: user (right, primary color), assistant (left, surface color).
- Monospace session ID in footer with link to `/docs`.

---

## Files Modified

| File | Change |
|------|--------|
| `primus/provider.py` | Added `LLMProviderError`; wrapped API calls in try/except; added `_is_retryable_format_error()` |
| `primus/engine.py` | Split exception handling: `LLMProviderError` propagates, other exceptions use safe fallback |
| `primus/api/routes.py` | Added `LLMProviderError` → 503 handler; generic Exception → 500 handler; added static file routes |
| `primus/api/schemas.py` | Added `ErrorDetail` and `APIErrorResponse` schemas |
| `tests/test_engine.py` | Updated `test_provider_error_propagates` to expect `LLMProviderError` |
| `tests/test_api.py` | Updated provider error test to 503; added 500 test, static UI tests, sensitive-data test |

## Files Created

| File | Purpose |
|------|---------|
| `primus/web/index.html` | Browser test UI markup |
| `primus/web/app.js` | Frontend chat logic |
| `primus/web/styles.css` | Dark-theme styles |
| `README.md` | Project-level documentation |
| `M3_IMPLEMENTATION.md` | This file |

---

## Test Results

```
69 passed in 8.98s
├── test_api.py      19 passed  (M2: 13 + M3: 6 new)
├── test_engine.py   15 passed  (M1: 14 + M3: 1 updated)
├── test_models.py   12 passed  (M1, unchanged)
├── test_provider.py 11 passed  (M1, unchanged)
└── test_session.py  12 passed  (M1, unchanged)
```

### New/Updated Tests

| Test | Status Code | What's Verified |
|------|------------|-----------------|
| `test_provider_error_returns_503` | 503 | `LLMProviderError` → correct HTTP status + error body |
| `test_malformed_llm_output_returns_200_fallback` | 200 | Non-JSON output → safe fallback with usable text |
| `test_unexpected_error_returns_500` | 500 | Error escaping engine → 500 + no stack trace leak |
| `test_non_provider_error_engine_fallback_200` | 200 | Non-provider error caught by engine → safe fallback |
| `test_no_sensitive_details_in_503` | 503 | API keys / internal messages never exposed |
| `test_index_html_served` | 200 | `GET /` serves the chat UI |
| `test_js_served` | 200 | `GET /app.js` serves frontend logic |
| `test_css_served` | 200 | `GET /styles.css` serves styles |
| `test_provider_error_propagates` | — | Engine re-raises `LLMProviderError` (updated from M1) |

---

## Assumptions

| # | Assumption | Rationale |
|---|-----------|-----------|
| 1 | `LLMProviderError` covers all provider-level failures | Single typed exception simplifies the error chain |
| 2 | `_is_retryable_format_error()` heuristic is sufficient | Format-related errors contain "format", "json", or "unsupported" |
| 3 | Static files are small enough for `FileResponse` | HTML/JS/CSS are all under 10KB |
| 4 | Browser UI is development-only | No auth, no persistence, no production hardening |
| 5 | Engine's generic `except Exception` still catches non-provider errors | Preserves M1 safe-fallback for non-infrastructure failures |

---

## Known Limitations

1. **No streaming** — responses are fully buffered before display.
2. **No reconnection/retry** in the browser UI on 503.
3. **Static files served individually** — not using `StaticFiles` mount (3 routes instead of 1 mount).
4. **No request logging middleware** — errors are logged but not structured for monitoring.
5. **Browser UI has no offline state** — page reload loses conversation history.
6. **CORS origins still hardcoded** — should be configurable for non-local deployments.

---

## Next Milestone Starting Points

- **M4 (Curriculum):** Structured physics content lookup, prompt grounding with curriculum context.
- **M5 (Benchmarking):** Compare curriculum-grounded vs. plain LLM responses.
- **M8 (Production UI):** React/Vue frontend with persistent sessions and richer UX.
- **M12 (ESP32 WebSocket):** Real-time robot command delivery over WebSocket.
