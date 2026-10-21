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

    def test_hold_release_reprint_delete(self):
        _, r = http("POST", self.base + "/print", {"title": "Managed", "body": "abc"})
        jid = r["id"]
        status = lambda: next((j["status"] for j in http("GET", self.base + "/jobs")[1]["jobs"]  # noqa: E731
                               if j["id"] == jid), None)
        self.assertEqual(http("POST", f"{self.base}/jobs/{jid}/hold")[1]["status"], "held")
        self.assertEqual(status(), "held")
        self.assertEqual(http("GET", self.base + "/next")[0], 204)          # held jobs are skipped
        self.assertEqual(http("POST", f"{self.base}/jobs/{jid}/hold")[0], 409)  # already held
        self.assertEqual(http("POST", f"{self.base}/jobs/{jid}/release")[1]["status"], "queued")
        self.assertEqual(http("POST", f"{self.base}/jobs/{jid}/printed")[1]["status"], "printed")
        code, r = http("POST", f"{self.base}/jobs/{jid}/reprint")
        self.assertEqual((code, r["copy_of"]), (201, jid))
        _, nxt = http("GET", self.base + "/next")
        self.assertEqual((nxt["job"]["id"], nxt["job"]["body"]), (r["id"], "abc"))
        self.assertEqual(http("DELETE", f"{self.base}/jobs/{jid}")[1]["status"], "deleted")
        self.assertIsNone(status())                                          # gone from the list
        self.assertTrue((Path(TMP.name) / "deleted" / f"{jid}.json").exists())  # but recoverable
        self.assertEqual(http("POST", f"{self.base}/jobs/{jid}/delete")[0], 404)
        self.assertEqual(http("POST", f"{self.base}/jobs/{r['id']}/explode")[0], 404)

    def test_printer_presence_from_polling(self):
        http("GET", self.base + "/next?printer=Bench%20CYD")
        _, h = http("GET", self.base + "/health")
        p = next(p for p in h["printers"] if p["name"] == "Bench CYD")
        self.assertTrue(p["online"])
        self.assertIsNone(p["printing"])

    def test_printer_is_busy_until_it_acks(self):
        _, r = http("POST", self.base + "/print", {"title": "Long one", "body": "x"})
        http("GET", self.base + "/next?printer=Busy%20CYD")
        srv.PRINTERS["Busy CYD"]["last_seen"] -= 120          # silent for 2 min while it prints
        p = next(p for p in http("GET", self.base + "/health")[1]["printers"] if p["name"] == "Busy CYD")
        self.assertTrue(p["online"])
        self.assertEqual(p["printing"]["title"], "Long one")
        http("POST", f"{self.base}/ack/{r['id']}")
        p = next(p for p in http("GET", self.base + "/health")[1]["printers"] if p["name"] == "Busy CYD")
        self.assertIsNone(p["printing"])

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

    def test_preview_shows_and_prints_one_animation_frame(self):
        preview = [sys.executable, str(ROOT / "server" / "le_spooler_preview.py"), "--anim", "robot_wave"]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = subprocess.run(preview + ["--frame", "1", "--ascii"], capture_output=True, text=True, encoding="utf-8", check=True, env=env)
        self.assertIn("[o_o]/", out.stdout)
        self.assertIn("1/20", out.stdout)
        out = subprocess.run(preview + ["--print-frame", "2", "--server", self.base],
                             capture_output=True, text=True, encoding="utf-8", check=True, env=env)
        jid = out.stdout.strip().split()[-1]
        _, job = http("GET", f"{self.base}/jobs/{jid}")
        self.assertEqual(job["job"]["title"], "Robot Wave - frame 2/20")
        self.assertIn("[o_o]-", job["job"]["body"])
        bad = subprocess.run(preview + ["--frame", "999"], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertNotEqual(bad.returncode, 0)

    def test_cli_reports_unreachable_server(self):
        out = subprocess.run([sys.executable, str(ROOT / "cli" / "leprint.py"),
                              "--server", "http://127.0.0.1:9", "hi"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("cannot reach", out.stderr)


def load_backend():
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    loader = SourceFileLoader("lespooler_backend", str(ROOT / "contrib" / "cups" / "lespooler"))
    mod = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


class BackendTest(unittest.TestCase):
    """The CUPS backend's pure-Python parts (no CUPS, poppler or tesseract needed)."""

    @classmethod
    def setUpClass(cls):
        cls.be = load_backend()

    def pgm(self, w, h, fn):
        data = bytes(fn(x, y) for y in range(h) for x in range(w))
        path = Path(TMP.name) / f"test-{w}x{h}.pgm"
        path.write_bytes(b"P5\n# made by a test\n%d %d\n255\n" % (w, h) + data)
        return path

    def test_options(self):
        o = self.be.parse_options("job-uuid=urn:x LeSpoolerMode=Picture AsciiWidth=48 'media=9.5x11'")
        self.assertEqual((o["lespoolermode"], o["asciiwidth"], o["media"]), ("Picture", "48", "9.5x11"))

    def test_tidy_strips_driver_margins_per_page(self):
        # what Windows' Generic / Text Only driver sends: CRLF, blank top margin, 10-col indent
        page1 = "\r\n\r\n\r\n          Title\r\n            indented\r\n          cafe\b'\r\n\r\n"
        page2 = "\r\n          Page two\r\n"
        self.assertEqual(self.be.tidy(page1 + "\f" + page2),
                         "Title\n  indented\ncafe\b'\n\fPage two\n")
        self.assertEqual(self.be.tidy_page("ABC\r---\n"), "ABC\r---\n")   # a lone CR is an overprint

    def test_tsv_rebuilds_lines_and_counts_confident_words(self):
        head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = [(1, 1, 1, 1, 100, "Quarterly", 96), (1, 1, 1, 2, 300, "report", 91),
                (1, 1, 2, 1, 140, "indented", 88), (2, 1, 1, 1, 100, "Next", 80),
                (2, 1, 1, 2, 220, "para", 20)]   # conf 20: dropped as noise
        tsv = head + "".join(f"5\t1\t{b}\t{p}\t{l}\t{n}\t{left}\t0\t{len(t) * 20}\t30\t{c}\t{t}\n"
                             for b, p, l, n, left, t, c in rows)
        text, good = self.be.tsv_to_text(tsv)
        self.assertEqual(text, "Quarterly report\n  indented\n\nNext\n")
        self.assertEqual(good, 4)

    def test_pgm_and_ascii_art(self):
        # a dark disc on a white page, with wide margins that should get cropped
        def disc(x, y):
            return 20 if (x - 150) ** 2 + (y - 100) ** 2 < 40 ** 2 else 255
        w, h, px = self.be.read_pgm(self.pgm(300, 200, disc))
        self.assertEqual((w, h, len(px)), (300, 200, 300 * 200))
        art = self.be.ascii_art(w, h, px, cols=20, dither=False)
        lines = art.splitlines()
        self.assertLessEqual(max(len(l) for l in lines), 20)
        self.assertGreaterEqual(len(lines), 8)                # cropped to the disc, ~1:1.8 cells
        self.assertIn("@", art)                               # solid ink in the middle
        mid = lines[len(lines) // 2].strip()
        self.assertEqual(mid[len(mid) // 2], "@")             # solid ink at the centre
        self.assertEqual(self.be.ascii_art(*self.be.read_pgm(self.pgm(50, 40, lambda x, y: 255))), "")

    def test_gradient_uses_the_whole_ramp(self):
        w, h, px = self.be.read_pgm(self.pgm(256, 40, lambda x, y: 255 - x))
        art = self.be.ascii_art(w, h, px, cols=64, dither=False)
        used = set(art) - {"\n"}
        self.assertGreaterEqual(len(used), len(self.be.ASCII_RAMP) - 2)

    def test_text_job_end_to_end_and_unreadable_job_never_blocks_queue(self):
        httpd = srv.make_server("127.0.0.1", 0, quiet=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            os.environ["DEVICE_URI"] = f"lespooler://127.0.0.1:{port}"
            txt = Path(TMP.name) / "job.txt"
            txt.write_text("HELLO FROM A BACKEND TEST\ncafe\b'\n", encoding="utf-8", newline="")
            os.environ["CONTENT_TYPE"] = "text/plain"
            self.assertEqual(self.be.main(["lespooler", "7", "dan", "Backend test", "1", "", str(txt)]), 0)
            junk = Path(TMP.name) / "job.bin"
            junk.write_bytes(bytes(range(256)) * 4)
            os.environ["CONTENT_TYPE"] = "application/octet-stream"
            self.assertEqual(self.be.main(["lespooler", "8", "dan", "Mystery", "1", "", str(junk)]), 0)
            os.environ["DEVICE_URI"] = "lespooler://127.0.0.1:9"     # spooler down -> retry
            self.assertEqual(self.be.main(["lespooler", "9", "dan", "Later", "1", "", str(txt)]), 6)
            titles = {j["title"]: j for j in srv.list_jobs()}
            self.assertEqual(srv.load_job(titles["Backend test"]["id"])["body"], "HELLO FROM A BACKEND TEST\ncafe\b'\n")
            self.assertIn("couldn't read", srv.load_job(titles["Mystery"]["id"])["body"])
        finally:
            for k in ("DEVICE_URI", "CONTENT_TYPE"):
                os.environ.pop(k, None)
            httpd.shutdown()
            httpd.server_close()


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
