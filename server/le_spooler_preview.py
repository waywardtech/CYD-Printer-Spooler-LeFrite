#!/usr/bin/env python3
"""
LE SPOOLER terminal preview - a host-side stand-in for what the CYD will do.

Renders a job as fanfold greenbar paper in your terminal and "types" it one
character at a time like a daisywheel printer.

    python3 le_spooler_preview.py                    # next queued job (else newest printed)
    python3 le_spooler_preview.py 20260928-101500-123
    python3 le_spooler_preview.py --file letter.txt --speed teletype
    python3 le_spooler_preview.py --chaos medium --ribbon 40 --seed 7
    python3 le_spooler_preview.py --list
    python3 le_spooler_preview.py --anim robot_wave --loops 3
    python3 le_spooler_preview.py --demo             # built-in overstrike/control-char demo

Control characters:
    \\b  backspace: head moves left one column; next strike overstrikes
    \\r  carriage return: head to column 0, same line
    \\n  line feed (treated as CR+LF like a Unix tty; --lf-only for strict LF)
    \\f  form feed: advance to top of next form (past the perforation)
    \\a  bell: rings the terminal bell and marks the margin
    \\t  tab: next multiple of 8

Overstrike composition (either order): e\\b' -> é, a\\b` -> à, o\\b^ -> ô,
n\\b~ -> ñ, c\\b, -> ç, u\\b" -> ü; X\\bX -> bold; X\\b_ -> underlined;
X\\b- -> struck through; O\\b/ -> Ø.

The preview never modifies the spool: it only reads job files.
"""

import argparse
import json
import os
import random
import shutil
import signal
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from le_spooler_server import (  # noqa: E402  (same directory, stdlib only)
    ANIM_DIR, INCOMING_DIR, PRINTED_DIR, JOB_ID_RE, load_job, list_jobs,
    parse_animation, parse_envelope,
)

# --------------------------------------------------------------------------
# Overstrike tables
# --------------------------------------------------------------------------

# Spacing accent struck over a letter -> Unicode combining mark.
ACCENTS = {
    "'": "\u0301",  # acute       é
    "`": "\u0300",  # grave       à
    "^": "\u0302",  # circumflex  ô
    "~": "\u0303",  # tilde       ñ
    ",": "\u0327",  # cedilla     ç
    '"': "\u0308",  # diaeresis   ü
    "\u00b4": "\u0301",
}

# Classic typewriter overstrike tricks that aren't accents.
OVERSTRIKE_PAIRS = {
    frozenset("O/"): "Ø", frozenset("o/"): "ø", frozenset("0/"): "Ø",
    frozenset("c|"): "¢", frozenset("=/"): "≠", frozenset("L-"): "£",
    frozenset("Y="): "¥", frozenset(":-"): "÷", frozenset("+_"): "±",
    frozenset("a_"): "ª", frozenset("o_"): "º",
}


def compose(a: str, b: str):
    """Combine two struck glyphs if they make a known character, else None."""
    for base, acc in ((a, b), (b, a)):
        if acc in ACCENTS and base.isalpha():
            c = unicodedata.normalize("NFC", base + ACCENTS[acc])
            if len(c) == 1:
                return c
    return OVERSTRIKE_PAIRS.get(frozenset((a, b)))


# --------------------------------------------------------------------------
# Paper styles (xterm-256 colour numbers)
# --------------------------------------------------------------------------

# 24-bit colours. Period greenbar is a pale, slightly grey mint on warm white
# stock, not a bright green. Terminals without truecolor get the nearest xterm-256.
STYLES = {
    #            band A           band B           strip            hole          perf
    "greenbar": ((225, 239, 217), (251, 250, 241), (247, 246, 239), (47, 48, 45), (185, 185, 174)),
    "bluebar":  ((226, 232, 246), (251, 250, 241), (247, 246, 239), (47, 48, 45), (185, 185, 174)),
    "plain":    ((251, 250, 241), (251, 250, 241), (247, 246, 239), (47, 48, 45), (185, 185, 174)),
    "thermal":  ((255, 255, 255), (255, 255, 255), (255, 255, 255), (190, 190, 190), (215, 215, 215)),
}


def xterm256(rgb):
    """Nearest xterm-256 colour (6x6x6 cube or grey ramp)."""
    r, g, b = rgb
    if max(rgb) - min(rgb) < 8:  # near-grey: use the 24-step ramp
        return 232 + min(23, max(0, round((r - 8) / 10)))
    q = lambda v: 0 if v < 48 else (1 if v < 115 else (v - 35) // 40)  # noqa: E731
    return 16 + 36 * q(r) + 6 * q(g) + q(b)

SPEEDS = {"teletype": 10.0, "daisywheel": 30.0, "fast": 250.0, "instant": 0.0}

CHAOS = {
    #          jam/char  paperout/char  misstrike/char  skew/line  ribbon wear %/char
    "off":    (0,        0,             0,              0,         0.0),
    "low":    (1/6000,   1/12000,       1/600,          1/150,     0.004),
    "medium": (1/1500,   1/4000,        1/150,          1/40,      0.02),
    "high":   (1/300,    1/900,         1/40,           1/10,      0.08),
}

STRIP_W = 4  # tractor-feed strip width in terminal columns (each side)


class Cell:
    __slots__ = ("ch", "bold", "under", "struck", "ink", "miss", "tone")

    def __init__(self, ch, ink, tone=0):
        self.ch, self.ink, self.tone = ch, ink, tone  # tone: per-strike hammer/ribbon variation
        self.bold = self.under = self.struck = self.miss = False


class Printer:
    def __init__(self, args, style, out=None):
        self.out = out or sys.stdout
        self.tty = self.out.isatty()
        self.color = self.tty and not args.no_color and "NO_COLOR" not in os.environ
        self.truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        enc = (getattr(self.out, "encoding", "") or "").lower()
        self.uni = enc.startswith("utf") and not args.ascii
        self.animate = self.tty and args.cps > 0
        self.cps = args.cps
        self.width = args.width
        self.page_lines = args.page_lines
        self.lf_only = args.lf_only
        self.colors = STYLES.get(style, STYLES["greenbar"])
        self.rng = random.Random(args.seed)
        self.p_jam, self.p_out, self.p_miss, self.p_skew, wear = CHAOS[args.chaos]
        self.wear = args.ribbon_wear if args.ribbon_wear is not None else wear
        self.ribbon = max(0.0, min(100.0, args.ribbon))
        self.events = {"jam": 0, "paper_out": 0, "misstrike": 0, "skew": 0, "bell": 0,
                       "overstrike": 0, "wrap": 0}
        self.line = 0      # line number on the current form
        self.skew = 0      # current sideways paper offset (columns)
        self._new_row()

    # --- low level output -------------------------------------------------

    def w(self, s):
        self.out.write(s)

    def sgr(self, *codes):
        return f"\x1b[{';'.join(str(c) for c in codes)}m" if self.color else ""

    def fg(self, rgb):
        return self.sgr(38, 2, *rgb) if self.truecolor else self.sgr(38, 5, xterm256(rgb))

    def bg(self, rgb):
        return self.sgr(48, 2, *rgb) if self.truecolor else self.sgr(48, 5, xterm256(rgb))

    def pause(self, seconds):
        if self.animate and seconds > 0:
            self.out.flush()
            time.sleep(seconds)

    def _new_row(self):
        self.cells = [None] * self.width
        self.col = 0
        self.note = ""

    # --- paper geometry ---------------------------------------------------

    def band_is_a(self, line):
        return (line // 3) % 2 == 0  # greenbar bands are 3 lines (1/2" at 6 LPI)

    def strip(self, line, right=False):
        hole = (line % 3) == 1       # one sprocket hole every 1/2"
        hc = ("●" if self.uni else "o") if hole else " "
        a, b, s, h, p = self.colors
        perf = "┊" if self.uni else ":"
        if not self.color:
            band = ("▒" if self.uni else "#") if self.band_is_a(line) else " "
            return (perf + band + " " + hc + "  ") if right else ("  " + hc + " " + band + perf)
        body = self.bg(s) + self.fg(h) + ("  " + hc + " " if not right else " " + hc + "  ")
        pf = self.bg(s) + self.fg(p) + perf
        return (pf + body if right else body + pf) + self.sgr(0)

    def ink_rgb(self, c):
        """Blue-black ribbon ink: fades with the ribbon, varies a little per strike."""
        t = 26 + c.tone + (1.0 - c.ink) * 165 + (60 if c.miss else 0)
        t = int(max(10, min(205, t)))
        return (t, t, min(255, t + 12))

    def render_row(self, cells, line, note=""):
        a, b, *_ = self.colors
        bg = a if self.band_is_a(line) else b
        parts = [self.strip(line)]
        body_cells = [None] * self.skew + list(cells)
        body_cells = body_cells[: self.width]
        body_cells += [None] * (self.width - len(body_cells))
        if self.color:
            paper = self.bg(bg)
            parts.append(paper)
            for c in body_cells:
                if c is None or c.ch == " ":
                    parts.append(" ")
                    continue
                codes = []
                if c.bold:
                    codes.append(1)
                if c.under:
                    codes.append(4)
                if c.struck:
                    codes.append(9)
                if c.miss:
                    codes.append(3)
                parts.append(self.sgr(0) + paper + self.fg(self.ink_rgb(c))
                             + (self.sgr(*codes) if codes else "") + c.ch + self.sgr(0) + paper)
            parts.append(self.sgr(0))
        else:
            parts.append("".join(" " if c is None else
                                 c.ch + ("\u0336" if c.struck and self.uni else "")
                                 for c in body_cells))
        parts.append(self.strip(line, right=True))
        if note:
            parts.append("  " + self.sgr(38, 5, 166) + note + self.sgr(0))
        return "".join(parts)

    def draw_current(self):
        """Redraw the row under the print head and park the cursor at the head."""
        if not self.animate:
            return
        row = self.render_row(self.cells, self.line, self.note)
        head = STRIP_W + 1 + self.col + self.skew
        self.w("\r" + row + "\x1b[K\r" + (f"\x1b[{head}C" if head else ""))
        self.out.flush()

    def perforation(self, label=""):
        dash = "╌" if self.uni else "-"
        total = self.width + 2 * (STRIP_W + 1)
        text = (dash + " ") * (total // 2 + 1)
        text = text[:total]
        if label:
            label = f" {label} "
            mid = (total - len(label)) // 2
            text = text[:mid] + label + text[mid + len(label):]
        self.w(self.bg(self.colors[2]) + self.fg((140, 140, 132)) + text + self.sgr(0) + "\n")

    # --- mechanism --------------------------------------------------------

    def line_feed(self):
        # finish this row on screen
        if self.animate:
            self.draw_current()
            self.w("\r" + self.render_row(self.cells, self.line, self.note) + "\x1b[K\n")
        else:
            self.w(self.render_row(self.cells, self.line, self.note) + "\n")
        keep_col = self.col if self.lf_only else 0
        self._new_row()
        self.col = keep_col
        self.line += 1
        # paper skew: drifts one column, then the tractor pulls it straight again
        if self.skew:
            self.skew = 0 if self.rng.random() < 0.5 else self.skew
        elif self.p_skew and self.rng.random() < self.p_skew:
            self.skew = 1
            self.events["skew"] += 1
            self.note = "(skew)"
        if self.line >= self.page_lines:
            self.perforation()
            self.line = 0
        self.pause(0.06)  # platen click

    def carriage_return(self):
        travel = self.col
        self.col = 0
        self.draw_current()
        self.pause(0.05 + travel * 0.003)  # the carriage has to physically travel back

    def form_feed(self):
        if self.col or any(self.cells):
            self.line_feed()
        while self.line != 0:
            self.line_feed()
            self.pause(0.01)

    def bell(self):
        self.events["bell"] += 1
        self.note = (self.note + " DING!").strip()
        if self.tty:
            self.w("\a")
        self.draw_current()

    def backspace(self):
        if self.col > 0:
            self.col -= 1
        self.draw_current()
        self.pause(0.02)

    def tab(self):
        self.col = min(self.width, (self.col // 8 + 1) * 8)
        self.draw_current()

    def strike(self, ch):
        if self.col >= self.width:  # past right margin: wrap like a line printer would
            self.events["wrap"] += 1
            self.line_feed()
        self.chaos_before_strike()

        ink = self.ribbon / 100.0
        if ink <= 0.03 and ch != " ":
            ch_eff = " " if self.rng.random() < 0.7 else ch  # ribbon is basically gone
        else:
            ch_eff = ch
        miss = bool(self.p_miss and ch != " " and self.rng.random() < self.p_miss)

        cell = self.cells[self.col]
        if cell is None or cell.ch == " ":
            if ch_eff != " ":
                # uneven hammer pressure: each strike is a touch lighter or darker
                self.cells[self.col] = Cell(ch_eff, ink, self.rng.randint(-14, 22))
        elif ch_eff != " ":
            self.events["overstrike"] += 1
            if ch_eff == cell.ch:
                cell.bold = True
            elif ch_eff == "_":
                cell.under = True
            elif cell.ch == "_":
                cell.ch, cell.under = ch_eff, True
            else:
                composed = compose(cell.ch, ch_eff)
                if composed:
                    cell.ch = composed
                elif "-" in (cell.ch, ch_eff):  # letter + hyphen = struck through
                    cell.ch, cell.struck = (ch_eff if cell.ch == "-" else cell.ch), True
                else:
                    cell.ch, cell.bold = ch_eff, True  # ink blob: last strike wins
            cell.ink = max(cell.ink, ink)
        if miss and self.cells[self.col] is not None:
            self.cells[self.col].miss = True
            self.cells[self.col].ink *= 0.5
            self.events["misstrike"] += 1

        if ch != " " and self.wear:
            self.ribbon = max(0.0, self.ribbon - self.wear)
        self.col += 1
        self.draw_current()
        if self.cps:
            self.pause((1.0 / self.cps) * self.rng.uniform(0.75, 1.25))

    def chaos_before_strike(self):
        if self.p_jam and self.rng.random() < self.p_jam:
            self.events["jam"] += 1
            self.note = "** PAPER JAM **"
            self.draw_current()
            self.pause(1.2)
            self.note = "(jam cleared)"
            self.draw_current()
        if self.p_out and self.rng.random() < self.p_out:
            self.events["paper_out"] += 1
            saved_col = self.col
            self.note = "** PAPER OUT **"
            self.line_feed()
            self.perforation("PAPER OUT - loading a fresh stack")
            self.pause(1.5)
            self.line = 0
            self.cells, self.col = [None] * self.width, saved_col  # head stays put

    # --- document ---------------------------------------------------------

    def print_text(self, text):
        self.perforation("LE SPOOLER")
        for ch in text:
            if ch == "\n":
                self.line_feed()
            elif ch == "\r":
                self.carriage_return()
            elif ch == "\b":
                self.backspace()
            elif ch == "\f":
                self.form_feed()
            elif ch == "\a":
                self.bell()
            elif ch == "\t":
                self.tab()
            elif unicodedata.category(ch) == "Cc":
                continue  # other control chars: ignored, like a real printer mostly would
            else:
                self.strike(ch)
        if self.col or any(self.cells):
            self.line_feed()
        # advance a few lines so the job can be torn off, then show the tear line
        for _ in range(2):
            self.line_feed()
        self.perforation("tear here")
        self.out.flush()


# --------------------------------------------------------------------------
# Job loading
# --------------------------------------------------------------------------

def load_from_file(path: str):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".json":
        job = json.loads(text)
        return job.get("title", p.name), job.get("style", "greenbar"), job["body"]
    headers, body = parse_envelope(text)
    return headers.get("title", p.name), headers.get("style", "greenbar").lower(), body


def pick_job(job_id):
    if job_id:
        job = load_job(job_id)
        if job is None:
            sys.exit(f"preview: no job '{job_id}' in spool/incoming or spool/printed")
        return job
    queued = sorted(p.stem for p in INCOMING_DIR.glob("*.json") if JOB_ID_RE.match(p.stem))
    if queued:
        return load_job(queued[0])
    printed = sorted(p.stem for p in PRINTED_DIR.glob("*.json") if JOB_ID_RE.match(p.stem))
    if printed:
        return load_job(printed[-1])
    sys.exit("preview: the spool is empty. Send something with cli/leprint.py first.")


DEMO = (
    "LE SPOOLER SELF-DEMO\n"
    "====================\n"
    "Accents by overstrike: cafe\b' ; voila\b` ; ho\b^tel ; man\b~ana ; garc\b,on ; u\b\"ber\n"
    "Bold by double strike: B\bBO\bOL\bLD\bD      Underline: U\b_N\b_D\b_E\b_R\b_\n"
    "Typewriter tricks: O\b/  c\b|  =\b/  L\b-\n"
    "TOP SECRET\r----------  <- struck out by a carriage-return overprint\n"
    "Bell coming up...\a\n"
    "Tab:\tone\ttwo\tthree\n"
)


def play_animation(name, loops, args):
    p = ANIM_DIR / f"{name.removesuffix('.anim')}.anim"
    if not p.exists():
        names = ", ".join(sorted(x.stem for x in ANIM_DIR.glob("*.anim")))
        sys.exit(f"preview: no animation '{name}'. Available: {names}")
    anim = parse_animation(p.read_text(encoding="utf-8"), p.stem)
    frames, fps = anim["frames"], anim["fps"]
    w = max(anim["width"], len(anim.get("name", "")) + 4)
    h = anim["height"]
    tty = sys.stdout.isatty()
    uni = (sys.stdout.encoding or "").lower().startswith("utf") and not args.ascii
    tl, tr, bl, br, hz, vt = ("┌", "┐", "└", "┘", "─", "│") if uni else ("+", "+", "+", "+", "-", "|")

    def frame_box(i, f):
        title = f" {anim.get('name', name)} "
        top = tl + title + hz * (w + 2 - len(title)) + tr
        rows = [vt + " " + (f[r] if r < len(f) else "").ljust(w) + " " + vt for r in range(h)]
        foot = f" {i + 1}/{len(frames)} @ {fps:g} fps "
        bot = bl + hz * (w + 2 - len(foot)) + foot + br
        return "\n".join([top, *rows, bot])

    def print_frame(i):
        """Queue frame i on the spooler, so it comes out on (virtual or real) paper."""
        job = {"title": f"{anim.get('name', name)} - frame {i + 1}/{len(frames)}", "source": "preview",
               "mode": "print", "style": args.style or "greenbar", "body": "\n".join(frames[i]) + "\n"}
        req = urllib.request.Request(args.server.rstrip("/") + "/print", data=json.dumps(job).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return f"queued frame {i + 1} as {json.load(r)['id']}"
        except (urllib.error.URLError, OSError) as e:
            return f"print failed: {getattr(e, 'reason', e)} (is the server running at {args.server}?)"

    if args.frame is not None or args.print_frame is not None:
        i = (args.print_frame if args.print_frame is not None else args.frame) - 1
        if not 0 <= i < len(frames):
            sys.exit(f"preview: frame must be 1..{len(frames)}")
        print(frame_box(i, frames[i]))
        if args.print_frame is not None:
            print(print_frame(i))
        return

    if not tty:  # e.g. piped into a log: just show every frame once
        for i, f in enumerate(frames):
            print(frame_box(i, f))
        return

    keys = KeyReader()
    help_line = "space pause/play · ←/→ step · p print frame · q quit"
    status, i, playing, loops_done = "", 0, True, 0
    sys.stdout.write("\x1b[?25l\x1b[2J")  # hide cursor, clear screen: fixed "screen" mode
    try:
        with keys:
            while True:
                state = "▶ playing" if playing else "❚❚ paused"
                sys.stdout.write("\x1b[H" + frame_box(i, frames[i]) + f"\n {state:<11} {help_line}\x1b[K\n {status}\x1b[K\n")
                sys.stdout.flush()
                key = keys.read(1.0 / fps if playing else None)
                if key in ("q", "\x1b", "\x03"):
                    break
                if key == " ":
                    playing = not playing
                elif key in ("right", "l", "."):
                    playing, i = False, (i + 1) % len(frames)
                elif key in ("left", "h", ","):
                    playing, i = False, (i - 1) % len(frames)
                elif key in ("p", "P"):
                    playing, status = False, print_frame(i)
                elif key is None and playing:
                    i += 1
                    if i >= len(frames):
                        loops_done += 1
                        if not anim.get("loop", True) or (loops > 0 and loops_done >= loops):
                            break
                        i = 0
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\n")


class KeyReader:
    """Single-key input for the animation player (POSIX terminals; elsewhere keys are ignored)."""

    def __enter__(self):
        self.saved = None
        try:
            import termios
            import tty
            self.termios = termios
            self.fd = sys.stdin.fileno()
            if sys.stdin.isatty():
                self.saved = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)
        except (ImportError, OSError, ValueError):
            pass
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.saved)

    def read(self, timeout):
        """Return a key ('right'/'left' for arrows), or None after `timeout` seconds."""
        if self.saved is None:
            time.sleep(timeout if timeout is not None else 0.25)
            return None
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        ch = os.read(self.fd, 1).decode(errors="ignore")
        if ch == "\x1b" and select.select([sys.stdin], [], [], 0.03)[0]:
            seq = os.read(self.fd, 2).decode(errors="ignore")
            return {"[C": "right", "[D": "left"}.get(seq, "")
        return ch


def main():
    if hasattr(signal, "SIGPIPE"):  # `preview ... | head` should just stop quietly
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description="Preview LE SPOOLER jobs as fanfold paper in a terminal.")
    ap.add_argument("job_id", nargs="?", help="job id (default: next queued, else newest printed)")
    ap.add_argument("--file", help="preview a local .txt / .json / @LE_SPOOLER envelope file")
    ap.add_argument("--demo", action="store_true", help="print the built-in control-character demo")
    ap.add_argument("--list", action="store_true", help="list spooled jobs and exit")
    ap.add_argument("--anim", metavar="NAME", help="play an ASCII animation on a fixed screen")
    ap.add_argument("--loops", type=int, default=3, help="animation loops (0 = forever, q to stop)")
    ap.add_argument("--frame", type=int, metavar="N", help="with --anim: show only frame N and exit")
    ap.add_argument("--print-frame", type=int, metavar="N", help="with --anim: send frame N to the spooler as a job")
    ap.add_argument("--server", default=os.environ.get("LE_SPOOLER_URL", "http://127.0.0.1:8765"),
                    help="spooler URL for --print-frame / the p key (default http://127.0.0.1:8765)")
    ap.add_argument("--speed", default="daisywheel",
                    help="teletype | daisywheel | fast | instant | <chars per second>")
    ap.add_argument("--chaos", default="off", choices=list(CHAOS), help="random printer events (default off)")
    ap.add_argument("--ribbon", type=float, default=100.0, help="starting ribbon ink %% (default 100)")
    ap.add_argument("--ribbon-wear", type=float, default=None, help="ink %% lost per character (default: from --chaos)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for repeatable chaos")
    ap.add_argument("--width", type=int, default=80, help="printable columns (default 80; shrinks to fit terminal)")
    ap.add_argument("--page-lines", type=int, default=66, help="lines per form (66 = 11in at 6 LPI)")
    ap.add_argument("--style", default=None, help="override paper style: greenbar | bluebar | plain | thermal")
    ap.add_argument("--lf-only", action="store_true", help="\\n is a pure line feed (no carriage return)")
    ap.add_argument("--no-color", action="store_true", help="plain text output (also automatic when piped)")
    ap.add_argument("--ascii", action="store_true", help="ASCII-only glyphs for holes and perforations")
    args = ap.parse_args()

    if args.list:
        for j in list_jobs():
            print(f"{j['id']}  {j['status']:<8} {j.get('chars', '?'):>6} ch  {j.get('title', '')}")
        return
    if args.anim:
        play_animation(args.anim, args.loops, args)
        return

    sp = args.speed.lower()
    try:
        args.cps = SPEEDS[sp] if sp in SPEEDS else float(sp)
    except ValueError:
        ap.error(f"--speed: expected one of {list(SPEEDS)} or a number")

    if args.demo:
        title, style, body = "Self demo", "greenbar", DEMO
    elif args.file:
        title, style, body = load_from_file(args.file)
    else:
        job = pick_job(args.job_id)
        title, style, body = job.get("title", ""), job.get("style", "greenbar"), job["body"]
        print(f"Job {job['id']}  \"{title}\"  [{job['status']}]  from {job.get('source', '?')}")

    cols = shutil.get_terminal_size((100, 24)).columns
    max_w = cols - 2 * (STRIP_W + 1) - 1
    if sys.stdout.isatty() and args.width > max_w:
        args.width = max(20, max_w)

    printer = Printer(args, (args.style or style or "greenbar").lower())
    try:
        printer.print_text(body)
    except KeyboardInterrupt:
        printer.w(printer.sgr(0) + "\n[cancelled]\n")
    ev = {k: v for k, v in printer.events.items() if v}
    summary = ", ".join(f"{k}={v}" for k, v in ev.items()) or "none"
    print(f"Printed \"{title}\" | events: {summary} | ribbon left: {printer.ribbon:.1f}%")


if __name__ == "__main__":
    main()
