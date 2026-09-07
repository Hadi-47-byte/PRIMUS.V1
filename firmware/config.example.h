// ============================================================
// PRIMUS V1 — ESP32 Firmware Configuration
// ============================================================
//
// Copy this file to "config.h" and fill in your Wi-Fi / backend
// values.  config.h is gitignored — never commit real credentials.
//
// HARDWARE (verified pin map — do NOT change):
//   MCU:        ESP32 DevKit V1
//   SERVO:      MG90/MG90S         signal → GPIO 13
//   OLED:       1.3" SH1106 I2C    SDA 21, SCL 22, addr 0x3C
//   WS2812B:    12-LED ring        DI → GPIO 18
//   INMP441:    I2S mic            SCK 26, WS 25, SD 33
//   MAX98357A:  I2S amp            BCLK 27, LRC 14, DIN 19
//   SPEAKER:    4 Ω / 3 W         connected to MAX98357A
//
// Required libraries (see firmware header for install instructions):
//   Provided by ESP32 Arduino core:
//     WiFi, Wire, driver/i2s.h
//   Install via Arduino Library Manager:
//     WebSockets by Markus Sattler   (v2.4+)
//     ArduinoJson by Benoît Blanchon (v6+)
//     Adafruit SH110X + Adafruit GFX (for SH1106 OLED)
//     Adafruit NeoPixel              (for WS2812 LED ring)
//     ESP32Servo                     (for MG90 servo)
//
// Board support:
//   ESP32 Arduino core 2.x (e.g. 2.0.14)
//   Board: "ESP32 Dev Module"
// ============================================================

#ifndef CONFIG_H
#define CONFIG_H

// ---- Wi-Fi -----------------------------------------------------------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ---- Backend WebSocket server ----------------------------------------
// The ESP32 connects OUT to the backend.
#define WS_HOST         "192.168.1.100"   // backend machine LAN IP
#define WS_PORT         8000
#define WS_PATH         "/ws/robot"

// ---- Reconnect interval ----------------------------------------------
#define RECONNECT_MS    5000   // ms between reconnect attempts

// ---- Servo (MG90 signal wire, external 5 V rail) --------------------
#define SERVO_PIN       13

// ---- OLED (SH1106 I2C, 128×64) --------------------------------------
#define OLED_SDA_PIN    21
#define OLED_SCL_PIN    22
#define OLED_ADDR       0x3C

// ---- WS2812B LED ring (12 SMD RGB, DI) ------------------------------
#define WS2812_PIN      18
#define WS2812_COUNT    12

// ---- INMP441 MEMS microphone (I2S input → I2S_NUM_1) ----------------
#define INMP441_SCK     26     // bit clock (BCLK / SCK)
#define INMP441_WS      25     // word select  (LRCK / WS)
#define INMP441_SD      33     // serial data   (DOUT / SD)
// L/R pin → GND  (selects left channel)

// ---- MAX98357A I2S amplifier (I2S output → I2S_NUM_0) ---------------
#define MAX98357_BCLK   27     // bit clock
#define MAX98357_LRC    14     // left/right clock
#define MAX98357_DIN    19     // serial data in
// SD (shutdown) → 3.3 V (always enabled, no GPIO control)

// ---- Servo safety limits (degrees) -----------------------------------
#define SERVO_MIN       50
#define SERVO_MAX       130
#define SERVO_NEUTRAL   90

// ---- Audio sample rate -----------------------------------------------
#define AUDIO_SAMPLE_RATE  16000

// ---- Test mode (set to 1 to run hardware tests, 0 for normal) --------
#define TEST_MODE       0

#endif // CONFIG_H
