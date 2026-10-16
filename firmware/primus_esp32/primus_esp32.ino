/*
 * PRIMUS V1 — ESP32 WebSocket Robot Client
 * ==========================================
 * Hardware-verified firmware for the physical PRIMUS robot.
 *
 * VERIFIED HARDWARE (source of truth):
 *   MCU:        ESP32 DevKit V1
 *   SERVO:      MG90/MG90S          signal → GPIO 13  (separate 5V rail)
 *   OLED:       1.3" SH1106 I2C    SDA 21, SCL 22, addr 0x3C
 *   WS2812B:    12-LED ring         DI → GPIO 18
 *   INMP441:    I2S microphone      SCK 26, WS 25, SD 33  (L/R → GND)
 *   MAX98357A:  I2S amplifier       BCLK 27, LRC 14, DIN 19
 *   SPEAKER:    4Ω / 3W            connected to MAX98357A SPK+/SPK-
 *
 * SAFETY RULES
 *  - The servo only uses predefined safe angles (SERVO_MIN … SERVO_MAX).
 *  - On startup the robot enters "idle" (neutral servo, LEDs off, OLED ready).
 *  - On WebSocket disconnect the robot returns to "idle" and retries.
 *  - Unknown or malformed states silently fall back to "idle".
 *  - Network data NEVER directly controls GPIO, servo angle, or LED colour.
 *  - INMP441 and MAX98357A use SEPARATE I2S peripherals (I2S_NUM_1 / I2S_NUM_0).
 *
 * REQUIRED LIBRARIES
 *   Provided by ESP32 Arduino core (no install needed):
 *     WiFi, Wire, ESP_I2S.h (I2SClass)
 *   Install via Arduino Library Manager:
 *     WebSockets     by Markus Sattler    (v2.4+)
 *     ArduinoJson    by Benoît Blanchon  (v6.21+)
 *     Adafruit SH110X + Adafruit GFX     (for SH1106 OLED)
 *     Adafruit NeoPixel                   (for WS2812 LED ring)
 *     ESP32Servo                          (for MG90 servo)
 *
 * BOARD / CORE
 *   Board: "ESP32 Dev Module"
 *   ESP32 Arduino core 3.x (verified on 3.3.11)
 *
 * CONFIGURATION
 *   Copy config.example.h → config.h and set your Wi-Fi / backend values.
 *   Set TEST_MODE 1 in config.h to run hardware validation tests.
 */

// --- configuration -------------------------------------------------------
#include "config.h"         // local, gitignored

// --- libraries -----------------------------------------------------------
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SH110X.h>
#include <Adafruit_NeoPixel.h>
#include <ESP_I2S.h>
#include <math.h>

// --- globals -------------------------------------------------------------
WebSocketsClient ws;
Servo headServo;
Adafruit_SH1106G display(128, 64, &Wire, -1);
Adafruit_NeoPixel leds(WS2812_COUNT, WS2812_PIN, NEO_GRB + NEO_KHZ800);

// --- protocol constants --------------------------------------------------
static const int    PROTO_VERSION     = 1;
static const char*  ALLOWED_STATES[]  = {
    "idle", "listening", "thinking", "speaking",
    "happy", "encouraging", "excited"
};
static const int NUM_STATES = sizeof(ALLOWED_STATES) / sizeof(ALLOWED_STATES[0]);

// --- state tracking ------------------------------------------------------
String currentState = "idle";
unsigned long lastReconnect = 0;
bool wsConnected = false;

// Re-entrancy guard for playAudioFromBackend().  The streaming loop pumps
// ws.loop() to keep the WebSocket alive, so a robot_audio message can
// arrive MID-playback and re-enter playAudioFromBackend() via
// handleIncomingMessage().  While this flag is set, the nested call drops
// the request instead of starting a second concurrent playback.
// Declared here (not near playAudioFromBackend) so that loop(),
// checkRecordButton(), and the Serial trigger can all reference it.
bool audioPlaybackActive = false;

// RAII: clears the guard on every exit path — including the early
// HTTP-failure returns inside playAudioFromBackend().
struct AudioPlaybackGuard {
    AudioPlaybackGuard()  { audioPlaybackActive = true; }
    ~AudioPlaybackGuard() { audioPlaybackActive = false; }
};

// --- I2S instances (ESP32 Arduino 3.x class API) -------------------------
// I2S speaker → MAX98357A  (I2S_NUM_0, TX only)
// I2S mic     → INMP441    (I2S_NUM_1, RX only)
// Two independent I2S peripherals allow simultaneous mic + speaker.
I2SClass i2sSpeaker(I2S_NUM_0);
I2SClass i2sMic(I2S_NUM_1);

// --- recording buffer (sized after heap check in setup) -----------------
// PCM: 16 kHz mono 16-bit = 32 000 B/s.
// Duration is chosen at runtime based on free heap after all peripherals
// are initialised (Wi-Fi stack ≈ 50–80 KB, I2S DMA ≈ 4 KB, WS ≈ 5 KB).
static const uint32_t REC_BYTES_PER_SEC = (uint32_t)AUDIO_SAMPLE_RATE * 2;  // 32 000
static const uint8_t  REC_MIN_SEC       = 3;   // default (safe for low heap)
static const uint8_t  REC_MAX_SEC       = 5;   // only if heap clearly supports it
static const uint32_t REC_HEAP_MARGIN   = 32768; // 32 KB breathing room
uint8_t  recordDurationSec = REC_MIN_SEC;  // chosen in setup()
uint32_t recordBufferSize  = 0;            // bytes = recordDurationSec * REC_BYTES_PER_SEC

// --- recording state -------------------------------------------------------
static const uint32_t REC_DEBOUNCE_MS  = 50;
static const uint32_t REC_WS_PUMP_EVERY = 20;   // ws.loop() every N chunks
static const uint8_t  REC_CHUNK_SAMPLES = 64;    // matches readMicLevel()
static const uint16_t REC_CHUNK_BYTES   = REC_CHUNK_SAMPLES * sizeof(int16_t);  // 128 B
static const size_t   REC_WAV_HDR_SIZE  = 44;    // WAV header bytes

// =========================================================================
//  SETUP
// =========================================================================

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n=== PRIMUS V1 ESP32 ==="));
    Serial.println(F("Hardware-verified firmware"));

    // ── 1. Initialize safe hardware state ────────────────────────────

    // Servo (external 5V rail powers the motor; GPIO 13 is signal only)
    ESP32PWM::allocateTimer(0);
    headServo.setPeriodHertz(50);
    headServo.attach(SERVO_PIN, 500, 2400);
    headServo.write(SERVO_NEUTRAL);
    Serial.println(F("[INIT] Servo → neutral"));

    // OLED (SH1106 I2C)
    Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
    if (display.begin(OLED_ADDR, true)) {
        display.clearDisplay();
        display.setTextColor(SH110X_WHITE);
        display.setTextSize(1);
        display.setCursor(0, 0);
        display.println(F("PRIMUS V1"));
        display.println(F("Starting..."));
        display.display();
        Serial.println(F("[INIT] OLED → ready"));
    } else {
        Serial.println(F("[INIT] OLED → NOT FOUND (check wiring)"));
    }

    // WS2812 LED ring
    leds.begin();
    leds.clear();
    leds.show();
    Serial.println(F("[INIT] WS2812 → off"));

    // I2S — speaker output (I2S_NUM_0 → MAX98357A)
    // setPins(bclk, ws, dout, din, mclk) — TX only, no din/mclk
    i2sSpeaker.setPins(MAX98357_BCLK, MAX98357_LRC, MAX98357_DIN);
    if (i2sSpeaker.begin(I2S_MODE_STD, AUDIO_SAMPLE_RATE,
                         I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
        Serial.println(F("[INIT] I2S speaker (I2S_NUM_0) → ready"));
    } else {
        Serial.println(F("[INIT] I2S speaker init FAILED"));
    }

    // I2S — microphone input (I2S_NUM_1 → INMP441)
    // setPins(bclk, ws, dout, din) — RX only, dout=-1
    i2sMic.setPins(INMP441_SCK, INMP441_WS, -1, INMP441_SD);
    if (i2sMic.begin(I2S_MODE_STD, AUDIO_SAMPLE_RATE,
                     I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO,
                     I2S_STD_SLOT_LEFT)) {
        Serial.println(F("[INIT] I2S mic (I2S_NUM_1) → ready"));
    } else {
        Serial.println(F("[INIT] I2S mic init FAILED"));
    }

    // ── 2. Test mode (bypasses Wi-Fi/WebSocket) ──────────────────────
#if TEST_MODE
    Serial.println(F("\n[TEST] Hardware test mode active"));
    return;  // skip Wi-Fi and WebSocket
#endif

    // ── 3. Wi-Fi ─────────────────────────────────────────────────────
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print(F("Connecting to Wi-Fi"));
    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries < 40) {
        delay(500);
        Serial.print(".");
        tries++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.print(F("\nIP: "));
        Serial.println(WiFi.localIP());
    } else {
        Serial.println(F("\nWi-Fi FAILED — will retry in loop"));
    }

    // ── 4. WebSocket ─────────────────────────────────────────────────
    ws.begin(WS_HOST, WS_PORT, WS_PATH);
    ws.onEvent(onWsEvent);
    ws.setReconnectInterval(RECONNECT_MS);
    Serial.println(F("[WS] Connecting to backend..."));

    // ── Button (momentary, active LOW via internal pull-up) ───────────
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    Serial.printf("[INIT] Button → GPIO %d (INPUT_PULLUP)\n", BUTTON_PIN);

    // ── 5. Heap-based recording buffer sizing ─────────────────────────
    // Everything is now initialised (servo, OLED, NeoPixel, both I2S
    // buses, Wi-Fi stack, WebSocket client) so ESP.getFreeHeap() gives
    // a realistic baseline — not the raw power-on figure.
    uint32_t freeHeap = ESP.getFreeHeap();
    Serial.printf("[HEAP] Free after full init: %u bytes\n", freeHeap);

    uint32_t buf5 = REC_BYTES_PER_SEC * REC_MAX_SEC;  // 160 000 B
    uint32_t buf3 = REC_BYTES_PER_SEC * REC_MIN_SEC;  //  96 000 B

    if (freeHeap >= buf5 + REC_HEAP_MARGIN) {
        recordDurationSec = REC_MAX_SEC;
        recordBufferSize  = buf5;
        Serial.printf("[HEAP] → %u s record buffer (%u B), margin %u B\n",
                      REC_MAX_SEC, buf5, freeHeap - buf5);
    } else {
        recordDurationSec = REC_MIN_SEC;
        recordBufferSize  = buf3;
        Serial.printf("[HEAP] → %u s record buffer (%u B), margin %u B\n",
                      REC_MIN_SEC, buf3, freeHeap - buf3);
        if (freeHeap < buf3 + REC_HEAP_MARGIN) {
            Serial.println(F("[HEAP] WARNING: margin below 32 KB — recording may be unstable"));
        }
    }
}

// =========================================================================
//  LOOP
// =========================================================================

void loop() {
#if TEST_MODE
    runTestSequence();
    return;
#endif

    ws.loop();
    checkRecordButton();
    handleSerialCommands();

    // If Wi-Fi dropped, attempt reconnection
    if (WiFi.status() != WL_CONNECTED) {
        WiFi.reconnect();
        delay(1000);
    }
}

// =========================================================================
//  WEBSOCKET EVENT HANDLER
// =========================================================================

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED:
            Serial.println(F("[WS] Connected to PRIMUS backend"));
            wsConnected = true;
            applyState("idle");
            break;

        case WStype_DISCONNECTED:
            Serial.println(F("[WS] Disconnected — will auto-reconnect"));
            wsConnected = false;
            applyState("idle");          // safe state on disconnect
            break;

        case WStype_TEXT: {
            Serial.printf("[WS] << %s\n", payload);
            handleIncomingMessage((char*)payload, length);
            break;
        }

        case WStype_PING:
        case WStype_PONG:
            break;

        default:
            break;
    }
}

// =========================================================================
//  MESSAGE PARSING
// =========================================================================

void handleIncomingMessage(char* payload, size_t length) {
    // 512 bytes: robot_audio messages carry a longer audio_url payload
    // than the tiny robot_state messages this buffer was originally sized for.
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload, length);

    if (err) {
        Serial.printf("[MSG] JSON parse error: %s\n", err.c_str());
        applyState("idle");
        return;
    }

    // Validate protocol version
    int version = doc["version"] | 0;
    if (version != PROTO_VERSION) {
        Serial.printf("[MSG] Bad protocol version: %d\n", version);
        applyState("idle");
        return;
    }

    // Route by message type — state and audio are independent protocols.
    const char* msgType = doc["type"] | "";
    if (strcmp(msgType, "robot_state") == 0) {
        // Validate state against allowlist
        const char* state = doc["state"] | "idle";
        Serial.printf("[ROBOT] Received state: %s\n", state);
        if (!isAllowedState(state)) {
            Serial.printf("[MSG] Unknown state: %s → idle\n", state);
            applyState("idle");
            return;
        }

        applyState(state);

    } else if (strcmp(msgType, "robot_audio") == 0) {
        // Audio delivery is independent of the emotional state:
        // a malformed audio message is logged and ignored — it must
        // NEVER change the current applyState() state.
        const char* audioUrl = doc["audio_url"] | "";
        int sampleRate = doc["sample_rate"] | 0;
        if (strlen(audioUrl) == 0 || sampleRate <= 0) {
            Serial.println(F("[MSG] Malformed robot_audio message — ignored"));
            return;
        }
        playAudioFromBackend(audioUrl, sampleRate);

    } else {
        Serial.printf("[MSG] Unknown message type: %s\n", msgType);
        applyState("idle");
        return;
    }
}

// =========================================================================
//  STATE VALIDATION
// =========================================================================

bool isAllowedState(const char* state) {
    for (int i = 0; i < NUM_STATES; i++) {
        if (strcmp(state, ALLOWED_STATES[i]) == 0) return true;
    }
    return false;
}

// =========================================================================
//  STATE → HARDWARE MAPPING (deterministic, safe)
// =========================================================================

void applyState(const char* state) {
    currentState = String(state);
    Serial.printf("[STATE] → %s\n", state);
    Serial.printf("[ROBOT] OLED expression: %s\n", state);

    if      (strcmp(state, "idle")        == 0) doIdle();
    else if (strcmp(state, "listening")   == 0) doListening();
    else if (strcmp(state, "thinking")    == 0) doThinking();
    else if (strcmp(state, "speaking")    == 0) doSpeaking();
    else if (strcmp(state, "happy")       == 0) doHappy();
    else if (strcmp(state, "encouraging") == 0) doEncouraging();
    else if (strcmp(state, "excited")     == 0) doExcited();
    else                                      doIdle();   // final safety net
}

// ---- individual state implementations -----------------------------------
// All servo movements are small, conservative, and clamped to SERVO_MIN…SERVO_MAX.

static void safeServo(int angle) {
    if (angle < SERVO_MIN) angle = SERVO_MIN;
    if (angle > SERVO_MAX) angle = SERVO_MAX;
    headServo.write(angle);
}

void doIdle() {
    safeServo(SERVO_NEUTRAL);
    showOled("(- _ -)", "idle");
    leds.clear(); leds.show();
}

void doListening() {
    safeServo(SERVO_NEUTRAL);
    showOled("( o_o )", "listening");
    fillLeds(0, 80, 200);      // calm blue
}

void doThinking() {
    safeServo(SERVO_NEUTRAL + 8);   // subtle tilt right
    showOled("( ._. )", "thinking");
    fillLeds(200, 150, 0);     // warm amber
}

void doSpeaking() {
    safeServo(SERVO_NEUTRAL);
    showOled("( ^_^ )", "speaking");
    fillLeds(0, 200, 100);     // green
}

void doHappy() {
    safeServo(SERVO_NEUTRAL - 5);   // small nod down
    showOled("( ^o^ )", "happy");
    fillLeds(255, 200, 0);     // warm yellow
}

void doEncouraging() {
    safeServo(SERVO_NEUTRAL + 5);   // small nod up
    showOled("( ^_^ )", "encouraging");
    fillLeds(0, 200, 200);     // cyan
}

void doExcited() {
    safeServo(SERVO_NEUTRAL - 6);   // limited nod
    showOled("( >_< )", "excited");
    fillLeds(255, 50, 150);    // magenta-pink
}

// =========================================================================
//  HARDWARE HELPERS
// =========================================================================

void showOled(const char* face, const char* label) {
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(10, 8);
    display.println(face);
    display.setTextSize(1);
    display.setCursor(30, 48);
    display.println(label);
    display.display();
}

void fillLeds(uint8_t r, uint8_t g, uint8_t b) {
    for (int i = 0; i < WS2812_COUNT; i++) {
        leds.setPixelColor(i, leds.Color(r, g, b));
    }
    leds.show();
}

// =========================================================================
//  AUDIO — SPEAKER OUTPUT (I2S_NUM_0 → MAX98357A)
// =========================================================================

/// Play a simple sine-wave test tone on the speaker.
/// @param freqHz   Tone frequency in Hz (e.g. 440 for A4).
/// @param durationMs  How long to play (milliseconds).
void playTestTone(uint16_t freqHz, uint32_t durationMs) {
    const uint32_t totalSamples = (uint32_t)AUDIO_SAMPLE_RATE * durationMs / 1000;
    const int16_t amplitude = 8000;   // ~25 % of 16-bit range (safe volume)

    int16_t sampleBuf[64];    // write in small DMA-friendly chunks
    uint32_t sampleIdx = 0;

    while (sampleIdx < totalSamples) {
        int chunkSize = 64;
        if (sampleIdx + chunkSize > totalSamples) {
            chunkSize = totalSamples - sampleIdx;
        }
        for (int i = 0; i < chunkSize; i++) {
            double t = (double)(sampleIdx + i) / AUDIO_SAMPLE_RATE;
            int16_t val = (int16_t)(amplitude * sin(2.0 * M_PI * freqHz * t));
            sampleBuf[i] = val;
        }
        i2sSpeaker.write(sampleBuf, chunkSize * sizeof(int16_t));
        sampleIdx += chunkSize;
    }
}

// =========================================================================
//  AUDIO — TTS PLAYBACK (HTTP PCM stream → I2S_NUM_0 speaker)
// =========================================================================

// Abort playback if the HTTP body stalls for this long (server hang).
static const uint32_t AUDIO_STALL_TIMEOUT_MS = 5000;

/// Stream a raw PCM16 audio file from the backend to the speaker.
/// The body is streamed in small chunks — never buffered fully in RAM.
/// Any HTTP/I2S failure is logged and playback is skipped gracefully;
/// the current emotional state is never touched by this function.
/// @param audioPath   Relative URL path on the backend (e.g. "/audio/x.pcm").
/// @param sampleRate  Sample rate of the PCM data (e.g. 16000 Hz).
void playAudioFromBackend(const char* audioPath, int sampleRate) {
    // A playback is already running (possible when ws.loop() below delivers
    // a second robot_audio mid-stream) → drop the new request, keep playing.
    if (audioPlaybackActive) {
        Serial.println(F("[AUDIO] Playback already in progress — request ignored"));
        return;
    }
    AudioPlaybackGuard playbackGuard;

    String url = String("http://") + WS_HOST + ":" + String(WS_PORT) + audioPath;
    Serial.printf("[AUDIO] GET %s @ %d Hz\n", audioPath, sampleRate);

    // Reconfigure the speaker I2S if the requested rate differs from the
    // boot-time default — a mismatch would play pitch-shifted/garbled.
    if (sampleRate != AUDIO_SAMPLE_RATE) {
        i2sSpeaker.end();
        i2sSpeaker.setPins(MAX98357_BCLK, MAX98357_LRC, MAX98357_DIN);
        if (!i2sSpeaker.begin(I2S_MODE_STD, sampleRate,
                              I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
            Serial.println(F("[AUDIO] I2S re-init failed — playback skipped"));
            i2sSpeaker.begin(I2S_MODE_STD, AUDIO_SAMPLE_RATE,
                             I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
            return;
        }
    }

    HTTPClient http;
    if (!http.begin(url)) {
        Serial.println(F("[AUDIO] HTTP begin failed"));
        return;
    }

    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        Serial.printf("[AUDIO] HTTP GET failed: %d\n", code);
        http.end();
        return;
    }

    WiFiClient* stream = http.getStreamPtr();
    if (stream == nullptr) {
        Serial.println(F("[AUDIO] No stream available"));
        http.end();
        return;
    }

    // Stream the body straight into the I2S DMA in 64-sample chunks —
    // the same small-chunk pattern used by playTestTone()/readMicLevel().
    int len = http.getSize();            // total bytes, or -1 if unknown
    uint8_t buf[128];                    // 64 samples × int16
    uint32_t totalBytes = 0;
    uint32_t lastDataMs = millis();
    uint8_t wsPumpCounter = 0;           // pumps ws.loop() every ~20 chunks

    // TCP delivers arbitrary byte counts, so a read chunk can end
    // mid-sample.  An odd trailing byte is CARRIED into the next buffer
    // — never discarded.  (The old code masked it off, which shifted
    // every later 16-bit sample by one byte and garbled the audio.)
    uint8_t carryByte = 0;
    bool haveCarry = false;

    while (http.connected() && (len > 0 || len == -1)) {
        // Pump the WebSocket every ~20 loop iterations (~20 chunks while
        // streaming, ~20 ms while stalled) so ping/pong keepalives are
        // answered and the connection survives long playback.
        // Re-entry safety: a robot_audio message delivered here re-enters
        // playAudioFromBackend(), hits the audioPlaybackActive guard, and
        // is dropped — no recursive playback.  robot_state messages stay
        // unaffected: applyState() is quick and drives separate hardware.
        if (++wsPumpCounter >= 20) {
            wsPumpCounter = 0;
            ws.loop();
        }

        size_t avail = stream->available();
        if (avail == 0) {
            if (millis() - lastDataMs > AUDIO_STALL_TIMEOUT_MS) {
                Serial.println(F("[AUDIO] Stream stalled — playback aborted"));
                break;
            }
            delay(1);
            continue;
        }
        lastDataMs = millis();

        // Reserve room at the front of the buffer for a carried byte.
        size_t toRead = sizeof(buf) - (haveCarry ? 1 : 0);
        if (toRead > avail) toRead = avail;
        if (len > 0 && (int)toRead > len) toRead = (size_t)len;

        size_t off = 0;
        if (haveCarry) { buf[0] = carryByte; off = 1; haveCarry = false; }
        size_t fresh = stream->readBytes(buf + off, toRead);
        if (len > 0) len -= (int)fresh;   // count only bytes truly consumed

        size_t have = off + fresh;
        if (have == 0) break;            // stream exhausted
        if (have & 1) {                   // odd count → save the last byte
            carryByte = buf[have - 1];
            haveCarry = true;
            have--;
        }
        if (have > 0) {
            i2sSpeaker.write(buf, have); // whole 16-bit samples only
            totalBytes += have;
        }
    }

    Serial.printf("[AUDIO] Done — %u bytes played\n", (unsigned)totalBytes);
    http.end();
}

// =========================================================================
//  AUDIO — MICROPHONE INPUT (I2S_NUM_1 → INMP441)
// =========================================================================

/// Read a short burst of microphone samples and report RMS level.
/// Returns the RMS amplitude (0–32767).  0 = silence.
/// This proves the INMP441 is connected and producing data.
/// NOTE: I2S DMA delivers 32-bit stereo frames even in mono mode;
/// we extract only the left channel (low 16 bits) per frame.
int16_t readMicLevel(uint32_t durationMs) {
    const uint32_t totalSamples = (uint32_t)AUDIO_SAMPLE_RATE * durationMs / 1000;
    int32_t frameBuf[64];               // 32-bit stereo frames from DMA
    uint32_t sampleIdx = 0;
    double sumSquares = 0.0;
    uint32_t count = 0;

    while (sampleIdx < totalSamples) {
        int chunkSize = 64;
        if (sampleIdx + chunkSize > totalSamples) {
            chunkSize = totalSamples - sampleIdx;
        }
        size_t bytesRead = i2sMic.readBytes(
            (char*)frameBuf, chunkSize * sizeof(int32_t));
        size_t frames = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < frames; i++) {
            int16_t sample = (int16_t)(frameBuf[i] & 0xFFFF);
            sumSquares += (double)sample * (double)sample;
            count++;
        }
        sampleIdx += chunkSize;
    }

    if (count == 0) return 0;
    return (int16_t)sqrt(sumSquares / count);
}

// =========================================================================
//  RECORDING — button-triggered capture + WAV wrapping (Stage 1)
// =========================================================================

/// Write a minimal 44-byte WAV header (RIFF/WAVE + fmt + data) into buf.
/// Caller must ensure buf is at least 44 bytes.  All fields are
/// little-endian per the RIFF specification.
static void writeWavHeader(uint8_t* buf, uint32_t dataSize) {
    uint32_t fileSize   = 36 + dataSize;           // RIFF chunk data
    uint16_t channels   = 1;                        // mono
    uint32_t sampleRate = AUDIO_SAMPLE_RATE;        // 16 000
    uint16_t bps        = 16;                       // bits per sample
    uint16_t blockAlign = channels * (bps / 8);     // 2 B/sample
    uint32_t byteRate   = sampleRate * blockAlign;   // 32 000 B/s

    // -- RIFF descriptor (12 B) -----------------------------------------
    memcpy(buf + 0,  "RIFF", 4);
    memcpy(buf + 8,  "WAVE", 4);
    buf[4] = fileSize & 0xFF;
    buf[5] = (fileSize >> 8)  & 0xFF;
    buf[6] = (fileSize >> 16) & 0xFF;
    buf[7] = (fileSize >> 24) & 0xFF;

    // -- fmt sub-chunk (24 B) -------------------------------------------
    memcpy(buf + 12, "fmt ", 4);
    buf[16] = 16; buf[17] = 0; buf[18] = 0; buf[19] = 0; // sub-chunk size
    buf[20] = 1;  buf[21] = 0;                             // PCM
    buf[22] = channels & 0xFF;
    buf[23] = (channels >> 8) & 0xFF;
    buf[24] = sampleRate & 0xFF;
    buf[25] = (sampleRate >> 8)  & 0xFF;
    buf[26] = (sampleRate >> 16) & 0xFF;
    buf[27] = (sampleRate >> 24) & 0xFF;
    buf[28] = byteRate & 0xFF;
    buf[29] = (byteRate >> 8)  & 0xFF;
    buf[30] = (byteRate >> 16) & 0xFF;
    buf[31] = (byteRate >> 24) & 0xFF;
    buf[32] = blockAlign & 0xFF;
    buf[33] = (blockAlign >> 8) & 0xFF;
    buf[34] = bps & 0xFF;
    buf[35] = (bps >> 8) & 0xFF;

    // -- data sub-chunk header (8 B) ------------------------------------
    memcpy(buf + 36, "data", 4);
    buf[40] = dataSize & 0xFF;
    buf[41] = (dataSize >> 8)  & 0xFF;
    buf[42] = (dataSize >> 16) & 0xFF;
    buf[43] = (dataSize >> 24) & 0xFF;
}

/// Button-triggered recording capture.
///
/// Called from checkRecordButton() on a debounced falling edge.
/// Allocates a SINGLE buffer (recordBufferSize + 44) and reads PCM
/// directly into buffer+44 — no second allocation, no memcpy.
/// The WAV header is written into the first 44 bytes in-place.
/// Pumps ws.loop() every ~20 chunks to keep the WebSocket alive.
///
/// Stage 1: Serial-only verification — no HTTP send.
static void recordAndWrapWav() {
    // Single allocation: [WAV header 44 B][PCM data recordBufferSize B]
    uint32_t totalAlloc = recordBufferSize + REC_WAV_HDR_SIZE;

    Serial.println(F("\n[REC] === Recording started ==="));
    Serial.printf("[REC] Allocating %u B (hdr 44 + pcm %u, %u s)\n",
                  totalAlloc, recordBufferSize, recordDurationSec);

    uint8_t* wavBuf = (uint8_t*)malloc(totalAlloc);
    if (!wavBuf) {
        Serial.printf("[REC] ALLOCATION FAILED — need %u B, free heap %u B\n",
                      totalAlloc, ESP.getFreeHeap());
        return;
    }

    Serial.printf("[REC] Heap after alloc: %u B\n", ESP.getFreeHeap());

    // Pre-write the WAV header into the first 44 bytes
    writeWavHeader(wavBuf, recordBufferSize);

    // -- Chunked I2S capture directly into wavBuf + 44 ------------------
    // ESP32 I2S standard-mode DMA delivers full 32-bit stereo frames
    // (16-bit left + 16-bit right) even when I2S_SLOT_MODE_MONO is set.
    // The mono mode selects which slot the hardware clocks in, but
    // readBytes() returns the raw interleaved data.  We must read as
    // 32-bit frames and extract only the left channel.
    uint8_t* pcmStart = wavBuf + REC_WAV_HDR_SIZE;
    uint32_t totalBytes = 0;
    uint32_t chunkCount = 0;
    uint32_t nextLogAt  = REC_BYTES_PER_SEC;  // progress at ~1 s
    uint32_t wsDiv      = 0;
    int32_t  frameBuf[REC_CHUNK_SAMPLES];     // 32-bit stereo frames from DMA
    uint32_t startMs    = millis();

    while (totalBytes < recordBufferSize) {
        uint32_t remaining = recordBufferSize - totalBytes;
        uint16_t toRead    = (remaining < REC_CHUNK_BYTES)
                             ? (uint16_t)remaining : REC_CHUNK_BYTES;

        size_t bytesRead = i2sMic.readBytes(
            (char*)frameBuf, toRead);
        if (bytesRead == 0) break;

        // Extract left channel (low 16 bits) from each 32-bit frame
        size_t frames = bytesRead / sizeof(int32_t);
        int16_t* dst = (int16_t*)(pcmStart + totalBytes);
        for (size_t i = 0; i < frames; i++) {
            dst[i] = (int16_t)(frameBuf[i] & 0xFFFF);
        }
        totalBytes += frames * sizeof(int16_t);
        chunkCount++;

        // Pump WebSocket every REC_WS_PUMP_EVERY chunks (~20 chunks)
        if (++wsDiv >= REC_WS_PUMP_EVERY) {
            wsDiv = 0;
            ws.loop();
        }

        // Progress every ~1 s of audio
        if (totalBytes >= nextLogAt) {
            Serial.printf("[REC]   %u / %u B\n", totalBytes, recordBufferSize);
            nextLogAt += REC_BYTES_PER_SEC;
        }
    }

    uint32_t elapsedMs = millis() - startMs;

    Serial.println(F("[REC] === Recording complete ==="));
    Serial.printf("[REC] Samples captured: %u\n", totalBytes / sizeof(int16_t));
    Serial.printf("[REC] Bytes captured:   %u / %u\n", totalBytes, recordBufferSize);
    Serial.printf("[REC] Wall time:        %u ms\n", elapsedMs);
    Serial.printf("[REC] Chunks read:      %u\n", chunkCount);

    // -- WAV verification (single buffer — no copy needed) ---------------
    uint32_t wavTotal = REC_WAV_HDR_SIZE + totalBytes;
    Serial.printf("[REC] WAV total:   %u B (hdr 44 + pcm %u)\n",
                  wavTotal, totalBytes);
    Serial.printf("[REC] Expected:    %u B — %s\n",
                  wavTotal,
                  (wavTotal == REC_WAV_HDR_SIZE + recordBufferSize) ? "MATCH" : "MISMATCH");

    // First 32 PCM samples — verify NO back-to-back duplicates
    Serial.print(F("[REC] First 32 samples: "));
    int16_t* s = (int16_t*)pcmStart;
    int dumpCount = (totalBytes / sizeof(int16_t) < 32)
                    ? (int)(totalBytes / sizeof(int16_t)) : 32;
    for (int i = 0; i < dumpCount; i++) {
        Serial.printf("%d ", s[i]);
    }
    Serial.println();

    // -- WebSocket health check -----------------------------------------
    Serial.printf("[REC] WS connected: %s\n",
                  wsConnected ? "yes" : "NO");

    // -- Heap leak check (single free — no double alloc to track) -------
    free(wavBuf);
    Serial.printf("[REC] Heap after free: %u B — buffer released\n",
                  ESP.getFreeHeap());
    Serial.println();
}

/// Handle Serial Monitor commands for testing (emotion + recording).
/// Type a single character in the Serial Monitor input box + Enter:
///   1=idle  2=listening  3=thinking  4=speaking
///   5=happy  6=encouraging  7=excited  r/R=record
static void handleSerialCommands() {
    if (Serial.available() == 0) return;
    char c = Serial.read();

    const char* cmdState = nullptr;
    switch (c) {
        case '1': cmdState = "idle";          break;
        case '2': cmdState = "listening";     break;
        case '3': cmdState = "thinking";      break;
        case '4': cmdState = "speaking";      break;
        case '5': cmdState = "happy";         break;
        case '6': cmdState = "encouraging";   break;
        case '7': cmdState = "excited";       break;
        case 'r': case 'R':
            if (audioPlaybackActive) {
                Serial.println(F("[REC] Serial trigger skipped — playback in progress"));
            } else {
                Serial.println(F("[REC] Serial trigger received"));
                recordAndWrapWav();
            }
            return;
        default:
            return;  // ignore other characters
    }

    if (cmdState) {
        Serial.printf("[TEST] Serial emotion trigger → %s\n", cmdState);
        applyState(cmdState);
    }
}

/// Poll the record button with simple 50 ms debounce.
static void checkRecordButton() {
    static bool     lastPressed = false;
    static uint32_t lastPressMs = 0;

    bool pressed = (digitalRead(BUTTON_PIN) == LOW);

    if (pressed && !lastPressed) {
        uint32_t now = millis();
        if (now - lastPressMs >= REC_DEBOUNCE_MS) {
            lastPressMs = now;
            // Block recording during speaker playback to avoid feedback
            if (audioPlaybackActive) {
                Serial.println(F("[REC] Skipped — audio playback in progress"));
            } else {
                recordAndWrapWav();
            }
        }
    }
    lastPressed = pressed;
}

// =========================================================================
//  TEST MODE — hardware validation without backend
// =========================================================================

#if TEST_MODE

void runTestSequence() {
    Serial.println(F("\n========== HARDWARE TEST SEQUENCE =========="));

    testOled();
    delay(2000);

    testWS2812();
    delay(1000);

    testServo();
    delay(1000);

    testSpeaker();
    delay(1000);

    testMicrophone();

    testWebSocket();

    Serial.println(F("\n========== TEST SEQUENCE COMPLETE =========="));
    Serial.println(F("Rebooting in 10 seconds..."));
    delay(10000);
    ESP.restart();
}

// ---- Test 1: OLED -------------------------------------------------------
void testOled() {
    Serial.println(F("[TEST 1/6] OLED"));
    display.clearDisplay();
    display.setTextColor(SH110X_WHITE);
    display.setTextSize(2);
    display.setCursor(0, 0);
    display.println(F("OLED TEST"));
    display.setTextSize(1);
    display.setCursor(0, 24);
    display.println(F("SH1106 128x64"));
    display.setCursor(0, 36);
    display.printf("Addr: 0x%02X", OLED_ADDR);
    display.setCursor(0, 48);
    display.println(F("SDA=21 SCL=22"));
    display.display();
    Serial.println(F("  → Check display shows test text"));
    delay(3000);
}

// ---- Test 2: WS2812 LED ring --------------------------------------------
void testWS2812() {
    Serial.println(F("[TEST 2/6] WS2812 LED ring"));
    Serial.printf("  LEDs: %d, Pin: GPIO %d\n", WS2812_COUNT, WS2812_PIN);

    // Red → Green → Blue → White → Off
    fillLeds(255, 0, 0);     delay(800);
    Serial.println(F("  → Should show RED"));
    fillLeds(0, 255, 0);     delay(800);
    Serial.println(F("  → Should show GREEN"));
    fillLeds(0, 0, 255);     delay(800);
    Serial.println(F("  → Should show BLUE"));
    fillLeds(50, 50, 50);    delay(800);   // dim white
    Serial.println(F("  → Should show dim WHITE"));
    leds.clear(); leds.show();
    Serial.println(F("  → Should be OFF"));
}

// ---- Test 3: Servo ------------------------------------------------------
void testServo() {
    Serial.println(F("[TEST 3/6] Servo (MG90)"));
    Serial.printf("  Pin: GPIO %d, Neutral: %d°\n", SERVO_PIN, SERVO_NEUTRAL);
    Serial.printf("  Range: %d° – %d°\n", SERVO_MIN, SERVO_MAX);

    safeServo(SERVO_NEUTRAL);         delay(1000);
    Serial.println(F("  → Neutral position"));

    safeServo(SERVO_NEUTRAL + 15);    delay(1000);
    Serial.println(F("  → Tilted right"));

    safeServo(SERVO_NEUTRAL - 15);    delay(1000);
    Serial.println(F("  → Tilted left"));

    safeServo(SERVO_NEUTRAL);         delay(500);
    Serial.println(F("  → Back to neutral"));
}

// ---- Test 4: Speaker (MAX98357A) ----------------------------------------
void testSpeaker() {
    Serial.println(F("[TEST 4/6] Speaker (MAX98357A)"));
    Serial.printf("  BCLK: GPIO %d, LRC: GPIO %d, DIN: GPIO %d\n",
                  MAX98357_BCLK, MAX98357_LRC, MAX98357_DIN);
    Serial.println(F("  Playing 440 Hz tone for 2 seconds..."));

    playTestTone(440, 2000);

    Serial.println(F("  → Should hear A4 tone from speaker"));
    delay(500);

    Serial.println(F("  Playing 880 Hz tone for 1 second..."));
    playTestTone(880, 1000);

    Serial.println(F("  → Should hear A5 tone from speaker"));
}

// ---- Test 5: Microphone (INMP441) ---------------------------------------
void testMicrophone() {
    Serial.println(F("[TEST 5/6] Microphone (INMP441)"));
    Serial.printf("  SCK: GPIO %d, WS: GPIO %d, SD: GPIO %d\n",
                  INMP441_SCK, INMP441_WS, INMP441_SD);

    // Take 3 readings
    for (int i = 0; i < 3; i++) {
        int16_t rms = readMicLevel(500);   // 500 ms sample
        Serial.printf("  Reading %d: RMS level = %d\n", i + 1, rms);
        delay(300);
    }

    Serial.println(F("  → RMS > 100 means mic is picking up sound"));
    Serial.println(F("  → RMS ≈ 0 may indicate wiring issue or silence"));
}

// ---- Test 6: WebSocket --------------------------------------------------
void testWebSocket() {
    Serial.println(F("[TEST 6/6] WebSocket connectivity"));
    Serial.printf("  Host: %s:%d%s\n", WS_HOST, WS_PORT, WS_PATH);
    Serial.println(F("  Attempting connection..."));

    // Temporarily start Wi-Fi + WS for this test
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries < 20) {
        delay(500);
        Serial.print(".");
        tries++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.print(F("\n  IP: "));
        Serial.println(WiFi.localIP());

        ws.begin(WS_HOST, WS_PORT, WS_PATH);
        ws.onEvent(onWsEvent);

        // Give it a few seconds to connect
        for (int i = 0; i < 10; i++) {
            ws.loop();
            delay(500);
            if (wsConnected) {
                Serial.println(F("  → WebSocket CONNECTED"));
                break;
            }
        }
        if (!wsConnected) {
            Serial.println(F("  → WebSocket NOT CONNECTED (check backend)"));
        }
        ws.disconnect();
    } else {
        Serial.println(F("\n  → Wi-Fi FAILED (check credentials)"));
    }
}

#endif // TEST_MODE
