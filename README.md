<div align="center">

```
 ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌
     ▒┊                                                 ┊▒
   ● ▒┊        L E   S P O O L E R                      ┊▒ ●
     ▒┊        ===================                      ┊▒
      ┊                                                 ┊
   ●  ┊   a fanfold daisywheel printer, reincarnated    ┊  ●
      ┊   on a $15 Cheap Yellow Display_                ┊
     ▒┊                                                 ┊▒
 ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌
```

**Print to a tiny screen like it's 1979.**

[![CI](https://github.com/waywardtech/CYD-Printer-Spooler-LeFrite/actions/workflows/ci.yml/badge.svg)](https://github.com/waywardtech/CYD-Printer-Spooler-LeFrite/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

---

**LE SPOOLER** turns a Linux box and an ESP32 "Cheap Yellow Display" into a pretend
fanfold printer. Send it text from the command line, a browser, `curl`, or a Scratch
project. It types the document out **one character at a time** onto green-and-cream
greenbar paper, with sprocket holes, perforations, and the clack of the hammer, the
whirr of the carriage return and the *ding* of the bell.

Accents are made the old way, by **overstrike**: `e`, backspace, `'` becomes **é**.
If you ask, it will also jam, run out of paper, and wear out its ribbon.

## ✨ What it does

| | |
|---|---|
| 🖨️ **Print spooler** | A tiny HTTP queue (`POST /print`, `GET /next`, `POST /ack`). Pure Python standard library, nothing to install. |
| 🌐 **Web UI** | Open `http://<your-box>:8765/` to type a document and see any job typed onto animated greenbar paper. **Manage the queue**: view, hold/release, mark printed, reprint, delete. It also shows whether a printer is actually collecting jobs. |
| 🖼️ **Photos → ASCII art** | Print a photo from any app and it comes out as dithered ASCII art sized for the paper. Or use the web UI's *Photo → ASCII art* panel to tune width, contrast, dithering and invert before printing. |
| 🖨️ **Virtual printer** | No CYD yet? Tick *Virtual printer* and the browser tab collects queued jobs, types them out, and marks them printed. |
| 🖋️ **Looks like real type** | Typewriter face, uneven hammer pressure, slightly translucent ribbon ink, and true **overtype**: every strike adds ink, so `e` + backspace + `'` really layers an accent onto the letter. |
| 🖥️ **A real printer for your desktop** | A CUPS backend makes "LE SPOOLER" appear in every app's Print dialog. It can also be shared over **Bonjour** so other computers on the LAN discover it. PDFs are converted to text, and page breaks become form feeds. |
| 💻 **Terminal preview** | The same printer in your terminal: colour greenbar, sprocket holes, char-by-char typing. |
| ⌫ **Real printer control codes** | Backspace/overstrike, carriage return, line feed, form feed, bell, tab. |
| é **Overstrike accents & tricks** | `é à ô ñ ç ü`, **bold** by double strike, underline with `_`, strike-through with `-`, `Ø ¢ ≠ £`. |
| 🎲 **Printer chaos** *(optional, off by default)* | Paper jams, paper out, misstrikes, skewed paper, a ribbon that fades. |
| 🎞️ **ASCII animations** | 10 classics on a fixed "screen": spinner, bouncing ball, waving robot, rocket, train, fish tank, matrix rain, starfield, radar, self-test page. **Pause, step frame by frame, and print any frame** onto the paper. |
| 📟 **CYD firmware** | ESP32 sketch: draws the paper, types jobs, plays mechanism sounds, saves to microSD, accepts jobs over Wi-Fi *or* USB serial. Compiled in CI. |

## 🧭 How it fits together

```
 your stuff                              Linux box (La Frite, Pi, laptop…)            Cheap Yellow Display
 ──────────                              ─────────────────────────────────            ────────────────────
 leprint.py ─────┐                     ┌──────────────────────────────┐   GET /next   ┌───────────────────┐
 curl ───────────┤   POST /print       │  LE SPOOLER server  :8765    │ ◄──────────── │  ESP32 firmware   │
 web UI ─────────┼───────────────────► │  spool/incoming → printed    │ ── job ─────► │  types it on      │
 TurboWarp ──────┘                     │  web UI · animations · API   │ ◄── POST /ack │  greenbar paper,  │
                                       └──────────────────────────────┘               │  saves to microSD │
 leprint.py --serial /dev/ttyUSB0 ─────────────── USB serial (no network needed) ───► └───────────────────┘
```

## 📦 Prerequisites

**Spooler (required)**
- Any Linux machine with **Python 3.9+**. Developed on a Libre Computer *La Frite*
  running Armbian (Debian 13), and works the same on a Raspberry Pi or a laptop.
  macOS works too.
- That's it: no pip, no venv, no frameworks.

**Display (optional, for the real thing)**
- A **Cheap Yellow Display**, *ESP32-2432S028R* (about $15). ILI9341 and ST7789 variants are both supported.
- A microSD card (to save printed documents) and a small **8 Ω speaker** on the SPEAK
  header (for the clack and ding). Both are optional.
- [`arduino-cli`](https://arduino.github.io/arduino-cli/) on any computer to build and
  flash the firmware.

## 🚀 Install

```bash
git clone https://github.com/waywardtech/CYD-Printer-Spooler-LeFrite.git ~/le-spooler
cd ~/le-spooler
./install.sh                 # checks Python, makes spool folders, runs the self-test
```

The installer never uses `sudo` and never changes system settings.
To keep the spooler running in the background as a **user** service:

```bash
./install.sh --service       # systemctl --user status le-spooler
```

## 🖨️ Use it

**Start the spooler**

```bash
python3 server/le_spooler_server.py          # listens on 0.0.0.0:8765
```

**Print something**

```bash
python3 cli/leprint.py --title "Hello Ellis" "HELLO ELLIS"
echo "Printed from a pipe" | python3 cli/leprint.py --title "Pipe"
python3 cli/leprint.py --file ~/letter.txt
python3 cli/leprint.py -e "Accents the old way: cafe\b' garc\b,on man\b~ana\a"
curl -X POST -H 'Content-Type: text/plain' --data 'HELLO' http://localhost:8765/print
```

**Watch it type**

```bash
python3 server/le_spooler_preview.py                     # next job in the queue
python3 server/le_spooler_preview.py --demo              # overstrike / CR / bell showcase
python3 server/le_spooler_preview.py --speed teletype --chaos low --ribbon 40
python3 server/le_spooler_preview.py --anim fish_tank --loops 0     # q to stop
```

Or open **`http://<your-box>:8765/`** in a browser.

**Animation controls.** These work in the terminal player, in the web UI (click the
screen first), and on the CYD by touch:

| Terminal / web | CYD touch | Action |
|---|---|---|
| <kbd>space</kbd> | tap while playing / middle | pause · resume |
| <kbd>←</kbd> <kbd>→</kbd> | left / right third | step one frame back / forward |
| <kbd>p</kbd> (web: <kbd>P</kbd> or 🖨 button) | top-right **PRINT** | print the current frame onto the paper |
| <kbd>q</kbd> | top-left **EXIT** | back to the paper |

Or straight from the shell:

```bash
python3 server/le_spooler_preview.py --anim rocket_launch --frame 7          # show frame 7
python3 server/le_spooler_preview.py --anim rocket_launch --print-frame 7    # print it
```

<details>
<summary><b>Sample terminal output</b></summary>

```
╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌  LE SPOOLER ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌ ╌
    ▒┊LE SPOOLER SELF-DEMO                                                  ┊▒
  ● ▒┊====================                                                  ┊▒ ●
    ▒┊Accents by overstrike: café ; voilà ; hôtel ; mañana ; garçon ; über  ┊▒
     ┊Bold by double strike: BOLD      Underline: UNDER                     ┊
  ●  ┊Typewriter tricks: Ø  ¢  ≠  £                                         ┊  ●
     ┊T̶O̶P̶-S̶E̶C̶R̶E̶T̶  <- struck out by a carriage-return overprint              ┊
    ▒┊Bell coming up...                                                     ┊▒      DING!
  ● ▒┊Tab:    one     two     three                                         ┊▒ ●
```
In a colour terminal the bands are real green and cream, and the text appears one
character at a time.
</details>

### Preview options

| Flag | Meaning |
|---|---|
| `--speed teletype\|daisywheel\|fast\|instant\|<cps>` | typing speed (default daisywheel, 30 cps) |
| `--chaos off\|low\|medium\|high` | random printer events (default **off**) |
| `--ribbon 0-100` | starting ink; faint text below ~35% |
| `--style greenbar\|bluebar\|plain\|thermal` | paper |
| `--width N` · `--page-lines N` | columns · lines per form (66 = 11″ at 6 LPI) |
| `--no-color` · `--ascii` · `--lf-only` | plain output · ASCII glyphs · strict line feeds |

## 🖥️ Add it as a printer (desktop apps + Bonjour)

LE SPOOLER ships a CUPS backend (`contrib/cups/lespooler`) and a small PPD. Once
they're installed, **"LE_SPOOLER"** shows up in the Print dialog of every desktop app
on the machine: Firefox, LibreOffice, text editors, anything. With `--share`, it's also
advertised over Bonjour (mDNS/DNS-SD), so Macs, Linux PCs and other machines on the
LAN discover it on their own.

```bash
sudo contrib/cups/install-cups-printer.sh            # this machine only
sudo contrib/cups/install-cups-printer.sh --share    # + discoverable on the LAN
contrib/cups/update-backend.sh                       # later, after git pull: update the backend (no sudo)
echo "HELLO FROM CUPS" | lp -d LE_SPOOLER            # test
lp -d LE_SPOOLER ~/Documents/letter.pdf
```

How it works: apps send PDF, PostScript or text. CUPS passes the file unfiltered to the
`lespooler` backend, which converts it to plain text (`pdftotext -layout`, or
ghostscript for PostScript), turns page breaks into form feeds, and posts the result to
the spooler. If the spooler is down, CUPS holds the job and retries.

What `--share` changes on your system: it installs `avahi-daemon` if it's missing, and
runs `cupsctl --share-printers`, so **CUPS listens on port 631 on your LAN**, not just
localhost. Undo it with `sudo cupsctl --no-share-printers` and
`sudo contrib/cups/install-cups-printer.sh --uninstall`.

| Client | How to add it |
|---|---|
| Linux (GNOME/KDE) | Shows up automatically (cups-browsed / driverless), or add it in Settings › Printers |
| macOS | System Settings › Printers & Scanners › **+**, pick "LE SPOOLER fanfold printer". If asked for a driver, choose **Generic PostScript Printer**. |
| Windows | Run `contrib\windows\install-windows-printer.ps1 -Server <your-box>` as administrator. It adds **LE SPOOLER** (IPP: Word, browsers, PDFs, photos) and **LE SPOOLER (plain text)** (Generic / Text Only: Notepad, console output). `contrib\windows\print-test-pages.ps1` prints a test page for every case and checks that each one reaches the printer. |

> **Pictures of pages and photos.** Driverless printing from Windows, macOS and phones,
> and Windows' PostScript driver, often send pages with no text layer. The backend
> renders each such page and decides for itself: if it finds words, it **reads them with
> OCR** (`tesseract-ocr`); if it's a picture, it **draws ASCII art**. A page it can't use
> prints a one-line notice instead, so the queue never stalls. You can choose how
> picture pages print (*Auto / Always text / Always ASCII art*) and the art width (34 =
> the CYD's paper, up to 80) in the Print dialog, or with
> `lp -o LeSpoolerMode=Picture -o AsciiWidth=64`. For the cleanest text from Windows, use
> the **Generic / Text Only** driver.
>
> **Where are my jobs?** CUPS hands each job to LE SPOOLER within a second and marks it
> *completed*. After that, the job lives in the LE SPOOLER queue, so view, hold,
> reprint or delete it in the web UI.

## 📟 Flash the Cheap Yellow Display

```bash
# one-time: arduino-cli + ESP32 core + libraries (see firmware/README.md)
cp firmware/CYD_LE_SPOOLER_Arduino/config.example.h firmware/CYD_LE_SPOOLER_Arduino/spooler_config.h
nano firmware/CYD_LE_SPOOLER_Arduino/spooler_config.h      # Wi-Fi + spooler URL
UPLOAD_PORT=/dev/ttyUSB0 firmware/build.sh         # PANEL=ST7789_DRIVER for 2-USB boards
```

Once it's flashed, the CYD polls the spooler every 3 seconds. You can also skip the
network entirely:

```bash
python3 cli/leprint.py --serial /dev/ttyUSB0 "HELLO OVER USB"
```

> ⚠️ The pin map is the commonly published one for the ESP32-2432S028R. CYD clones
> vary, so check [`notes/hardware-notes.md`](notes/hardware-notes.md) before flashing.

## 🐱 Scratch / TurboWarp

The API sends open CORS headers. From **TurboWarp Desktop** (or any page served over
plain `http://`), `POST` text to `http://<your-box>:8765/print` and it prints.
The public `https://turbowarp.org` site can't call a plain-`http` LAN address, because
browsers block mixed content, so use TurboWarp Desktop.

## 🔌 "Can it be a real USB printer?"

Not the ordinary CYD. Its original ESP32 has **no native USB**. The USB port goes
through a CH340 serial chip, so a computer only ever sees a serial port. LE SPOOLER v1
therefore uses **Wi-Fi and USB serial**. A plug-and-play **USB Printer Class** device
needs a board with native USB, such as an **ESP32-S3**, which is the
[v2 plan](docs/roadmap.md). The full explanation is in
[`notes/cyd-usb-printer-reality-check.md`](notes/cyd-usb-printer-reality-check.md).

## 🛠️ Troubleshooting

<details>
<summary><b>The CYD shows in <code>lsusb</code> (1a86:7523) but there's no <code>/dev/ttyUSB0</code></b></summary>

On Debian-based systems the **brltty** braille-display service often claims CH340
serial chips the moment they're plugged in, so the tty never appears. If you don't use a
braille display, you can stop that:

```bash
sudo systemctl stop brltty-udev.service brltty.service
sudo systemctl mask brltty-udev.service brltty.service
# then unplug and replug the CYD
```

Or remove it with `sudo apt remove brltty`. These are system changes, so the installer
never makes them for you.
</details>

<details>
<summary><b>Permission denied on /dev/ttyUSB0</b></summary>

`sudo usermod -aG dialout $USER`, then log out and back in.
</details>

<details>
<summary><b>Address already in use (port 8765)</b></summary>

Another spooler is already running: `ss -ltnp | grep 8765`. Stop it, or start this one
with `--port 8766`.
</details>

## 🗂️ Repository layout

```
server/le_spooler_server.py    HTTP spooler + web UI (server/web/index.html)
server/le_spooler_preview.py   terminal preview: paper, typing, overstrike, chaos, animations
cli/leprint.py                 send a job over HTTP or USB serial
assets/animations/*.anim       10 ASCII animations (tools/gen_animations.py rebuilds them)
firmware/                      CYD Arduino sketch + build.sh
docs/protocol.md               job envelope, control codes, HTTP API, .anim format, serial
docs/roadmap.md                what's next
notes/                         hardware notes, USB-printer reality check
tests/                         end-to-end tests (python3 -m unittest discover -s tests)
install.sh · contrib/          installer, systemd user unit
contrib/cups/                  CUPS backend, PPD, and printer installer (desktop + Bonjour)
```

## 🧪 Develop

```bash
python3 -m unittest discover -s tests -v       # server, CLI and preview tests
python3 tools/gen_animations.py --force        # regenerate the animations
firmware/build.sh                              # compile the firmware
```

CI runs the tests on Python 3.11–3.13 and compiles the firmware for both CYD panel types.

## 📜 License

[MIT](LICENSE) © 2026 Daniel Teixeira-Gomes. All code and ASCII art in this repository
are original. The firmware *uses*, but does not include, these libraries:
[TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) (FreeBSD-style license),
[ArduinoJson](https://arduinojson.org) (MIT),
[XPT2046_Touchscreen](https://github.com/PaulStoffregen/XPT2046_Touchscreen) (MIT-style), and the
[ESP32 Arduino core](https://github.com/espressif/arduino-esp32) (LGPL-2.1). All of
them are compatible with distributing this project under MIT.
