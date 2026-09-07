# PRIMUS V1 — ESP32 Firmware Hardware Guide

## Verified Pin Map (Source of Truth)

| Component | Signal | GPIO | Notes |
|-----------|--------|------|-------|
| **Servo** (MG90) | Signal | 13 | External 5V rail (not ESP32 5V) |
| **OLED** (SH1106) | SDA | 21 | I2C data |
| **OLED** (SH1106) | SCL | 22 | I2C clock |
| **WS2812B** ring | DI | 18 | 12 SMD RGB LEDs |
| **INMP441** mic | SCK | 26 | I2S bit clock (I2S_NUM_1) |
| **INMP441** mic | WS | 25 | I2S word select (I2S_NUM_1) |
| **INMP441** mic | SD | 33 | I2S serial data (I2S_NUM_1) |
| **INMP441** mic | L/R | GND | Selects left channel |
| **MAX98357A** amp | BCLK | 27 | I2S bit clock (I2S_NUM_0) |
| **MAX98357A** amp | LRC | 14 | I2S left/right clock (I2S_NUM_0) |
| **MAX98357A** amp | DIN | 19 | I2S serial data (I2S_NUM_0) |
| **MAX98357A** amp | SD | 3.3V | Shutdown — always enabled |
| **Speaker** | — | — | 4Ω/3W → MAX98357A SPK+/SPK- |

### Power Distribution

| Rail | Powered Components | Source |
|------|-------------------|--------|
| 3.3V | ESP32, OLED VCC, INMP441 VDD, MAX98357A SD | ESP32 3.3V output |
| 5V (main) | ESP32 VIN, MAX98357A VIN, WS2812B 5V | Buck converter |
| 5V (servo) | MG90 VCC | Separate regulated 5V rail |
| GND | All components (common ground) | Shared |

---

## Required Arduino Libraries

### Provided by ESP32 Arduino Core (no install needed)
- `WiFi.h`
- `Wire.h`
- `ESP_I2S.h` (I2SClass)

### Install via Arduino Library Manager
| Library | Author | Version | Purpose |
|---------|--------|---------|---------|
| WebSockets | Markus Sattler | v2.4+ | WebSocket client |
| ArduinoJson | Benoît Blanchon | v6.21+ | JSON protocol parsing |
| **Adafruit SH110X** | Adafruit | latest | SH1106 OLED display |
| Adafruit GFX | Adafruit | latest | Graphics primitives (SH110X dependency) |
| Adafruit NeoPixel | Adafruit | latest | WS2812 LED ring |
| ESP32Servo | Kevin Harrington | latest | MG90 servo PWM |

### Board Configuration
- **Board**: ESP32 Dev Module
- **ESP32 Arduino Core**: 3.x (e.g. 3.3.11)
- **Flash Size**: 4MB
- **Upload Speed**: 921600

---

## Compilation

### Using Arduino IDE
1. Open `firmware/primus_esp32.ino` in Arduino IDE
2. Install ESP32 board support: **File → Preferences → Boards Manager URL** → add `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Tools → Board → ESP32 Arduino → ESP32 Dev Module**
4. Install libraries listed above via **Tools → Manage Libraries**
5. Copy `firmware/config.example.h` → `firmware/config.h`
6. Edit `config.h`: set Wi-Fi credentials and backend IP
7. Compile: **Sketch → Verify/Compile**

### Using arduino-cli (command line)
```bash
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/
```

### Known Compilation Concerns

| Item | Status | Notes |
|------|--------|-------|
| SH1106 OLED | Verified in code | `Adafruit_SH1106G(128, 64, &Wire, -1)` — requires "Adafruit SH110X" library |
| I2S dual-port | Verified in code | `I2S_NUM_0` for speaker, `I2S_NUM_1` for mic — uses ESP32 Arduino 3.x `ESP_I2S.h` class API |
| I2SClass instances | Verified in code | `I2SClass i2sSpeaker(I2S_NUM_0)` / `I2SClass i2sMic(I2S_NUM_1)` — 3.x class-based API |
| `setPins()` + `begin()` | Verified in code | Correct 3.x API (NOT legacy `i2s_driver_install` / `i2s_set_pin` from 2.x) |
| Servo clamping | Verified in code | `safeServo()` clamps to SERVO_MIN(50°)…SERVO_MAX(130°) |
| Test mode | Verified in code | `#define TEST_MODE 1` bypasses Wi-Fi/WebSocket, runs hardware tests |

**Requires physical validation**: All I2S and I2C communication must be tested on actual hardware.

---

## Upload Procedure

### Arduino IDE
1. Connect ESP32 via USB
2. Select correct **COM port** under **Tools → Port**
3. **Tools → Upload Speed** → 921600
4. Click **Upload**
5. If upload fails: hold **BOOT** button while clicking Upload, release after upload starts

### Serial Monitor
- Baud rate: **115200**
- Shows initialization log, Wi-Fi status, WebSocket events, and state transitions

---

## Test Mode

Set `TEST_MODE 1` in `config.h` to run the hardware validation sequence without Wi-Fi/WebSocket.

### Test Sequence (runs automatically after boot)

| # | Test | What to Look For |
|---|------|------------------|
| 1 | OLED | Display shows "OLED TEST", "SH1106 128x64", I2C address |
| 2 | WS2812 | Ring cycles: Red → Green → Blue → White → Off |
| 3 | Servo | Head tilts: neutral → right → left → neutral |
| 4 | Speaker | Two tones: A4 (440 Hz, 2s) then A5 (880 Hz, 1s) |
| 5 | Microphone | Serial shows RMS levels (should be >100 with ambient noise) |
| 6 | WebSocket | Attempts Wi-Fi + WS connection (may fail without backend) |

After tests complete, ESP32 reboots after 10 seconds.

---

## Normal Mode Operation (TEST_MODE 0)

### Startup Sequence
1. Servo → neutral (90°)
2. OLED → "PRIMUS V1 / Starting..."
3. WS2812 → all off
4. I2S speaker → initialized (I2S_NUM_0)
5. I2S microphone → initialized (I2S_NUM_1)
6. Wi-Fi → connect
7. WebSocket → connect to backend

### WebSocket Protocol
```json
{"version": 1, "type": "robot_state", "state": "thinking"}
```

### Semantic State → Hardware Mapping

| State | Servo | OLED Face | LED Color |
|-------|-------|-----------|-----------|
| idle | 90° (neutral) | `(- _ -)` | Off |
| listening | 90° (neutral) | `( o_o )` | Blue (0,80,200) |
| thinking | 98° (slight right) | `( ._. )` | Amber (200,150,0) |
| speaking | 90° (neutral) | `( ^_^ )` | Green (0,200,100) |
| happy | 85° (small nod) | `( ^o^ )` | Yellow (255,200,0) |
| encouraging | 95° (nod up) | `( ^_^ )` | Cyan (0,200,200) |
| excited | 84° (limited nod) | `( >_< )` | Magenta (255,50,150) |

### Disconnect Behavior
- WebSocket disconnect → servo neutral, LEDs off, auto-reconnect every 5s
- Wi-Fi disconnect → automatic reconnect attempt
- Unknown/malformed state → reject, fall back to "idle"

---

## I2S Architecture (ESP32 Arduino 3.x Class API)

```
I2SClass i2sSpeaker(I2S_NUM_0)       I2SClass i2sMic(I2S_NUM_1)
setPins(27, 14, 19)                  setPins(26, 25, -1, 33)
begin(I2S_MODE_STD, 16000,           begin(I2S_MODE_STD, 16000,
  I2S_DATA_BIT_WIDTH_16BIT,            I2S_DATA_BIT_WIDTH_16BIT,
  I2S_SLOT_MODE_MONO)                  I2S_SLOT_MODE_MONO,
write(buf, size)                       I2S_STD_SLOT_LEFT)
                                     readBytes(buf, size)
        ↓                                      ↓
MAX98357A amplifier              INMP441 microphone
        ↓                                      ↓
4Ω/3W speaker                    Audio samples (RMS test)
```

**Header**: `#include <ESP_I2S.h>` (part of ESP32 Arduino core 3.x)

**Two instances** of `I2SClass` bound to separate I2S peripherals via the constructor.
The `setPins()` method configures GPIO; `begin()` initializes the driver.
`write()` and `readBytes()` handle DMA transfers transparently.

---

## Verification Status

| Subsystem | Verified in Code | Requires Physical Validation |
|-----------|:-:|:-:|
| Pin definitions | ✅ | — |
| Servo movement (safeServo clamp) | ✅ | Verify smooth motion, no jitter |
| OLED SH1106 init | ✅ | Verify display lights up, text readable |
| WS2812 colors | ✅ | Verify all 12 LEDs light correctly |
| I2S speaker init | ✅ | Verify tone audible from speaker |
| I2S microphone init | ✅ | Verify RMS > 0 with ambient noise |
| WebSocket protocol | ✅ | Verify state commands from backend |
| Safe startup sequence | ✅ | Verify no glitches on power-on |
| Disconnect → idle | ✅ | Verify robot returns to idle on disconnect |
| Test mode sequence | ✅ | Run full hardware validation |
| Simultaneous I2S in/out | ✅ | **Critical** — test both ports active together |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| OLED blank | Wrong I2C address or wiring | Check SDA=21, SCL=22; try 0x3D |
| OLED compile error | Missing library | Install "Adafruit SH110X" from Library Manager |
| LEDs not lighting | Wrong pin | Verify WS2812 DI = GPIO 18 |
| Servo jitter | Power supply noise | Ensure separate 5V rail, common GND |
| Servo no movement | Timer conflict | ESP32Servo uses `allocateTimer(0)` |
| Speaker silent | I2S pin mismatch | Verify BCLK=27, LRC=14, DIN=19 |
| Speaker garbled audio | Wrong slot mode | Use `I2S_SLOT_MODE_MONO` (3.x API) for mono output |
| Mic reads all zeros | Wiring or I2S config | Verify SCK=26, WS=25, SD=33; L/R→GND; `I2S_STD_SLOT_LEFT` for mic |
| `driver/i2s.h` compile error | Legacy 2.x API used | Use `ESP_I2S.h` and `I2SClass` (ESP32 core 3.x) |
| Wi-Fi fails | Wrong credentials | Check config.h WIFI_SSID/WIFI_PASSWORD |
| WS never connects | Backend not running | Start PRIMUS server, check WS_HOST IP |
