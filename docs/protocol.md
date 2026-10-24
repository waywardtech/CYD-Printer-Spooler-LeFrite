# LE SPOOLER protocol

Version 0.2. Everything is UTF-8 text. The spooler listens on **TCP 8765** by default
(`http://<spooler-ip>:8765`). There is no authentication: it's a LAN toy. Don't
port-forward it to the internet.

## 1. Job format: the `@LE_SPOOLER` envelope

A job is plain text with an optional header block. This is the human-readable form.
You can `POST` it as `text/plain`. `GET /next?format=text` returns it, the CYD
writes it to microSD, and it goes over USB serial unchanged.

```
@LE_SPOOLER_BEGIN
TITLE: Hello World
SOURCE: La Frite
MODE: PRINT
STYLE: GREENBAR
SPEED: DAISYWHEEL
CHAOS: LOW

HELLO WORLD

THIS IS YOUR FIRST DOCUMENT.
@LE_SPOOLER_END
```

Rules:

* The first non-blank line is `@LE_SPOOLER_BEGIN`.
* Header lines are `KEY: value`. KEY is uppercase letters and underscores. Readers
  treat keys case-insensitively. Unknown keys are kept and ignored.
* The **first blank line** ends the header. Everything after it, up to the last
  `@LE_SPOOLER_END` line, is the document body, including control characters.
* A `text/plain` POST *without* `@LE_SPOOLER_BEGIN` is a bare body with default headers.

| Header   | Values                                        | Default      | Meaning |
|----------|-----------------------------------------------|--------------|---------|
| `ID`     | `YYYYMMDD-HHMMSS-mmm`                         | assigned     | Set by the spooler (UTC). Sort order = queue order. |
| `TITLE`  | text (max 120)                                | `Untitled`   | Shown in the CYD document browser. |
| `SOURCE` | text                                          | sender IP    | Who sent it. |
| `MODE`   | `PRINT`, `ANIMATION`                          | `PRINT`      | `ANIMATION`: the body is an `.anim` file, played on the fixed screen. |
| `STYLE`  | `GREENBAR`, `BLUEBAR`, `PLAIN`, `THERMAL`     | `GREENBAR`   | Paper look. |
| `SPEED`  | `TELETYPE` (10 cps), `DAISYWHEEL` (30), `FAST`, `INSTANT` | `DAISYWHEEL` | Hint for the typing rate. |
| `CHAOS`  | `OFF`, `LOW`, `MEDIUM`, `HIGH`                | `OFF`        | Hint for random printer events. The device's own setting wins. |

### Control characters in the body

| Char | Name | Printer behaviour |
|------|------|-------------------|
| `\b` 0x08 | Backspace | Head moves one column left (not below 0). The next strike **overstrikes**. |
| `\r` 0x0D | Carriage return | Head returns to column 0 on the same line (overprint). |
| `\n` 0x0A | Line feed | New line. Treated as CR+LF, like a Unix tty with ONLCR (`--lf-only` in the preview for purists). |
| `\f` 0x0C | Form feed | Advance to the top of the next form, past the perforation. |
| `\a` 0x07 | Bell | Ding. Sound on the CYD, a margin note in the previews. |
| `\t` 0x09 | Tab | Next multiple of 8 columns. |
| other C0 | | Ignored. |

Printing past the right margin wraps to a new line.

### Overstrike rules

When a glyph is struck on a cell that already has ink:

| First + second (either order) | Result |
|---|---|
| letter + `'` `` ` `` `^` `~` `,` `"` | accented letter via Unicode NFC: `é à ô ñ ç ü` |
| same glyph twice | **bold** |
| glyph + `_` | underlined |
| letter + `-` | struck through |
| `O`+`/`, `c`+`\|`, `=`+`/`, `L`+`-`, `Y`+`=`, `:`+`-`, `+`+`_` | `Ø ¢ ≠ £ ¥ ÷ ±` |
| anything else | the later glyph, heavier ("ink blob") |
| space over anything | no change. A space never erases. |

On the CYD, overstrike is literal: both glyphs are drawn into the same cell.

## 2. HTTP API

JSON responses carry `"ok": true|false`. Errors look like `{"ok": false, "error": "…"}`.
CORS is open (`Access-Control-Allow-Origin: *`, plus `Access-Control-Allow-Private-Network`)
so browser tools such as TurboWarp can call it.

| Method | Path | Result |
|---|---|---|
| GET | `/` | The web UI (compose, queue, paper preview, animation screen). |
| GET | `/health` | `{ok, service, version, time, queued, printed, animations}` |
| POST | `/print` | Queue a job. **201** `{ok, id, job:{…metadata, chars}}`. 400 bad/empty, 413 over 1 MiB. |
| GET | `/jobs` | `{ok, count, jobs:[{id, title, source, mode, style, created, status, chars, printed?}]}`, oldest first |
| GET | `/jobs/<id>` | `{ok, job:{…, body, status}}`. `?format=text` returns the envelope, with header `X-Job-Id`. |
| GET | `/next` | Oldest `queued` job: `{ok, job}`. **204 No Content** when the queue is empty. `?format=text` returns the envelope. |
| POST | `/ack/<id>` | Mark printed and move it to `spool/printed/`. Idempotent: a repeat ack returns `already_acked: true`. 404 if unknown. |
| POST | `/jobs/<id>/hold` | Park a queued job: `/next` skips it. 409 if it isn't queued. |
| POST | `/jobs/<id>/release` | Put a held job back in the queue (keeps its place: ids sort by age). |
| POST | `/jobs/<id>/printed` | Mark printed by hand (same as `/ack`). |
| POST | `/jobs/<id>/reprint` | Queue a fresh copy of any job. **201** `{id, copy_of}` |
| POST · DELETE | `/jobs/<id>/delete` · `/jobs/<id>` | Soft-delete: moved to `spool/deleted/`, gone from `/jobs`. |
| GET | `/animations` | `{ok, count, animations:[{id, name, fps, loop, frame_count, width, height}]}` |
| GET | `/animations/<name>` | `{ok, animation:{name, fps, loop, frames:[[line,…],…], …}}`. `?format=raw` returns the `.anim` file. |

### POST /print bodies

JSON (`Content-Type: application/json`):

```json
{"title": "Hello World", "source": "lafrite", "mode": "print",
 "style": "greenbar", "body": "HELLO WORLD\n"}
```

Only `body` is required (`text` is accepted as an alias). Control characters use
normal JSON escapes: `"cafe\b'"`.

Plain text (`Content-Type: text/plain`, or anything that isn't JSON): a bare body or a
full `@LE_SPOOLER` envelope.

```sh
curl -X POST --data-binary @letter.txt -H 'Content-Type: text/plain' http://<spooler-ip>:8765/print
```

### Printer presence
Every `GET /next` is a heartbeat. `/health` lists `printers: [{name, address, seconds_ago, online}]`,
where online means polled within 30 s. A client can name itself with `/next?printer=NAME`.
The web UI's virtual printer uses `Virtual printer (web)`. The CYD is detected by its ESP32 user agent.

### CYD polling loop

```
every POLL_INTERVAL_MS (default 3 s):
    GET /next
      204 -> idle
      200 -> print job character by character
             save to SD as /spool/<id>.txt (envelope format)
             POST /ack/<id>
```

Delivery is **at-least-once**. If the CYD reboots mid-print, the job is still `queued`
and gets printed again. That's the right failure mode for a printer.

## 3. Animation format (`.anim`)

```
# name: Robot Wave
# fps: 4
---
 [o_o]/
 /| |
 / \
---
 [o_o]-
 /| |
 / \
```

* `# key: value` lines **before the first `---`** are metadata. Known keys: `name`,
  `fps` (0.1–60, default 4), `loop` (`yes`/`no`, default yes), `note`. Other keys are kept.
* A line that is exactly `---` starts a new frame. Frame lines are drawn verbatim,
  top-left aligned. Leading spaces and leading blank lines matter. Trailing blank
  lines are trimmed.
* Lines inside frames may start with `#`. Only the header is parsed for metadata.
* Keep art in 7-bit ASCII and ≤ 32×12 for the CYD's small font.
* The player shows frames on a fixed screen (no scrolling paper). This is separate
  from print mode.

## 4. USB serial

Same envelope over the CYD's USB-serial port (CH340, **115200 8N1**):

```
CYD  -> host : @LE_SPOOLER_READY                 (after boot)
host -> CYD  : @LE_SPOOLER_BEGIN … @LE_SPOOLER_END\n
CYD  -> host : @ACK <id>                          (after printing)
             | @NAK <reason>                      (e.g. too-large)
host -> CYD  : 0x18 (Ctrl-X)                      cancels the job being printed
```

Opening the port toggles DTR/RTS, which resets most ESP32 boards. Wait for
`@LE_SPOOLER_READY` before sending. With no Wi-Fi configured this takes ~18 s, because the CYD tries Wi-Fi first. `cli/leprint.py --serial` waits for it.
