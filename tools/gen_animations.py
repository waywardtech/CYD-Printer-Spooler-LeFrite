#!/usr/bin/env python3
"""
Regenerate the bundled ASCII animations in assets/animations/.

    python3 tools/gen_animations.py            # writes only files that don't exist yet
    python3 tools/gen_animations.py --force    # overwrite the bundled ones

Everything is plain 7-bit ASCII so the CYD's built-in GLCD font can draw it.
Frames are at most 32 columns x 12 rows (fits the CYD in either orientation).
Output is deterministic (fixed random seed).
"""

import argparse
import math
import random
from pathlib import Path

W, H = 32, 12
OUT = Path(__file__).resolve().parent.parent / "assets" / "animations"


def blank():
    return [[" "] * W for _ in range(H)]


def put(g, x, y, s):
    for i, ch in enumerate(s):
        if 0 <= y < H and 0 <= x + i < W:
            g[y][x + i] = ch


def sprite(g, x, y, lines):
    for dy, line in enumerate(lines):
        for dx, ch in enumerate(line):
            if ch != " ":
                put(g, x + dx, y + dy, ch)


def rows(g):
    return ["".join(r).rstrip() for r in g]


def write(name, title, fps, frames, note="", loop=True):
    head = [f"# name: {title}", f"# fps: {fps}", f"# loop: {'yes' if loop else 'no'}"]
    if note:
        head.append(f"# note: {note}")
    body = []
    for f in frames:
        body.append("---")
        body.extend(f)
    return name, "\n".join(head + body) + "\n"


# --------------------------------------------------------------------------

def spinner():
    frames = []
    for i, c in enumerate("|/-\\" * 2):
        dots = "." * (i % 4)
        frames.append(["", f"   [{c}] SPOOLING{dots}"])
    return write("spinner", "Spinning Cursor", 8, frames, "the classic | / - \\ busy indicator")


def bouncing_ball():
    frames = []
    floor = H - 2
    x, vx = 1, 1
    for t in range(28):
        phase = (t % 14) / 14.0
        height = abs(math.sin(phase * math.pi))
        y = int(round(floor - 1 - height * (floor - 2)))
        g = blank()
        put(g, 0, floor, "=" * W)
        put(g, x, y, "O" if y < floor - 1 else "o")  # squashes a little on the floor
        x += vx
        if x >= W - 1 or x <= 0:
            vx = -vx
            x += 2 * vx
        frames.append(rows(g))
    return write("bouncing_ball", "Bouncing Ball", 12, frames)


def robot_wave():
    heads = [" [o_o]/", " [o_o]-", " [o_o]\\", " [o_o]-"]
    body = ["  /| |", "  / \\"]
    walk = [["  /| |", "  / \\"], ["  /| |", "   |\\"], ["  /| |", "  / \\"], ["  /| |", "  /|"]]
    frames = []
    for _ in range(2):  # wave on the spot
        for hd in heads:
            g = blank()
            sprite(g, 12, 4, [hd, *body])
            if hd.endswith("/"):
                put(g, 13, 3, "HELLO!")
            frames.append(rows(g))
    for i in range(0, W - 8, 2):  # then walk off
        g = blank()
        sprite(g, i, 4, [" [o_o]", *walk[(i // 2) % 4]])
        put(g, 0, 7, "_" * W)
        frames.append(rows(g))
    return write("robot_wave", "Robot Wave", 4, frames, "waves, then walks off")


def rocket_launch():
    rocket = ["   /\\", "  /  \\", "  |LE|", "  |SP|", " /|__|\\", "  /\\/\\"]
    flame = [["   ''", "  '  '"], ["  ,''.", "   ''"], ["  '..'", "  ,  ,"]]
    frames = []
    for n in (5, 4, 3, 2, 1):
        g = blank()
        sprite(g, 12, H - 7, rocket)
        put(g, 0, H - 1, "_" * W)
        put(g, 22, 2, f"T-{n}")
        frames.append(rows(g))
    y = H - 7
    for t in range(14):
        g = blank()
        sprite(g, 12, y, rocket)
        sprite(g, 12, y + 6, flame[t % 3])
        if t < 4:
            put(g, 0, H - 1, "_" * W)
            put(g, 8, H - 2, "( ( ) ) " if t % 2 else " ) ( ( ")
        if t == 0:
            put(g, 22, 2, "LIFTOFF")
        frames.append(rows(g))
        y -= 1 if t < 3 else 2
    g = blank()
    put(g, 9, 5, "* bon voyage *")
    frames.append(rows(g))
    return write("rocket_launch", "Rocket Launch", 6, frames, "countdown, liftoff, gone", loop=False)


def train():
    loco = [
        "     ____        ",
        " ___ |[]|_n__n_I_",
        "|___||__|###|____}",
        " O-O--O=O-O-O-O  ",
    ]
    car = [
        " _________ ",
        "|  LE     |",
        "|_SPOOLER_|",
        " O-O   O-O ",
    ]
    art = [a.ljust(18) + c for a, c in zip(loco, car)]
    width = max(len(l) for l in art)
    smoke = ["  (  )", " ( )", "()"]
    frames = []
    for x in range(W, -width - 1, -2):
        g = blank()
        sprite(g, x, 5, art)
        for i, s in enumerate(smoke):
            puff = s if (x // 2 + i) % 2 else s.replace("(", "o").replace(")", "")
            put(g, x + 3 + i * 3, 3 - i, puff)
        put(g, 0, 9, "=" * W)
        put(g, 0, 10, "".join("|" if (i + x) % 4 == 0 else " " for i in range(W)))
        frames.append(rows(g))
    return write("train", "Freight Train", 8, frames, "choo choo, right to left")


def fish_tank():
    rng = random.Random(3)
    floor = "".join(rng.choice("._,") for _ in range(W))
    # (start offset, row, direction, right-facing shape); fish bounce off the glass
    fish = [(2, 3, 1, "><>"), (20, 5, -1, "><>"), (8, 7, 1, "><(((o>"), (26, 2, -1, "><(((o>")]
    flip = str.maketrans("<>()", "><)(")
    frames = []
    for t in range(32):
        g = blank()
        put(g, 0, 0, "~" * W)
        put(g, 0, H - 1, floor)
        for k in range(3):  # swaying weed
            wx = 5 + k * 11
            put(g, wx, H - 3, ")" if (t + k) % 2 else "(")
            put(g, wx, H - 2, "(" if (t + k) % 2 else ")")
        for x0, y, d, f in fish:
            span = W - len(f)
            pos = (x0 + t) % (2 * span)
            heading_right = pos < span
            x = pos if heading_right else 2 * span - pos
            if d < 0:  # mirror the path so this fish starts out swimming left
                x, heading_right = span - x, not heading_right
            shape = f if heading_right else f[::-1].translate(flip)
            put(g, x, y, shape)
            if (t + x0) % 6 == 0:
                put(g, x + len(f) if heading_right else x - 1, y - 1, "o")
        for b in range(2):  # bubbles
            by = H - 2 - ((t + b * 5) % (H - 2))
            put(g, 16 + b * 7, by, "o" if by % 2 else ".")
        frames.append(rows(g))
    return write("fish_tank", "Fish Tank", 5, frames)


def matrix_rain():
    rng = random.Random(42)
    glyphs = "01ABCDEFXZ$%&*+=<>?:;"
    trail = [[-rng.randrange(0, H * 2), rng.randrange(4, 9)] for _ in range(W)]
    frames = []
    for _ in range(36):
        g = blank()
        for x in range(0, W, 2):
            head, length = trail[x]
            for k in range(length):
                y = head - k
                if 0 <= y < H:
                    put(g, x, y, "@" if k == 0 else rng.choice(glyphs))
            trail[x][0] = head + 1
            if head - length > H:
                trail[x] = [-rng.randrange(0, 8), rng.randrange(4, 9)]
        frames.append(rows(g))
    return write("matrix_rain", "Matrix Rain", 10, frames, "@ marks the head of each falling column")


def starfield():
    rng = random.Random(7)
    cx, cy = W / 2, H / 2
    stars = [[rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.1, 1.0)] for _ in range(40)]
    frames = []
    for _ in range(30):
        g = blank()
        for s in stars:
            s[2] -= 0.06
            if s[2] <= 0.05:
                s[0], s[1], s[2] = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
            x = int(cx + s[0] / s[2] * cx * 0.5)
            y = int(cy + s[1] / s[2] * cy * 0.5)
            put(g, x, y, "." if s[2] > 0.66 else ("+" if s[2] > 0.33 else "*"))
        frames.append(rows(g))
    return write("starfield", "Starfield", 12, frames, "warp speed, Mr. Spooler")


def radar_sweep():
    cx, cy, r = W // 2, H // 2, 5.5
    blips = [(0.8, 3.2), (0.5, 1.1), (0.9, 5.0)]  # (radius fraction, angle rad)
    frames = []
    steps = 16
    for i in range(steps):
        ang = 2 * math.pi * i / steps
        g = blank()
        for a in range(0, 360, 10):  # scope ring (x doubled because chars are tall)
            t = math.radians(a)
            put(g, int(round(cx + 2 * r * math.cos(t))), int(round(cy + r * math.sin(t))), ".")
        put(g, cx, cy, "+")
        for k in range(1, 12):
            d = r * k / 12
            put(g, int(round(cx + 2 * d * math.cos(ang))), int(round(cy + d * math.sin(ang))),
                "#" if k < 11 else "*")
        for br, ba in blips:
            diff = (ang - ba) % (2 * math.pi)
            if diff < 2.0:  # blip fades after the sweep passes
                put(g, int(round(cx + 2 * r * br * math.cos(ba))), int(round(cy + r * br * math.sin(ba))),
                    "@" if diff < 0.7 else "o")
        put(g, 0, 0, "RADAR")
        put(g, W - 6, 0, f"{int(math.degrees(ang)):03d}deg")
        frames.append(rows(g))
    return write("radar_sweep", "Radar Sweep", 8, frames)


def printer_self_test():
    pattern = "".join(chr(c) for c in range(33, 127))
    lines = ["LE SPOOLER SELF TEST  ROM v0.2", "-" * W]
    for i in range(8):
        lines.append((pattern[i:] + pattern[:i])[:W])
    lines += ["-" * W, "RIBBON OK  PLATEN OK  BELL *"]
    frames, shown = [], []
    for line in lines:
        for cut in (8, 16, 24, W):  # the head sweeps across in four bursts
            g = blank()
            for y, l in enumerate((shown + [line[:cut]])[-H:]):
                put(g, 0, y, l)
            frames.append(rows(g))
        shown.append(line)
    return write("printer_self_test", "Printer Self-Test Page", 10, frames,
                 "the rolling ASCII barber-pole every printer did at power-on", loop=False)


GENERATORS = [spinner, bouncing_ball, robot_wave, rocket_launch, train, fish_tank,
              matrix_rain, starfield, radar_sweep, printer_self_test]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="overwrite existing .anim files")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for gen in GENERATORS:
        name, text = gen()
        path = OUT / f"{name}.anim"
        if path.exists() and not args.force:
            print(f"skip   {path.name} (exists; use --force)")
            continue
        path.write_text(text, encoding="ascii", newline="\n")
        print(f"wrote  {path.name}  ({text.count(chr(10) + '---')} frames)")


if __name__ == "__main__":
    main()
