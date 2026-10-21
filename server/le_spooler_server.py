#!/usr/bin/env python3
"""
LE SPOOLER - La Frite-side print spooler.

A tiny HTTP print queue for the LE SPOOLER fanfold daisywheel emulator.
Pure Python 3 standard library: no Flask, no FastAPI, nothing to pip install.

    python3 le_spooler_server.py                 # listens on 0.0.0.0:8765
    python3 le_spooler_server.py --port 8765 --host 127.0.0.1

Endpoints (see ../docs/protocol.md for full detail):

    GET  /                     web UI: type a document, watch the queue, preview paper
    GET  /health               liveness + queue counts
    POST /print                queue a job (application/json or text/plain)
    GET  /jobs                 list all jobs (queued + printed)
    GET  /jobs/<id>            one job (add ?format=text for the @LE_SPOOLER envelope)
    GET  /next                 oldest unacknowledged job, or 204 if the queue is empty
    POST /ack/<id>             mark job printed; moves it incoming/ -> printed/
    POST /jobs/<id>/<action>   hold | release | delete | reprint | printed  (DELETE /jobs/<id> too)
    GET  /animations           list available ASCII animations
    GET  /animations/<name>    one animation, parsed into frames (add ?format=raw for the file)

Storage layout (relative to the project root, i.e. the parent of server/):

    spool/incoming/<id>.json   jobs waiting for the CYD
    spool/printed/<id>.json    jobs the CYD has acknowledged
    spool/index.jsonl          append-only event log ("queued", "printed")

Set LE_SPOOLER_SPOOL=/some/dir to keep the spool somewhere else (tests do this).

The *file location* is the source of truth for job state; index.jsonl is a
human-readable audit trail and is used to list jobs in order.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.5.1"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1024 * 1024  # 1 MiB is a *lot* of fanfold paper

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPOOL_DIR = Path(os.environ.get("LE_SPOOLER_SPOOL") or PROJECT_ROOT / "spool").resolve()
INCOMING_DIR = SPOOL_DIR / "incoming"
PRINTED_DIR = SPOOL_DIR / "printed"
HELD_DIR = SPOOL_DIR / "held"          # paused jobs: /next skips them
DELETED_DIR = SPOOL_DIR / "deleted"    # soft-deleted jobs (recoverable by hand)
INDEX_FILE = SPOOL_DIR / "index.jsonl"
ANIM_DIR = PROJECT_ROOT / "assets" / "animations"
WEB_DIR = Path(__file__).resolve().parent / "web"

JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{3}$")
ANIM_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")

VALID_MODES = {"print", "animation"}
VALID_STYLES = {"greenbar", "plain", "bluebar", "thermal"}

# One lock serialises every write to the spool (new job, ack, index append).
SPOOL_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_last_job_id = ""


def new_job_id() -> str:
    """
    Sortable, human-readable id: YYYYMMDD-HHMMSS-mmm (UTC, milliseconds).
    Sorting ids == queue order, so /next can just take the smallest one.
    Caller must hold SPOOL_LOCK; we wait out the millisecond to stay unique.
    """
    global _last_job_id
    while True:
        now = time.time()
        jid = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        if jid > _last_job_id and job_path(jid)[0] is None:
            _last_job_id = jid
            return jid
        time.sleep(0.001)


def ensure_dirs() -> None:
    for d in (INCOMING_DIR, PRINTED_DIR, HELD_DIR, DELETED_DIR, ANIM_DIR):
        d.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.touch(exist_ok=True)


def append_index(event: dict) -> None:
    """Caller must hold SPOOL_LOCK."""
    with INDEX_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_index() -> list:
    """Return index events, skipping any corrupt lines rather than crashing."""
    events = []
    if not INDEX_FILE.exists():
        return events
    with INDEX_FILE.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log(f"warning: index.jsonl line {lineno} is not valid JSON, skipped")
    return events


def job_path(job_id: str):
    """Locate a job on disk. Returns (path, status) or (None, None)."""
    if not JOB_ID_RE.match(job_id or ""):
        return None, None
    for folder, status in ((INCOMING_DIR, "queued"), (HELD_DIR, "held"), (PRINTED_DIR, "printed")):
        p = folder / f"{job_id}.json"
        if p.exists():
            return p, status
    return None, None


def load_job(job_id: str):
    path, status = job_path(job_id)
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as f:
        job = json.load(f)
    job["status"] = status
    return job


def job_summary(job: dict) -> dict:
    """Job metadata without the (possibly large) body."""
    s = {k: v for k, v in job.items() if k != "body"}
    s["chars"] = len(job.get("body", ""))
    return s


# --------------------------------------------------------------------------
# @LE_SPOOLER envelope (see docs/protocol.md)
# --------------------------------------------------------------------------

ENVELOPE_BEGIN = "@LE_SPOOLER_BEGIN"
ENVELOPE_END = "@LE_SPOOLER_END"
HEADER_RE = re.compile(r"^([A-Z_]+):\s*(.*)$")


def parse_envelope(text: str):
    """
    Parse a text/plain body. If it is wrapped in @LE_SPOOLER_BEGIN/END, read the
    KEY: value header block (ended by the first blank line) and return
    (headers_dict, body). Otherwise return ({}, text) unchanged.
    """
    stripped = text.lstrip("\ufeff")
    if not stripped.lstrip().startswith(ENVELOPE_BEGIN):
        return {}, text

    lines = stripped.lstrip().split("\n")[1:]  # drop @LE_SPOOLER_BEGIN
    headers = {}
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r")
        if line.strip() == "":
            i += 1
            break
        m = HEADER_RE.match(line)
        if not m:
            break  # no blank line after headers; treat the rest as body
        headers[m.group(1).lower()] = m.group(2).strip()
        i += 1
    body_lines = lines[i:]
    # Drop everything from the last @LE_SPOOLER_END onwards.
    for j in range(len(body_lines) - 1, -1, -1):
        if body_lines[j].strip() == ENVELOPE_END:
            body_lines = body_lines[:j]
            break
    body = "\n".join(body_lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return headers, body


def to_envelope(job: dict) -> str:
    head = [
        ENVELOPE_BEGIN,
        f"ID: {job['id']}",
        f"TITLE: {job.get('title', '')}",
        f"SOURCE: {job.get('source', '')}",
        f"MODE: {job.get('mode', 'print').upper()}",
        f"STYLE: {job.get('style', 'greenbar').upper()}",
        f"SPEED: {job.get('speed', 'daisywheel').upper()}",
        f"CHAOS: {job.get('chaos', 'off').upper()}",
        "",
    ]
    body = job.get("body", "")
    if not body.endswith("\n"):
        body += "\n"
    return "\n".join(head) + "\n" + body + ENVELOPE_END + "\n"


def normalise_job(fields: dict) -> dict:
    """Validate/clean user-supplied job fields. Raises ValueError on bad input."""
    body = fields.get("body")
    if body is None:
        body = fields.get("text", "")
    if not isinstance(body, str):
        raise ValueError("'body' must be a string")
    if body == "":
        raise ValueError("empty job: nothing to print")

    def s(key, default, maxlen=120):
        v = fields.get(key, default)
        if v is None or v == "":
            v = default
        return str(v)[:maxlen]

    mode = s("mode", "print").lower()
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    style = s("style", "greenbar").lower()
    if style not in VALID_STYLES:
        raise ValueError(f"style must be one of {sorted(VALID_STYLES)}")

    return {
        "title": s("title", "Untitled"),
        "source": s("source", "unknown"),
        "mode": mode,
        "style": style,
        "speed": s("speed", "daisywheel").lower(),
        "chaos": s("chaos", "off").lower(),
        "body": body,
    }


def enqueue(fields: dict) -> dict:
    job = normalise_job(fields)
    with SPOOL_LOCK:
        job_id = new_job_id()
        job = {"id": job_id, "created": utc_now(), **job}
        write_json_atomic(INCOMING_DIR / f"{job_id}.json", job)
        append_index({"event": "queued", **job_summary(job)})
    return job


def acknowledge(job_id: str):
    """Move a job to printed/. Idempotent: acking a printed job is OK."""
    note_printed(job_id)
    with SPOOL_LOCK:
        path, status = job_path(job_id)
        if path is None:
            return None
        if status == "printed":
            job = load_job(job_id)
            job["already_acked"] = True
            return job
        with path.open("r", encoding="utf-8") as f:
            job = json.load(f)
        job["printed"] = utc_now()
        write_json_atomic(PRINTED_DIR / f"{job_id}.json", job)
        path.unlink()
        append_index({"event": "printed", "id": job_id, "printed": job["printed"]})
        job["status"] = "printed"
        return job


# Queue management: hold / release / delete / reprint ------------------------

MOVES = {
    #  action     allowed from              destination    new status
    "hold":    ({"queued"},                HELD_DIR,      "held"),
    "release": ({"held"},                  INCOMING_DIR,  "queued"),
    "delete":  ({"queued", "held", "printed"}, DELETED_DIR, "deleted"),
}


def move_job(job_id: str, action: str):
    """Apply hold/release/delete. Returns (job, error) - error is None on success."""
    allowed, dest, new_status = MOVES[action]
    with SPOOL_LOCK:
        path, status = job_path(job_id)
        if path is None:
            return None, "no such job"
        if status not in allowed:
            return None, f"can't {action} a {status} job"
        with path.open("r", encoding="utf-8") as f:
            job = json.load(f)
        os.replace(path, dest / path.name)
        append_index({"event": action, "id": job_id, "at": utc_now()})
        job["status"] = new_status
        return job, None


def reprint(job_id: str):
    """Queue a fresh copy of any job (queued, held or printed)."""
    job = load_job(job_id)
    if job is None:
        return None
    fields = {k: job.get(k) for k in ("title", "mode", "style", "speed", "chaos", "body")}
    fields["source"] = f"reprint of {job_id}"
    return enqueue(fields)


# Which printers are collecting jobs? Anything that polls /next counts.
PRINTERS = {}  # name -> {"address", "agent", "last_seen", "job": {"id", "title"} | None}
PRINTER_ONLINE_SECONDS = 30
PRINTER_BUSY_SECONDS = 30 * 60  # a printer mid-job doesn't poll; it's still there for this long


def note_printer(address: str, agent: str, name: str = "", job=None) -> None:
    if not name:
        name = "CYD" if "ESP32" in agent else (agent.split("/")[0] or "printer")
        name = f"{name} @ {address}"
    PRINTERS[name] = {"address": address, "agent": agent, "last_seen": time.time(),
                      "job": {"id": job["id"], "title": job.get("title", "")} if job else None}


def note_printed(job_id: str) -> None:
    """The printer that took this job has finished it."""
    for p in PRINTERS.values():
        if p.get("job") and p["job"]["id"] == job_id:
            p["job"] = None
            p["last_seen"] = time.time()


def printer_status() -> list:
    now = time.time()
    out = []
    for name, p in sorted(PRINTERS.items(), key=lambda kv: -kv[1]["last_seen"]):
        age = now - p["last_seen"]
        busy = p.get("job") is not None and age < PRINTER_BUSY_SECONDS
        if age < 24 * 3600:
            out.append({"name": name, "address": p["address"], "seconds_ago": round(age, 1),
                        "online": age < PRINTER_ONLINE_SECONDS or busy,
                        "printing": p["job"] if busy else None})
    return out


def list_jobs() -> list:
    """Fold index events into one entry per job, oldest first; verify on disk."""
    order, seen = [], {}
    for ev in read_index():
        jid = ev.get("id")
        if not jid:
            continue
        if ev.get("event") == "queued" and jid not in seen:
            seen[jid] = {k: v for k, v in ev.items() if k != "event"}
            order.append(jid)
        elif ev.get("event") == "printed" and jid in seen:
            seen[jid]["printed"] = ev.get("printed")
    jobs = []
    for jid in order:
        _, status = job_path(jid)
        if status is None:
            continue  # file removed by hand; don't list a ghost
        entry = seen[jid]
        entry["status"] = status
        jobs.append(entry)
    return jobs


def next_job():
    """Oldest queued job. Uses directory listing so it works even if index is damaged."""
    ids = sorted(p.stem for p in INCOMING_DIR.glob("*.json") if JOB_ID_RE.match(p.stem))
    return load_job(ids[0]) if ids else None


# --------------------------------------------------------------------------
# Animations (see docs/protocol.md, "Animation format")
# --------------------------------------------------------------------------

def parse_animation(text: str, name: str) -> dict:
    meta = {"name": name, "fps": 4, "loop": True}
    frames, current, started = [], [], False
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.strip() == "---":
            if started:
                frames.append(current)
            current, started = [], True
            continue
        if not started:
            m = re.match(r"^#\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
            if m:
                key, val = m.group(1).lower(), m.group(2).strip()
                if key == "fps":
                    try:
                        meta["fps"] = max(0.1, min(60.0, float(val)))
                    except ValueError:
                        pass
                elif key == "loop":
                    meta["loop"] = val.lower() not in ("0", "no", "false", "off")
                else:
                    meta[key] = val
            continue
        current.append(line)
    if started and any(l.strip() for l in current):
        frames.append(current)
    # Trim trailing blank lines inside each frame, keep leading ones (they position art).
    for f in frames:
        while f and f[-1].strip() == "":
            f.pop()
    meta["frames"] = frames
    meta["frame_count"] = len(frames)
    meta["width"] = max((len(l) for f in frames for l in f), default=0)
    meta["height"] = max((len(f) for f in frames), default=0)
    return meta


def list_animations() -> list:
    out = []
    for p in sorted(ANIM_DIR.glob("*.anim")):
        try:
            a = parse_animation(p.read_text(encoding="utf-8"), p.stem)
        except OSError:
            continue
        out.append({k: v for k, v in a.items() if k != "frames"} | {"id": p.stem})
    return out


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

QUIET = False


def log(msg: str) -> None:
    if QUIET:
        return
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


class SpoolerHandler(BaseHTTPRequestHandler):
    server_version = f"LE-SPOOLER/{VERSION}"
    protocol_version = "HTTP/1.1"

    # --- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        log(f"{self.client_address[0]} {fmt % args}")

    def _cors(self):
        # Lets browser tools (TurboWarp desktop / a local web page) call us.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def send_bytes(self, data: bytes, ctype: str, status=HTTPStatus.OK, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj, status=HTTPStatus.OK):
        data = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_bytes(data, "application/json; charset=utf-8", status)

    def send_text(self, text, status=HTTPStatus.OK, extra_headers=None):
        self.send_bytes(text.encode("utf-8"), "text/plain; charset=utf-8", status, extra_headers)

    def send_empty(self, status=HTTPStatus.NO_CONTENT):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def error(self, status, message):
        self.send_json({"ok": False, "error": message}, status)

    def read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            raise ValueError("Content-Length header required")
        n = int(length)
        if n < 0 or n > MAX_BODY_BYTES:
            raise OverflowError(f"body too large (max {MAX_BODY_BYTES} bytes)")
        return self.rfile.read(n)

    def route(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        return parts, parse_qs(u.query)

    # --- verbs ------------------------------------------------------------

    def do_OPTIONS(self):
        self.send_empty(HTTPStatus.NO_CONTENT)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            parts, q = self.route()
            fmt = (q.get("format") or ["json"])[0]

            if parts in ([], ["ui"], ["index.html"]):
                page = WEB_DIR / "index.html"
                if not page.exists():
                    return self.error(HTTPStatus.NOT_FOUND, "web UI not installed (server/web/index.html)")
                return self.send_bytes(page.read_bytes(), "text/html; charset=utf-8",
                                       extra_headers={"Cache-Control": "no-cache"})

            if parts == ["health"]:
                queued = sum(1 for _ in INCOMING_DIR.glob("*.json"))
                printed = sum(1 for _ in PRINTED_DIR.glob("*.json"))
                return self.send_json({
                    "ok": True,
                    "service": "le-spooler",
                    "version": VERSION,
                    "time": utc_now(),
                    "queued": queued,
                    "printed": printed,
                    "held": sum(1 for _ in HELD_DIR.glob("*.json")),
                    "animations": len(list(ANIM_DIR.glob("*.anim"))),
                    "printers": printer_status(),
                })

            if parts == ["jobs"]:
                jobs = list_jobs()
                return self.send_json({"ok": True, "count": len(jobs), "jobs": jobs})

            if len(parts) == 2 and parts[0] == "jobs":
                job = load_job(parts[1])
                if job is None:
                    return self.error(HTTPStatus.NOT_FOUND, "no such job")
                if fmt == "text":
                    return self.send_text(to_envelope(job), extra_headers={"X-Job-Id": job["id"]})
                return self.send_json({"ok": True, "job": job})

            if parts == ["next"]:
                # ?printer=NAME lets a client label itself (the web UI's virtual printer does)
                job = next_job()
                note_printer(self.client_address[0], self.headers.get("User-Agent", ""),
                             (q.get("printer") or [""])[0][:60], job)
                if job is None:
                    return self.send_empty(HTTPStatus.NO_CONTENT)
                if fmt == "text":
                    return self.send_text(to_envelope(job), extra_headers={"X-Job-Id": job["id"]})
                return self.send_json({"ok": True, "job": job})

            if parts == ["animations"]:
                anims = list_animations()
                return self.send_json({"ok": True, "count": len(anims), "animations": anims})

            if len(parts) == 2 and parts[0] == "animations":
                name = parts[1].removesuffix(".anim")
                if not ANIM_NAME_RE.match(name):
                    return self.error(HTTPStatus.BAD_REQUEST, "bad animation name")
                p = ANIM_DIR / f"{name}.anim"
                if not p.exists():
                    return self.error(HTTPStatus.NOT_FOUND, "no such animation")
                text = p.read_text(encoding="utf-8")
                if fmt == "raw":
                    return self.send_text(text)
                return self.send_json({"ok": True, "animation": parse_animation(text, name)})

            return self.error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except Exception as e:  # never let one bad request kill the thread quietly
            log(f"error handling GET {self.path}: {e!r}")
            return self.error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def do_POST(self):
        try:
            parts, _ = self.route()

            if parts == ["print"]:
                try:
                    raw = self.read_body()
                except OverflowError as e:
                    return self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
                except ValueError as e:
                    return self.error(HTTPStatus.LENGTH_REQUIRED, str(e))

                ctype = (self.headers.get("Content-Type") or "text/plain").split(";")[0].strip().lower()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")  # old-school 8-bit printers say hi

                if ctype == "application/json":
                    try:
                        fields = json.loads(text)
                    except json.JSONDecodeError as e:
                        return self.error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {e}")
                    if not isinstance(fields, dict):
                        return self.error(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
                else:
                    # text/plain (or anything else): optional @LE_SPOOLER envelope,
                    # otherwise the whole body is the document.
                    headers, body = parse_envelope(text)
                    fields = dict(headers)
                    fields["body"] = body
                    fields.setdefault("source", self.client_address[0])
                    fields.setdefault("title", "Plain text job")

                try:
                    job = enqueue(fields)
                except ValueError as e:
                    return self.error(HTTPStatus.BAD_REQUEST, str(e))
                log(f"queued {job['id']} '{job['title']}' ({len(job['body'])} chars) from {job['source']}")
                return self.send_json({"ok": True, "id": job["id"], "job": job_summary(job)},
                                      HTTPStatus.CREATED)

            if len(parts) == 2 and parts[0] == "ack":
                job = acknowledge(parts[1])
                if job is None:
                    return self.error(HTTPStatus.NOT_FOUND, "no such job")
                log(f"acked {job['id']}")
                return self.send_json({"ok": True, "id": job["id"], "status": "printed",
                                       "already_acked": job.get("already_acked", False)})

            # Queue management: POST /jobs/<id>/{hold,release,delete,reprint,printed}
            if len(parts) == 3 and parts[0] == "jobs":
                return self.manage(parts[1], parts[2])

            return self.error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except Exception as e:
            log(f"error handling POST {self.path}: {e!r}")
            return self.error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def do_DELETE(self):
        parts, _ = self.route()
        if len(parts) == 2 and parts[0] == "jobs":
            return self.manage(parts[1], "delete")
        return self.error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def manage(self, job_id: str, action: str):
        if not JOB_ID_RE.match(job_id):
            return self.error(HTTPStatus.NOT_FOUND, "no such job")
        if action == "printed":
            job = acknowledge(job_id)
            if job is None:
                return self.error(HTTPStatus.NOT_FOUND, "no such job")
            log(f"marked {job_id} printed by hand")
            return self.send_json({"ok": True, "id": job_id, "status": "printed"})
        if action == "reprint":
            job = reprint(job_id)
            if job is None:
                return self.error(HTTPStatus.NOT_FOUND, "no such job")
            log(f"reprint of {job_id} queued as {job['id']}")
            return self.send_json({"ok": True, "id": job["id"], "status": "queued", "copy_of": job_id},
                                  HTTPStatus.CREATED)
        if action in MOVES:
            job, err = move_job(job_id, action)
            if err:
                code = HTTPStatus.NOT_FOUND if err == "no such job" else HTTPStatus.CONFLICT
                return self.error(code, err)
            log(f"{action} {job_id}")
            return self.send_json({"ok": True, "id": job_id, "status": job["status"]})
        return self.error(HTTPStatus.NOT_FOUND, f"unknown action '{action}'")


def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, quiet: bool = False):
    global QUIET
    QUIET = quiet
    ensure_dirs()
    httpd = ThreadingHTTPServer((host, port), SpoolerHandler)
    httpd.daemon_threads = True
    return httpd


def main():
    ap = argparse.ArgumentParser(description="LE SPOOLER print spooler (stdlib HTTP server)")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    ap.add_argument("--quiet", action="store_true", help="don't log every HTTP request")
    args = ap.parse_args()

    httpd = make_server(args.host, args.port, args.quiet)
    sys.stderr.write(f"LE SPOOLER {VERSION} listening on http://{args.host}:{args.port}  (spool: {SPOOL_DIR})\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down (Ctrl-C)")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
