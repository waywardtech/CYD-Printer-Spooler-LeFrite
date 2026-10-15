"""
End-to-end tests for the LE SPOOLER server, CLI and preview.

    python3 -m unittest discover -s tests -v

Runs a real server on a random localhost port with a throwaway spool directory.
Standard library only.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory(prefix="le-spooler-test-")
os.environ["LE_SPOOLER_SPOOL"] = TMP.name           # must be set before import
sys.path.insert(0, str(ROOT / "server"))

import le_spooler_server as srv  # noqa: E402
import le_spooler_preview as prev  # noqa: E402


def http(method, url, body=None, ctype="application/json"):
    data = body if body is None or isinstance(body, bytes) else (
        json.dumps(body).encode() if ctype == "application/json" else body.encode())
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": ctype} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw and "json" in r.headers.get("Content-Type", "") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class SpoolerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = srv.make_server("127.0.0.1", 0, quiet=True)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def drain(self):
        """Ack everything so each test starts with an empty queue."""
        while True:
            code, r = http("GET", self.base + "/next")
            if code == 204:
                return
            http("POST", f"{self.base}/ack/{r['job']['id']}")

    def setUp(self):
        self.drain()

    # ------------------------------------------------------------------ API

    def test_health(self):
        code, r = http("GET", self.base + "/health")
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["service"], "le-spooler")
        self.assertGreaterEqual(r["animations"], 10)

    def test_print_json_then_next_then_ack(self):
        code, r = http("POST", self.base + "/print",
                       {"title": "Hello Ellis", "source": "test", "mode": "print",
                        "style": "greenbar", "body": "HELLO ELLIS"})
        self.assertEqual(code, 201)
        jid = r["id"]
        self.assertRegex(jid, srv.JOB_ID_RE)

        code, r = http("GET", self.base + "/next")
        self.assertEqual(code, 200)
        self.assertEqual(r["job"]["id"], jid)
        self.assertEqual(r["job"]["body"], "HELLO ELLIS")

        code, r = http("POST", f"{self.base}/ack/{jid}")
        self.assertEqual((code, r["already_acked"]), (200, False))
        code, r = http("POST", f"{self.base}/ack/{jid}")
        self.assertEqual((code, r["already_acked"]), (200, True))   # idempotent
        self.assertTrue((Path(TMP.name) / "printed" / f"{jid}.json").exists())
        self.assertFalse((Path(TMP.name) / "incoming" / f"{jid}.json").exists())
        self.assertEqual(http("GET", self.base + "/next")[0], 204)

    def test_queue_order_is_fifo_even_within_one_second(self):
        ids = [http("POST", self.base + "/print", {"body": f"job {i}"})[1]["id"] for i in range(6)]
        self.assertEqual(ids, sorted(ids))
        seen = []
        for _ in ids:
            _, r = http("GET", self.base + "/next")
            seen.append(r["job"]["id"])
            http("POST", f"{self.base}/ack/{r['job']['id']}")
        self.assertEqual(seen, ids)

    def test_plain_text_and_envelope(self):
        code, r = http("POST", self.base + "/print", "just text\n", ctype="text/plain")
        self.assertEqual(code, 201)
        env = ("@LE_SPOOLER_BEGIN\nTITLE: Env test\nSTYLE: BLUEBAR\nCHAOS: LOW\n\n"
               "LINE ONE\n\nLINE THREE\n@LE_SPOOLER_END\n")
        code, r = http("POST", self.base + "/print", env, ctype="text/plain")
        self.assertEqual(code, 201)
        self.assertEqual((r["job"]["title"], r["job"]["style"], r["job"]["chaos"]), ("Env test", "bluebar", "low"))
        code, job = http("GET", f"{self.base}/jobs/{r['id']}")
        self.assertEqual(job["job"]["body"], "LINE ONE\n\nLINE THREE\n")
        code, text = http("GET", f"{self.base}/jobs/{r['id']}?format=text")
        self.assertTrue(text.startswith("@LE_SPOOLER_BEGIN\nID: "))
        self.assertIn("STYLE: BLUEBAR", text)
        self.assertEqual(srv.parse_envelope(text)[1], "LINE ONE\n\nLINE THREE\n")  # round-trips

    def test_control_characters_survive(self):
        body = "cafe\b' garc\b,on\r----\f\x07\n"
        _, r = http("POST", self.base + "/print", {"body": body})
        _, job = http("GET", f"{self.base}/jobs/{r['id']}")
        self.assertEqual(job["job"]["body"], body)

    def test_rejects_bad_input(self):
        self.assertEqual(http("POST", self.base + "/print", {"body": ""})[0], 400)
        self.assertEqual(http("POST", self.base + "/print", {"body": "x", "style": "nope"})[0], 400)
        self.assertEqual(http("POST", self.base + "/print", b"{not json", ctype="application/json")[0], 400)
        self.assertEqual(http("POST", self.base + "/print", [1, 2])[0], 400)
        self.assertEqual(http("GET", self.base + "/jobs/..%2F..%2Fetc%2Fpasswd")[0], 404)
        self.assertEqual(http("POST", self.base + "/ack/20990101-000000-000")[0], 404)
        self.assertEqual(http("GET", self.base + "/animations/..%2Fsecret")[0], 400)
        self.assertEqual(http("GET", self.base + "/nope")[0], 404)

    def test_jobs_listing(self):
        _, r = http("POST", self.base + "/print", {"title": "Listed", "body": "abc"})
        _, lst = http("GET", self.base + "/jobs")
        entry = next(j for j in lst["jobs"] if j["id"] == r["id"])
        self.assertEqual((entry["title"], entry["status"], entry["chars"]), ("Listed", "queued", 3))
        self.assertNotIn("body", entry)

    def test_animations(self):
        _, r = http("GET", self.base + "/animations")
        names = {a["id"] for a in r["animations"]}
        for n in ("spinner", "bouncing_ball", "robot_wave", "rocket_launch", "train", "fish_tank",
                  "matrix_rain", "starfield", "radar_sweep", "printer_self_test"):
            self.assertIn(n, names)
        _, r = http("GET", self.base + "/animations/robot_wave")
        a = r["animation"]
        self.assertEqual(a["name"], "Robot Wave")
        self.assertGreater(a["frame_count"], 3)
        self.assertLessEqual(a["width"], 32)
        self.assertLessEqual(a["height"], 12)
        _, raw = http("GET", self.base + "/animations/robot_wave?format=raw")
        self.assertTrue(raw.startswith("# name: Robot Wave"))

    def test_web_ui_and_cors(self):
        req = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn(b"LE SPOOLER", r.read())
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")

    # ------------------------------------------------------------------ CLI

    def test_cli_prints_and_reads_stdin(self):
        cli = [sys.executable, str(ROOT / "cli" / "leprint.py"), "--server", self.base]
        out = subprocess.run(cli + ["--title", "CLI", "HELLO", "ELLIS"], capture_output=True, text=True, check=True)
        self.assertIn('"CLI"', out.stdout)
        out = subprocess.run(cli + ["-q", "-e"], input="cafe\\b'\n", capture_output=True, text=True, check=True)
        _, job = http("GET", f"{self.base}/jobs/{out.stdout.strip()}")
        self.assertEqual(job["job"]["body"], "cafe\b'\n")

    def test_cli_reports_unreachable_server(self):
        out = subprocess.run([sys.executable, str(ROOT / "cli" / "leprint.py"),
                              "--server", "http://127.0.0.1:9", "hi"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("cannot reach", out.stderr)


class Utf8Buffer(io.StringIO):
    encoding = "utf-8"  # the preview picks Unicode glyphs only on UTF-8 outputs


class PreviewTest(unittest.TestCase):
    def render(self, text, **kw):
        args = type("A", (), dict(no_color=True, ascii=False, cps=0, width=40, page_lines=66,
                                  lf_only=False, seed=1, chaos="off", ribbon_wear=None, ribbon=100.0) | kw)
        buf = Utf8Buffer()
        p = prev.Printer(args, "greenbar", out=buf)
        p.print_text(text)
        return buf.getvalue(), p

    def test_accent_overstrike(self):
        out, _ = self.render("cafe\b' a\b` ho\b^tel man\b~ana garc\b,on u\b\"ber\n")
        for word in ("café", "à", "hôtel", "mañana", "garçon", "über"):
            self.assertIn(word, out)

    def test_compose_table(self):
        self.assertEqual(prev.compose("e", "'"), "é")
        self.assertEqual(prev.compose("'", "e"), "é")   # either order
        self.assertEqual(prev.compose("O", "/"), "Ø")
        self.assertIsNone(prev.compose("x", "y"))

    def test_carriage_return_overprints_and_space_never_erases(self):
        out, p = self.render("ABC\r   X\n")
        self.assertIn("ABCX", out)

    def test_form_feed_reaches_next_form(self):
        out, _ = self.render("top\fnext page\n", page_lines=10)
        lines = out.splitlines()
        top = next(i for i, l in enumerate(lines) if "top" in l)
        nxt = next(i for i, l in enumerate(lines) if "next page" in l)
        self.assertEqual(nxt - top, 11)  # 10 lines per form + the perforation row

    def test_bell_and_chaos_events_are_counted(self):
        _, p = self.render("ding\a\n")
        self.assertEqual(p.events["bell"], 1)
        _, p = self.render("x" * 3000 + "\n", chaos="high", seed=3)
        self.assertGreater(p.events["misstrike"] + p.events["jam"] + p.events["skew"], 0)
        self.assertLess(p.ribbon, 100)

    def test_chaos_off_is_calm(self):
        _, p = self.render("x" * 3000 + "\n")
        self.assertEqual(p.events["jam"] + p.events["misstrike"] + p.events["paper_out"], 0)
        self.assertEqual(p.ribbon, 100)


if __name__ == "__main__":
    unittest.main()
