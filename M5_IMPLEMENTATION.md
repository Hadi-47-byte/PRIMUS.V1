# PRIMUS V1 — Milestone 5: Expression Mapping (TutorResponse → Robot State)

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** Deterministic mapping from TutorResponse.interaction_state to RobotController semantic states  
> **Depends on:** M1 (engine), M2 (API), M3 (error handling), M4 (WebSocket robot)

---

## Purpose

Connect the AI tutor's semantic output to the physical robot through a deterministic, testable mapping layer — without modifying the LLM, the TutorResponse schema, or the ESP32 firmware.

```
LLM
 ↓  interaction_state: "thinking"
TutorResponse
 ↓
ExpressionMapper (deterministic code)
 ↓  robot_state: "thinking"
RobotController.send_state()
 ↓  WebSocket JSON
ESP32
```

**Mandatory rule:** The LLM never produces hardware commands. It produces only `interaction_state` — the mapper converts that to a validated robot state.

---

## Mapping Table

| InteractionState | Robot State | Notes |
|-----------------|-------------|-------|
| `idle` | `idle` | Direct |
| `listening` | `listening` | Direct |
| `thinking` | `thinking` | Direct |
| `speaking` | `speaking` | Direct |
| `happy` | `happy` | Direct |
| `encouraging` | `encouraging` | Direct |
| `confused` | `thinking` | No robot equivalent — closest physical expression |
| `excited` | `excited` | Direct |

7 of 8 states map 1:1. Only `CONFUSED` requires a deliberate decision (→ `thinking`).

---

## Architecture

### New File

```
primus/robot/expressions.py
```

Contains:

1. **`map_interaction_to_robot_state(interaction_state) -> str`**  
   Pure function. No I/O. Accepts `InteractionState` enum or string. Returns a validated robot state string. Raises `ValueError` for unknown inputs.

2. **`RobotOrchestrator`**  
   Bridges `TutorResponse` → mapper → `RobotController`. All delivery failures are caught and logged — the tutor response is never disrupted.

### Modified Files

| File | Change |
|------|--------|
| `primus/api/routes.py` | Added `orchestrator.dispatch(tutor_response)` after successful `/chat` |
| `primus/api/app.py` | Created `RobotOrchestrator` instance, stored on `app.state` |

### Separation of Concerns

```
ExpressionMapper (pure function)
  - decides WHICH robot state to use
  - no network, no side effects
  - unit-testable in isolation

RobotOrchestrator (fault-tolerant bridge)
  - extracts interaction_state from TutorResponse
  - calls ExpressionMapper
  - calls RobotController.send_state()
  - swallows all failures

RobotController (delivery layer — M4)
  - validates state against protocol
  - sends over WebSocket
  - manages connection lifecycle
```

---

## Robot-Offline Behaviour

```
AI success + robot online
  → TutorResponse returned to client
  → robot state sent via WebSocket

AI success + robot offline
  → TutorResponse returned to client  (unchanged)
  → orchestrator.dispatch() returns False
  → log: "Robot offline; skipping physical expression."

AI success + robot send failure
  → TutorResponse returned to client  (unchanged)
  → orchestrator.dispatch() catches exception, returns False
  → log: "Robot dispatch failed: ..."
```

**Critical guarantee:** An offline or broken robot NEVER turns a successful tutor response into an error. The `/chat` endpoint returns the same `ChatResponse` regardless of robot connectivity.

---

## Integration Point

The orchestration is wired in the `/chat` route, immediately after the engine returns a successful `TutorResponse`:

```python
# routes.py — POST /chat
tutor_response = engine.chat(message=..., session_id=...)
orchestrator = request.app.state.orchestrator
await orchestrator.dispatch(tutor_response)   # fire-and-forget, non-fatal
return ChatResponse(...)                       # always succeeds
```

The `dispatch()` call is a single `await` line — minimal surface area, fully isolated from the response construction.

---

## API Compatibility

The `/chat` response schema is **unchanged**:

```json
{
  "session_id": "demo",
  "response_text": "...",
  "language": "en",
  "interaction_state": "speaking",
  "topic": "newtons_laws"
}
```

No hardware-specific fields are added:
- No `servo_angle`
- No `gpio_pin`
- No `led_color`
- No `robot_state`

The `/robot/state` endpoint continues to behave exactly as defined in M4.

---

## Testing

### Test Results

```
118 passed in 9.36s
├── test_api.py         19 passed  (M2 + M3)
├── test_engine.py      15 passed  (M1 + M3)
├── test_expressions.py 22 passed  ← M5
│   ├── TestExpressionMapper      12 tests
│   ├── TestRobotOrchestrator      6 tests
│   └── TestChatRobotIntegration   4 tests
├── test_models.py      12 passed  (M1)
├── test_provider.py    11 passed  (M1)
├── test_robot.py       27 passed  (M4)
└── test_session.py     12 passed  (M1)
```

### Test Coverage

| Test Class | Count | What's Verified |
|-----------|-------|----------------|
| `TestExpressionMapper` | 12 | All 7 direct mappings, CONFUSED→thinking, string input, unknown rejection, empty string, all results are valid protocol states |
| `TestRobotOrchestrator` | 6 | Online dispatch, offline dispatch, send failure, CONFUSED mapping, all InteractionStates, never raises |
| `TestChatRobotIntegration` | 4 | /chat works offline, /chat dispatches online, response schema unchanged, provider error still 503 |

---

## Logging

| Event | Level | Message |
|-------|-------|---------|
| Successful mapping | `DEBUG` | `"Mapped interaction state 'thinking' → robot state 'thinking'."` |
| Robot offline | `INFO` | `"Robot offline; skipping physical expression."` |
| Dispatch failure | `WARNING` | `"Robot dispatch failed: ..."` |

No API keys, credentials, or sensitive request data are logged.

---

## Assumptions

| # | Assumption | Rationale |
|---|-----------|-----------|
| 1 | `InteractionState` and robot states are semantically aligned | Both were designed from the same state vocabulary |
| 2 | `CONFUSED → thinking` is acceptable | The ESP32 shows a "thinking" face which is reasonable for confusion |
| 3 | Robot dispatch is fire-and-forget | The tutor response must not depend on hardware connectivity |
| 4 | Single `await` in the route is sufficient | Robot state delivery is simple enough for a single async call |
| 5 | `dispatch()` catches ALL exceptions | Belt-and-suspenders — even unexpected errors must not break /chat |

---

## Limitations

1. **No animation sequencing** — the robot enters a static state. Transitions (e.g., idle→thinking→speaking) are instant. Animation smoothing is a future milestone.
2. **No state duration** — the robot holds the state until the next `/chat` call changes it. A "listening" state persists until the student sends another message.
3. **No concurrent dispatch** — only one state is sent per `/chat` call. Multi-state sequences (e.g., thinking→speaking) are not yet supported.
4. **No state debouncing** — rapid successive `/chat` calls will send rapid successive states. Rate limiting could be added later if needed.

---

## Files Added/Modified

### New Files (2)

| File | Purpose |
|------|---------|
| `primus/robot/expressions.py` | ExpressionMapper + RobotOrchestrator |
| `tests/test_expressions.py` | 22 automated tests |

### Modified Files (2)

| File | Change |
|------|--------|
| `primus/api/routes.py` | +4 lines: orchestrator dispatch in `/chat` |
| `primus/api/app.py` | +3 lines: create orchestrator, store on app.state |

---

## Next Milestones

- **M6:** Animation sequencing (smooth transitions between robot states).
- **M7:** State duration and timing (hold state for minimum/maximum durations).
- **M8:** Production UI with live robot status indicator.
- **M12:** STT/TTS integration with expression timing.
