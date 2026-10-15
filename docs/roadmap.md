# LE SPOOLER roadmap

## v0.2: spooler, previews, firmware that compiles ✅
- [x] Stdlib-only HTTP spooler on :8765 (`/health /print /jobs /next /ack /animations`)
- [x] Web UI at `/`: compose, queue, animated paper preview, animation screen
- [x] `leprint.py` CLI: args, stdin, files, `--escapes`, and `--serial` straight to a CYD
- [x] Terminal preview: greenbar paper, sprocket holes, perforations, char-by-char
      typing, BS/CR/LF/FF/BEL/TAB, accent overstrike, optional chaos and ribbon wear
- [x] 10 ASCII animations, a deterministic generator, and a fixed-screen player
- [x] Protocol doc; tests; CI that runs the tests and compiles the firmware
- [x] `install.sh` with an optional systemd **user** service

## v1.0: CYD on the bench
- [ ] Confirm the exact CYD variant (ILI9341 vs ST7789, 1 vs 2 USB ports) and pins
- [ ] Free `/dev/ttyUSB0` from brltty if needed (see README › Troubleshooting)
- [ ] Flash. Tune colours, touch calibration, speaker volume.
- [ ] Smooth scrolling: TFT hardware scroll or sprite instead of a full redraw per line
- [ ] Device-side settings menu: chaos level, speed, volume, paper style
- [ ] Animations stored on SD (`/anim`) and an attract mode when idle

## v1.1: Linux integration
- [ ] CUPS "raw" queue whose backend is `leprint.py`, so any app can File → Print
- [ ] Advertise that queue over IPP/mDNS, so other computers discover "LE SPOOLER"
- [ ] `leprint --serial` auto-detects the CYD's port

## v1.2: Fun
- [ ] TurboWarp extension that POSTs to `/print`
- [ ] Banner mode: big sideways letters, like 1983
- [ ] Physical "form feed" button. Ribbon-change ritual (touch and hold).

## v2: a real USB printer (new hardware)
- [ ] ESP32-S3 (or S2/RP2040) board with a display and native USB
- [ ] TinyUSB **USB Printer Class** (interface class 0x07) with an IEEE-1284 device ID
      such as `MFG:LE;MDL:SPOOLER;CMD:TXT;`
- [ ] The host sees a real printer. Linux: `/dev/usb/lp0` plus CUPS "Generic Text-Only".
- [ ] Keep Wi-Fi spooling as a second input

See `../notes/cyd-usb-printer-reality-check.md` for why v2 needs different hardware.
