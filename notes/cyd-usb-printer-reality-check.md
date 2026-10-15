# Can the CYD be a "real" USB printer? (Reality check)

**Short answer: not the common CYD. v1 uses USB serial and Wi-Fi. A true USB printer
is a v2 hardware change.**

## Why

The common Cheap Yellow Display (ESP32-2432S028R) uses the **original ESP32**
(ESP32-D0WD family). That chip has **no USB peripheral at all**. The USB port goes to
a **USB-to-UART bridge chip**, usually a **CH340**, which the host sees as a serial port:

- Linux: `/dev/ttyUSB0` (driver `ch341`). `lsusb` shows `1a86:7523 QinHeng Electronics CH340 serial converter`.
- It can **only** ever be a serial port. The ESP32 firmware can't change what the
  CH340 tells the host.

To appear as a **USB Printer Class device** (interface class `0x07`, which CUPS and
Windows auto-detect as "a printer"), the microcontroller itself must be the USB device.
That needs a chip with native USB device/OTG:

| Chip | Native USB device? | Printer class possible? |
|---|---|---|
| ESP32 (CYD) | ❌ no | ❌ (only via the CH340 serial bridge) |
| ESP32-S2 | ✅ USB OTG (full speed) | ✅ via TinyUSB |
| ESP32-S3 | ✅ USB OTG + USB-Serial/JTAG | ✅ via TinyUSB |
| ESP32-C3 / C6 | ⚠️ USB-Serial/JTAG only (fixed function) | ❌ |
| RP2040 / RP2350 | ✅ | ✅ via TinyUSB |

## What the CYD can do (v1)

1. **Wi-Fi polling** (main path): the CYD polls the spooler's HTTP API.
2. **USB serial**: the host writes `@LE_SPOOLER` envelopes to `/dev/ttyUSB0`. The CYD
   prints them and replies `@ACK`. It isn't discoverable, but it works with no network.
   (`cli/leprint.py --serial /dev/ttyUSB0 …`)
3. **Let the Linux box play "the printer"**: a CUPS raw queue whose backend forwards to
   the spooler. Any app's Print dialog can then target "LE SPOOLER", and the queue can
   be advertised to the LAN. The CYD is its print engine. (Roadmap v1.1.)

## v2 path: a true USB printer

- Hardware: an ESP32-S3 board with a screen, wired so the S3's native USB D+/D−
  (GPIO19/20) reach the connector, rather than a CH340.
- Firmware: the TinyUSB device stack with a printer-class interface (bulk OUT, optional
  bulk IN), a `GET_DEVICE_ID` answer (IEEE-1284 ID string), and the same renderer.
- Host: Linux sees `/dev/usb/lp0` (usblp). CUPS offers "Generic Text-Only". Windows
  offers "Generic / Text Only".

Sources: Espressif ESP32 / ESP32-S2 / ESP32-S3 datasheets (USB OTG availability) and
the USB Device Class Definition for Printing Devices 1.1. Check against the specific
board's schematic before buying.
