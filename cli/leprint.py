#!/usr/bin/env python3
"""
leprint - send a document to LE SPOOLER.

    python3 leprint.py "HELLO ELLIS"
    echo "HELLO ELLIS" | python3 leprint.py --title "Test"
    python3 leprint.py --file letter.txt --title "Letter"
    python3 leprint.py --escapes "caf" "e\\b' au lait"     # -> café (overstrike)

    # Skip the spooler and push straight to a CYD over USB serial (Linux/macOS):
    python3 leprint.py --serial /dev/ttyUSB0 "HELLO OVER USB"

Standard library only. Talks to POST /print on the spooler.
"""

import argparse
import codecs
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_SERVER = os.environ.get("LE_SPOOLER_URL", "http://127.0.0.1:8765")


def decode_escapes(s: str) -> str:
    r"""Turn literal \b \r \n \f \a \t \xNN into control characters."""
    return codecs.decode(s.encode("latin-1", "backslashreplace"), "unicode_escape")


def envelope(payload: dict) -> str:
    """The human-readable @LE_SPOOLER job format (docs/protocol.md)."""
    head = ["@LE_SPOOLER_BEGIN"]
    if payload.get("id"):
        head.append(f"ID: {payload['id']}")
    for key in ("title", "source", "mode", "style", "speed", "chaos"):
        value = payload[key] if key in ("title", "source") else str(payload[key]).upper()
        head.append(f"{key.upper()}: {value}")
    body = payload["body"] if payload["body"].endswith("\n") else payload["body"] + "\n"
    return "\n".join(head) + "\n\n" + body + "@LE_SPOOLER_END\n"


def send_serial(device: str, payload: dict, baud: int, boot_wait: float, timeout: float) -> int:
    """
    Write an envelope to a CYD's USB-serial port and wait for "@ACK <id>".
    Opening the port pulses DTR/RTS, which resets most ESP32 boards, so we
    wait `boot_wait` seconds for the firmware to come up before writing.
    """
    try:
        import termios
        import tty
    except ImportError:
        print("leprint: --serial needs a POSIX system (Linux/macOS)", file=sys.stderr)
        return 2
    speeds = {9600: termios.B9600, 57600: termios.B57600, 115200: termios.B115200}
    if baud not in speeds:
        print(f"leprint: unsupported baud {baud}; use one of {sorted(speeds)}", file=sys.stderr)
        return 2
    try:
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY)
    except OSError as e:
        print(f"leprint: cannot open {device}: {e.strerror}", file=sys.stderr)
        if e.errno == 13:
            print("         (is your user in the 'dialout' group?)", file=sys.stderr)
        return 2
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[4] = attrs[5] = speeds[baud]                   # ispeed / ospeed
        attrs[2] = (attrs[2] | termios.CLOCAL | termios.CREAD) & ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        # Opening the port may reset the CYD. Wait for its "@LE_SPOOLER_READY"
        # (it tries Wi-Fi first, ~18 s with no Wi-Fi), or boot_wait seconds at most.
        os.set_blocking(fd, False)
        seen, deadline = b"", time.time() + boot_wait
        while time.time() < deadline and b"@LE_SPOOLER_READY" not in seen:
            try:
                seen = (seen + os.read(fd, 512))[-4096:]
            except BlockingIOError:
                time.sleep(0.1)
        os.set_blocking(fd, True)
        termios.tcflush(fd, termios.TCIFLUSH)                # drop boot chatter
        payload = dict(payload, id=payload.get("id") or time.strftime("serial-%Y%m%d-%H%M%S"))
        os.write(fd, envelope(payload).encode("utf-8"))
        termios.tcdrain(fd)

        os.set_blocking(fd, False)
        buf, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf += chunk
                for line in buf.decode("utf-8", "replace").splitlines():
                    if line.startswith("@ACK"):
                        print(f"printed over serial: {line}")
                        return 0
                    if line.startswith("@NAK"):
                        print(f"leprint: printer refused job: {line}", file=sys.stderr)
                        return 1
            else:
                time.sleep(0.05)
        print(f"leprint: sent, but no @ACK within {timeout:.0f}s "
              "(the CYD keeps printing slowly; a long job may need --timeout)", file=sys.stderr)
        return 3
    finally:
        os.close(fd)


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a print job to LE SPOOLER.")
    ap.add_argument("text", nargs="*", help="text to print (joined with spaces); omit to read stdin")
    ap.add_argument("--title", default=None, help="job title (default: first words of text)")
    ap.add_argument("--source", default=socket.gethostname() or "cli", help="who sent it (default: hostname)")
    ap.add_argument("--style", default="greenbar", help="greenbar | bluebar | plain | thermal")
    ap.add_argument("--mode", default="print", help="print | animation")
    ap.add_argument("--speed", default="daisywheel", help="hint for the printer: teletype | daisywheel | fast")
    ap.add_argument("--chaos", default="off", help="hint for random printer events: off | low | medium | high")
    ap.add_argument("--file", "-f", help="read the document from a file")
    ap.add_argument("--escapes", "-e", action="store_true",
                    help=r"interpret \b \r \n \f \a escapes in the text (for overstrike tests)")
    ap.add_argument("--server", default=DEFAULT_SERVER,
                    help=f"spooler base URL (default {DEFAULT_SERVER}, or $LE_SPOOLER_URL)")
    ap.add_argument("--serial", metavar="DEVICE", help="bypass the spooler: write to a CYD on this serial port")
    ap.add_argument("--baud", type=int, default=115200, help="serial speed (default 115200)")
    ap.add_argument("--boot-wait", type=float, default=25,
                    help="max seconds to wait for the CYD's @LE_SPOOLER_READY after opening the port")
    ap.add_argument("--timeout", type=float, default=120, help="seconds to wait for @ACK over serial")
    ap.add_argument("--quiet", "-q", action="store_true", help="only print the job id")
    args = ap.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    elif args.text:
        body = " ".join(args.text)
        if not body.endswith("\n"):
            body += "\n"
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        ap.error("nothing to print: give text, --file, or pipe something in")

    if args.escapes:
        body = decode_escapes(body)
    if not body.strip("\n"):
        ap.error("nothing to print (empty document)")

    title = args.title or (body.strip().splitlines() or ["Untitled"])[0][:40]
    payload = {
        "title": title,
        "source": args.source,
        "mode": args.mode,
        "style": args.style,
        "speed": args.speed,
        "chaos": args.chaos,
        "body": body,
    }

    if args.serial:
        return send_serial(args.serial, payload, args.baud, args.boot_wait, args.timeout)

    req = urllib.request.Request(
        args.server.rstrip("/") + "/print",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", e.reason)
        except Exception:
            msg = e.reason
        print(f"leprint: server refused job ({e.code}): {msg}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"leprint: cannot reach LE SPOOLER at {args.server}: {e.reason}", file=sys.stderr)
        print("         is the server running?  python3 server/le_spooler_server.py", file=sys.stderr)
        return 2

    if args.quiet:
        print(result["id"])
    else:
        j = result["job"]
        print(f"queued job {result['id']}  \"{j['title']}\"  ({j['chars']} chars, {j['style']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
