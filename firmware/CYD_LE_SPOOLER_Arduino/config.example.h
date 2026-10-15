// ============================================================================
//  LE SPOOLER - CYD firmware configuration
//
//  Copy this file to  spooler_config.h  (same folder) and edit it.
//  spooler_config.h is git-ignored because it holds your Wi-Fi password.
//
//  WARNING: PIN VALUES ARE UNVERIFIED. They are the values commonly published for
//      the ESP32-2432S028R "Cheap Yellow Display" (1x micro-USB, ILI9341).
//      Check them against YOUR board before flashing. See ../../notes/hardware-notes.md
// ============================================================================
#pragma once

// ---------------------------------------------------------------- Network --
#define WIFI_SSID            "your-wifi-name"
#define WIFI_PASSWORD        "your-wifi-password"
// The Linux box running server/le_spooler_server.py (no trailing slash):
#define LA_FRITE_SERVER_URL  "http://192.168.1.50:8765"
#define POLL_INTERVAL_MS     3000      // how often to ask GET /next
#define HTTP_TIMEOUT_MS      5000
#define MAX_JOB_BYTES        32768     // refuse jobs bigger than this (RAM is ~300 KB)

// ------------------------------------------------------------ Display -----
// The TFT pins themselves are NOT set here. TFT_eSPI reads them from compiler
// flags (see ../build.sh, which passes the usual CYD values) or from its own
// User_Setup.h. For the CYD those are usually:
//   ILI9341_2_DRIVER (or ST7789_DRIVER on the 2-USB revision), 240x320,
//   MISO 12, MOSI 13, SCLK 14, CS 15, DC 2, RST -1, BL 21, USE_HSPI_PORT
#define TFT_BACKLIGHT_PIN    21        // UNVERIFIED
#define SCREEN_ROTATION      0         // 0 = portrait 240x320 (paper looks best tall)

// -------------------------------------------------------------- Touch -----
// XPT2046 on its own pins. The ESP32 has only two free SPI hosts: the TFT uses
// HSPI and the SD card uses VSPI. So by default the sketch reads touch with a
// tiny built-in bit-banged driver. Set TOUCH_USE_LIBRARY 1 to use
// XPT2046_Touchscreen on VSPI instead (SD and touch then can't share the bus
// at the same time; read the notes in the .ino first).
#define TOUCH_USE_LIBRARY    0
#define TOUCH_CS             33        // UNVERIFIED
#define TOUCH_IRQ            36        // UNVERIFIED (input-only pin, LOW when touched)
#define TOUCH_MOSI           32        // UNVERIFIED
#define TOUCH_MISO           39        // UNVERIFIED (input-only pin)
#define TOUCH_CLK            25        // UNVERIFIED
// Raw ADC range -> screen mapping. Calibrate by printing raw values at corners.
#define TOUCH_X_MIN          200
#define TOUCH_X_MAX          3700
#define TOUCH_Y_MIN          240
#define TOUCH_Y_MAX          3800

// ------------------------------------------------------------- microSD ----
#define SD_ENABLED           1
#define SD_CS                5         // UNVERIFIED
#define SD_SCK               18        // UNVERIFIED
#define SD_MISO              19        // UNVERIFIED
#define SD_MOSI              23        // UNVERIFIED
#define SD_SPOOL_DIR         "/spool"  // saved jobs:        /spool/<id>.txt
#define SD_ANIM_DIR          "/anim"   // saved animations:  /anim/<name>.anim

// --------------------------------------------------------------- Sound ----
// A small 8-ohm speaker must be plugged into the "SPEAK" JST connector. The amp
// input is on GPIO26 (DAC2).
#define SOUND_ENABLED        1
#define SPEAKER_PIN          26        // UNVERIFIED
#define SOUND_VOLUME         60        // 0..127 DAC amplitude. Keep it modest.

// ------------------------------------------------------------ RGB LED -----
#define LED_R                4         // UNVERIFIED, active LOW
#define LED_G                16
#define LED_B                17

// ---------------------------------------------------- Printer behaviour ---
#define CHARS_PER_SECOND     30        // daisywheel ~30, teletype ~10
#define FORM_LINES           66        // 11" form at 6 LPI
// Random printer events: 0 = off (default), 1 = low, 2 = medium, 3 = high.
// Off by default so nothing surprising happens unless you ask for it.
#define CHAOS_LEVEL          0
#define RIBBON_START_PCT     100
#define USB_SERIAL_JOBS      1         // also accept @LE_SPOOLER envelopes over USB serial
#define SERIAL_BAUD          115200
