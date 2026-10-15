# LE SPOOLER firmware

Target: **Cheap Yellow Display, ESP32-2432S028R** (original ESP32, CH340 USB-serial,
2.8" 320×240 ILI9341 or ST7789, XPT2046 resistive touch, microSD, speaker header).

CI compiles the sketch for both panel types on every push. It has **not yet been run
on real hardware**, so treat the first flash as a bring-up session.

## Build & flash with arduino-cli

```sh
# one-time toolchain setup (~500 MB, on any Linux/macOS/Windows machine)
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32@2.0.17
arduino-cli lib install TFT_eSPI ArduinoJson XPT2046_Touchscreen

# configure
cp firmware/CYD_LE_SPOOLER_Arduino/config.example.h firmware/CYD_LE_SPOOLER_Arduino/config.h
$EDITOR firmware/CYD_LE_SPOOLER_Arduino/config.h        # Wi-Fi, spooler URL

# compile, then compile + flash
firmware/build.sh
UPLOAD_PORT=/dev/ttyUSB0 firmware/build.sh
PANEL=ST7789_DRIVER UPLOAD_PORT=/dev/ttyUSB0 firmware/build.sh   # 2-USB CYD
```

`build.sh` passes TFT_eSPI's display config (driver and pins) as compiler flags, so
you **don't** have to edit `User_Setup.h` inside the library. If you prefer the Arduino
IDE, copy those `-D` values from `build.sh` into TFT_eSPI's `User_Setup.h` instead.

Some CYDs need **BOOT** held while flashing starts.

## Inputs
- **Wi-Fi**: polls `GET /next`, prints, saves to SD, then `POST /ack/<id>`.
- **USB serial**: send an `@LE_SPOOLER` envelope at 115200 baud. The CYD replies `@ACK <id>`.
  `python3 cli/leprint.py --serial /dev/ttyUSB0 "HELLO"`
- **Touch**: top third opens the saved-document browser, middle plays an animation,
  bottom does a form feed.

## Function map
| Function | What it does |
|---|---|
| `drawPaper()` | Redraws visible paper rows plus the platen (after each line feed) |
| `drawSprocketHoles(row, line)` | Tractor strips; one hole per greenbar band |
| `drawPerforations(row, line)` | Vertical perfs beside the strips; horizontal fold at top of form |
| `printCharacter(c)` | One byte through the mechanism: BS/CR/LF/FF/BEL/TAB or a strike |
| `printTextSlowly(text)` | A whole document at `CHARS_PER_SECOND` with jitter |
| `playClick()` / `playCarriageReturn()` / … | DAC noise-burst "mechanism" sounds on GPIO26 |
| `saveJobToSD(id,title,body)` | Writes `/spool/<id>.txt` in envelope format |
| `browseDocuments()` | Lists saved jobs; tap to replay |
| `playAsciiAnimation(text)` | Plays a `.anim` file on a fixed screen |

## Open questions for bring-up
- Panel: ILI9341 or ST7789? Rotation and colour inversion?
- Touch calibration constants (`TOUCH_X_MIN` …) in `config.h`
- Is a speaker connected to the SPEAK header?
- Full redraw per line feed may flicker. Hardware scroll is the next step.

USB printer class (plug-and-play "printer") needs different hardware: see
[`../notes/cyd-usb-printer-reality-check.md`](../notes/cyd-usb-printer-reality-check.md).
