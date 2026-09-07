# PRIMUS V1 — Milestone 4: Robot Communication (WebSocket)

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** Backend ↔ ESP32 WebSocket communication layer with semantic robot states  
> **Depends on:** M1 (core engine), M2 (HTTP API), M3 (error handling)

---

## Objective

Establish a reliable, minimal communication pipeline between the PRIMUS backend and the ESP32 hardware — **independent of the LLM**.

```
POST /robot/state { "state": "thinking" }
        ↓
   RobotController (validates state)
        ↓
   WebSocket /ws/robot
        ↓
   ESP32 firmware
        ↓
   State → deterministic hardware behaviour
   (servo + OLED + WS2812 LEDs)
```

The LLM is NOT connected to the robot in M4. That coupling (`TutorResponse.interaction_state` → `RobotController`) is deferred until the communication layer is proven on real hardware.

---

## WebSocket Protocol

### Message Format

```json
{
    "version": 1,
    "type": "robot_state",
    "state": "thinking"
}
```

### Allowed States

| State         | Description                          |
|---------------|--------------------------------------|
| `idle`        | Neutral — servo center, LEDs off     |
| `listening`   | Student is speaking — calm blue LEDs |
| `thinking`    | Processing — amber LEDs, slight tilt |
| `speaking`    | Responding — green LEDs              |
| `happy`       | Positive feedback — warm yellow      |
| `encouraging` | Supportive — cyan LEDs, small nod    |
| `excited`     | High energy — magenta-pink LEDs      |

### Protocol Rules

1. Backend sends only validated semantic state names — never GPIO numbers, servo angles, or raw hardware commands.
2. ESP32 validates `version`, `type`, and `state` before acting.
3. Unknown states silently fall back to `idle`.
4. Protocol is versioned (`version: 1`) for future evolution.

---

## Backend Architecture

### New Files

```
primus/robot/
├── __init__.py        # Package marker
├── protocol.py        # ALLOWED_STATES, validate_state(), build_state_message(), parse_message()
├── controller.py      # RobotController (async WebSocket management)
└── ws.py              # WebSocket handler (accept → loop → disconnect)
```

### `RobotController`

```python
class RobotController:
    async def connect(websocket)    # register active robot WS
    async def disconnect()          # clear active connection
    def is_connected() → bool       # query connection status
    async def send_state(state) → bool  # validate + send
```

- Uses `asyncio.Lock` for thread-safe connection management.
- On send failure, automatically clears the connection.
- On new connect, closes any existing connection first.
- Raises `ValueError` for unknown states (before any I/O).

### WebSocket Endpoint

```
WebSocket /ws/robot
```

- ESP32 connects as **client** to the backend (not the other way around).
- Backend accepts, registers with `RobotController`, and listens for ack messages.
- On disconnect, `RobotController` is notified and the robot falls to idle.

### HTTP Test Endpoint

```
POST /robot/state
```

**Request:**
```json
{ "state": "thinking" }
```

**Responses:**

| Status | Condition | Body |
|--------|-----------|------|
| 200 | State sent successfully | `{"status": "sent", "state": "thinking"}` |
| 400 | Unknown state | `{"status": "invalid_state", "allowed_states": [...]}` |
| 422 | Missing `state` field | FastAPI validation error |
| 503 | No robot connected | `{"status": "robot_offline"}` |

---

## ESP32 Firmware

### File Structure

```
firmware/
├── config.example.h      # Configuration template (copy to config.h)
└── primus_esp32.ino      # Main Arduino sketch
```

`config.h` is gitignored — Wi-Fi passwords and network details are never committed.

### Required Libraries

| Library | Purpose |
|---------|---------|
| WebSockets (Markus Sattler) | WebSocket client |
| ArduinoJson (Benoît Blanchon) | JSON parsing |
| Adafruit SSD1306 + GFX | OLED display |
| Adafruit NeoPixel | WS2812 LED ring |
| ESP32Servo | MG90 servo control |

### Firmware Behaviour

1. **Startup:** Connect to Wi-Fi → attach servo (neutral) → init OLED → init LEDs → connect WebSocket.
2. **Connected:** Enter `idle` state. Listen for JSON messages.
3. **On message:** Validate version → validate type → validate state → `applyState()`.
4. **Disconnected:** Enter `idle` (safe state) → auto-reconnect via `ws.setReconnectInterval()`.
5. **Wi-Fi lost:** `WiFi.reconnect()` in loop.

### State → Hardware Mapping

| State | Servo | OLED Face | LED Colour |
|-------|-------|-----------|------------|
| idle | 90° (neutral) | `(- _ -)` | Off |
| listening | 90° | `( o_o )` | Blue (0,80,200) |
| thinking | 100° (tilt) | `( ._. )` | Amber (200,150,0) |
| speaking | 90° | `( ^_^ )` | Green (0,200,100) |
| happy | 85° (nod down) | `( ^o^ )` | Yellow (255,200,0) |
| encouraging | 95° (nod up) | `( ^_^ )` | Cyan (0,200,200) |
| excited | 82° | `( >_< )` | Pink (255,50,150) |

### Hardware Safety

- Servo range clamped to **40°–140°** (conservative for MG90).
- Neutral position is 90°.
- All movements are small offsets from neutral (±10° max).
- No abrupt or rapid movements.
- On any parse failure or unknown state → `idle`.
- On disconnect → `idle` + reconnect loop.
- Network data **never** directly specifies GPIO, angles, or LED values.

---

## Configuration

### Backend

No additional configuration needed. The WebSocket endpoint is at `/ws/robot` on the same host/port as the HTTP API.

### ESP32 (`config.example.h`)

| Define | Default | Description |
|--------|---------|-------------|
| `WIFI_SSID` | `YOUR_WIFI_SSID` | Wi-Fi network name |
| `WIFI_PASSWORD` | `YOUR_WIFI_PASSWORD` | Wi-Fi password |
| `WS_HOST` | `192.168.1.100` | Backend machine LAN IP |
| `WS_PORT` | `8000` | Backend port |
| `WS_PATH` | `/ws/robot` | WebSocket endpoint path |
| `RECONNECT_MS` | `5000` | Reconnect interval (ms) |
| `SERVO_PIN` | `13` | MG90 signal pin |
| `SERVO_MIN` | `40` | Minimum servo angle |
| `SERVO_MAX` | `140` | Maximum servo angle |
| `SERVO_NEUTRAL` | `90` | Neutral/resting angle |
| `LED_PIN` | `16` | WS2812 data pin |
| `LED_COUNT` | `12` | Number of LEDs |

---

## Testing

### Test Results

```
96 passed in 7.00s
├── test_api.py      19 passed  (M2 + M3)
├── test_engine.py   15 passed  (M1 + M3)
├── test_models.py   12 passed  (M1)
├── test_provider.py 11 passed  (M1)
├── test_robot.py    27 passed  ← M4
│   ├── TestProtocol          10 tests
│   ├── TestRobotController   10 tests
│   ├── TestWebSocketEndpoint  2 tests
│   └── TestRobotStateEndpoint 5 tests
└── test_session.py  12 passed  (M1)
```

### Test Coverage

| Test Class | Count | What's Verified |
|-----------|-------|----------------|
| `TestProtocol` | 10 | All states exist, validation, message building, parsing, version/type checking |
| `TestRobotController` | 10 | Connect, disconnect, send, failure handling, connection replacement, all states |
| `TestWebSocketEndpoint` | 2 | WS connect/disconnect lifecycle, message delivery to client |
| `TestRobotStateEndpoint` | 5 | Robot offline (503), invalid state (400), successful send (200), all states, missing field (422) |

---

## Manual Testing Procedure

### Prerequisites

1. Backend running: `uvicorn primus.api.app:create_app --factory --reload`
2. ESP32 flashed with `primus_esp32.ino` (with correct `config.h`)
3. ESP32 and backend on the same Wi-Fi network

### Steps

1. **Start the backend:**
   ```bash
   uvicorn primus.api.app:create_app --factory --reload
   ```

2. **Flash and power the ESP32.** Monitor serial output (115200 baud):
   ```
   --- PRIMUS V1 ESP32 ---
   Connecting to Wi-Fi.......
   IP: 192.168.1.42
   [WS] Connected to PRIMUS backend
   [STATE] → idle
   ```

3. **Test each state via `/docs` or curl:**
   ```bash
   curl -X POST http://localhost:8000/robot/state \
     -H "Content-Type: application/json" \
     -d '{"state": "thinking"}'
   # {"status": "sent", "state": "thinking"}
   ```

4. **Verify all 7 states:**
   `idle`, `listening`, `thinking`, `speaking`, `happy`, `encouraging`, `excited`

5. **Test invalid state:**
   ```bash
   curl -X POST http://localhost:8000/robot/state \
     -H "Content-Type: application/json" \
     -d '{"state": "fly_away"}'
   # 400: {"status": "invalid_state", "allowed_states": [...]}
   ```

6. **Test disconnect safety:**
   - Unplug ESP32 → verify backend still works normally
   - Replug ESP32 → verify auto-reconnect to `/ws/robot`
   - Turn off Wi-Fi → verify ESP32 enters idle and reconnects

7. **Verify backend independence:**
   - With ESP32 powered off, `POST /chat` still works
   - `POST /robot/state` returns `{"status": "robot_offline"}` (503)

---

## Files Added/Modified

### New Files (7)

| File | Purpose |
|------|---------|
| `primus/robot/__init__.py` | Package marker |
| `primus/robot/protocol.py` | Message schema, validation, building |
| `primus/robot/controller.py` | `RobotController` async abstraction |
| `primus/robot/ws.py` | WebSocket handler for `/ws/robot` |
| `firmware/config.example.h` | ESP32 configuration template |
| `firmware/primus_esp32.ino` | ESP32 Arduino firmware |
| `tests/test_robot.py` | 27 automated tests |

### Modified Files (4)

| File | Change |
|------|--------|
| `primus/api/app.py` | Wire `RobotController` + WebSocket endpoint |
| `primus/api/routes.py` | Add `POST /robot/state` endpoint |
| `.gitignore` | Add `firmware/config.h` |
| `pyproject.toml` | (no change needed — `websockets` pulled by FastAPI) |

---

## Assumptions

| # | Assumption | Rationale |
|---|-----------|-----------|
| 1 | Single ESP32 robot per backend instance | Simplifies connection management |
| 2 | ESP32 always initiates the WebSocket connection | Backend doesn't need to discover ESP32's IP |
| 3 | `asyncio.Lock` provides sufficient concurrency control | Single-process backend; no multi-worker issues |
| 4 | ArduinoJson v6 `StaticJsonDocument<256>` is sufficient | Robot messages are tiny (< 100 bytes) |
| 5 | `websockets` library (pulled by FastAPI/Starlette) handles reconnection | ESP32 uses `ws.setReconnectInterval()` |
| 6 | Servo MG90 operates safely within 40°–140° | Conservative range prevents stalling |

---

## Known Limitations

1. **No TutorEngine ↔ RobotController coupling** — intentional for M4. Will be added when communication is proven on hardware.
2. **Single robot connection** — only one ESP32 at a time. Connecting a second replaces the first.
3. **No robot authentication** — any client can connect to `/ws/robot`.
4. **No message acknowledgement from ESP32 to backend** — the backend sends fire-and-forget. The ESP32 could send status messages but the backend currently only logs them.
5. **Firmware state mapping is placeholder** — expressions are simple static positions, not animations. Will be refined in later milestones.
6. **No OTA firmware updates** — ESP32 must be flashed via USB.
7. **WebSocket is not encrypted** — `ws://` not `wss://`. Acceptable for local LAN demo.

---

## Next Milestone Starting Points

- **M5:** Couple `TutorResponse.interaction_state` → `RobotController.send_state()` so the AI tutor drives physical expressions automatically.
- **M6:** Add expression animation sequences (transitions, breathing idle, pulse effects).
- **M7:** Robot authentication on the WebSocket endpoint.
- **M12:** Physical audio integration (I2S microphone + amplifier).
