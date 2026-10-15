#!/usr/bin/env bash
# Compile (and optionally flash) the LE SPOOLER CYD firmware with arduino-cli.
#
#   firmware/build.sh                         # compile only
#   UPLOAD_PORT=/dev/ttyUSB0 firmware/build.sh  # compile + flash
#   PANEL=ST7789_DRIVER firmware/build.sh     # 2-USB CYD revision
#
# TFT_eSPI normally wants you to edit User_Setup.h inside the library folder.
# Instead we pass the CYD's display config as compiler flags, so the library
# stays stock and the build is reproducible. Pins are the commonly published
# ESP32-2432S028R values: VERIFY THEM against your board (notes/hardware-notes.md).
#
# Prerequisites (once):
#   arduino-cli config init
#   arduino-cli config add board_manager.additional_urls \
#       https://espressif.github.io/arduino-esp32/package_esp32_index.json
#   arduino-cli core update-index
#   arduino-cli core install esp32:esp32@2.0.17
#   arduino-cli lib install TFT_eSPI ArduinoJson XPT2046_Touchscreen
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKETCH="$HERE/CYD_LE_SPOOLER_Arduino"
FQBN="${FQBN:-esp32:esp32:esp32}"
PANEL="${PANEL:-ILI9341_2_DRIVER}"

TFT_FLAGS=(
  -DUSER_SETUP_LOADED=1
  "-D${PANEL}=1"
  -DTFT_WIDTH=240 -DTFT_HEIGHT=320
  -DTFT_MISO=12 -DTFT_MOSI=13 -DTFT_SCLK=14
  -DTFT_CS=15 -DTFT_DC=2 -DTFT_RST=-1
  -DTFT_BL=21 -DTFT_BACKLIGHT_ON=HIGH
  -DUSE_HSPI_PORT=1
  -DLOAD_GLCD=1 -DLOAD_FONT2=1 -DLOAD_FONT4=1
  -DSPI_FREQUENCY=55000000 -DSPI_READ_FREQUENCY=20000000 -DSPI_TOUCH_FREQUENCY=2500000
)
if [[ "$PANEL" == "ST7789_DRIVER" ]]; then
  TFT_FLAGS+=(-DTFT_INVERSION_ON=1 -DTFT_RGB_ORDER=TFT_BGR)
fi
FLAGS="${TFT_FLAGS[*]} ${EXTRA_FLAGS:-}"

if [[ ! -f "$SKETCH/config.h" ]]; then
  echo "note: $SKETCH/config.h not found; building with config.example.h placeholders" >&2
fi

arduino-cli compile \
  --fqbn "$FQBN" \
  --build-property "compiler.cpp.extra_flags=$FLAGS" \
  --build-property "compiler.c.extra_flags=$FLAGS" \
  --warnings default \
  ${UPLOAD_PORT:+--upload --port "$UPLOAD_PORT"} \
  "$SKETCH"
