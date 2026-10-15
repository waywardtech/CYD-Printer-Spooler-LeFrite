# Hardware notes

## Spooler host: Libre Computer La Frite (AML-S805X-AC)
- Amlogic S805X, 4× Cortex-A53, 512 MB or 1 GB RAM. Developed on Armbian 26.8 (Debian 13
  "trixie"), Python 3.13. Any Linux with Python ≥ 3.9 works (Raspberry Pi, a laptop…).
- The spooler is tiny: stdlib Python, ~15 MB RSS. It's fine to leave running.
- It binds `0.0.0.0:8765`. If you run a firewall, allow **8765/tcp from your LAN only**, e.g.
  `sudo nft add rule inet filter input ip saddr 192.168.1.0/24 tcp dport 8765 accept`.
  The installer doesn't touch firewall settings.

## Cheap Yellow Display (ESP32-2432S028R): verify on your board

> ⚠️ The pins below are the ones **commonly published** for the ESP32-2432S028R
> ("CYD", one micro-USB port, ILI9341). There are variants: the two-USB (micro +
> USB-C) revision often uses an **ST7789** panel and may need inverted colours
> (`PANEL=ST7789_DRIVER firmware/build.sh`), and clones differ. **Check against your
> board / seller schematic.**

| Function | Bus | Pins (commonly reported) |
|---|---|---|
| TFT 2.8" 320×240 ILI9341 | HSPI | MOSI 13, MISO 12, SCK 14, CS 15, DC 2, RST −1 (tied to EN), backlight **21** |
| Touch XPT2046 (resistive) | bit-banged | MOSI 32, MISO 39, CLK 25, CS 33, IRQ 36 |
| microSD | VSPI | MOSI 23, MISO 19, SCK 18, CS 5 |
| Speaker (via SC8002B amp, "SPEAK" JST) | DAC2 | GPIO **26** |
| RGB LED (active LOW) | GPIO | R 4, G 16, B 17 |
| Light sensor (LDR) | ADC | GPIO 34 |
| USB | CH340 USB-UART | `/dev/ttyUSB0`, `1a86:7523` |

### Three SPI devices, two SPI hosts
The TFT takes HSPI and the SD card takes VSPI. The touch controller is read with a
~20-line bit-banged driver in the sketch, so all three work at once without bus juggling.

### Speaker reality
Plug a small 8 Ω speaker into the 2-pin "SPEAK" JST connector. Many boards ship
without one. The sounds are DAC noise bursts (hammer, ratchet, carriage whirr) and a
decaying square-wave ding.

### Paper geometry on a 240×320 (portrait) screen
Real 9½" fanfold has ½" tractor strips on each side, holes every ½", 6 lines/inch, and
½" (3-line) greenbar bands. Scaled to the CYD:

| | Pixels (portrait, 240 wide) |
|---|---|
| Tractor strip each side | 13 px, holes ⌀ 6 px |
| Perforation lines | x = 13 and x = 226 (dotted) |
| Printable width | 34 columns with the 6×8 font |
| Line height | 10 px (8 px glyph + 2 px leading) |
| Greenbar band | 3 lines = 30 px, one sprocket hole per band |
| Form length | 66 lines (11" at 6 LPI). Horizontal perforation at each form boundary |
| Platen | bottom 20 px, with an orange carriage marker under the print head |

## Checklist when the CYD is plugged into Linux
1. `lsusb` shows `1a86:7523` (CH340) or `10c4:ea60` (CP210x).
2. `ls -l /dev/ttyUSB*` exists. If it doesn't but the chip shows in `lsusb`, it's
   probably **brltty** (see README › Troubleshooting).
3. Your user is in the `dialout` group (`id -nG`).
4. Compile with `firmware/build.sh`. Try ILI9341 first. If colours look inverted or the
   picture is garbled, rebuild with `PANEL=ST7789_DRIVER`.
