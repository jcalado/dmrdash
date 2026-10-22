"""HTTP-level tests for dmrdash.py. No journal, no BrandMeister: only the request handling.

    python3 -m unittest discover -s tests
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
with open(os.path.join(TMP, "dmrdash.conf"), "w") as f:
    json.dump({"device_id": 268000000, "api_key": "", "pin": "4321"}, f)
os.environ["DMRDASH_CONF"] = os.path.join(TMP, "dmrdash.conf")
os.environ["STATE_DIRECTORY"] = TMP

spec = importlib.util.spec_from_file_location("dmrdash", os.path.join(ROOT, "dmrdash.py"))
dmrdash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmrdash)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), dmrdash.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        dmrdash.PIN_FAILS.clear()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=5)
        h = {"Host": "localhost"}
        if body is not None:
            h.update({"Content-Type": "application/json", "X-Dmrdash": "1"})
        h.update(headers or {})
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_page(self):
        status, data = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="pindlg"', data)

    def test_state(self):
        status, data = self.req("GET", "/api/state")
        s = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(s["version"], dmrdash.VERSION)
        self.assertTrue(s["pin"])
        self.assertFalse(s["can_manage"])
        self.assertNotIn("api_key", data.decode())

    def test_log(self):
        status, data = self.req("GET", "/api/log?after=0")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["lines"], [])
        self.assertEqual(self.req("GET", "/api/log?after=x")[0], 400)

    def test_unknown_host_refused(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "192.168.1.5:8080"})[0], 200)

    def test_post_needs_custom_header(self):
        status, _ = self.req("POST", "/api/bm", {"action": "clearEvents"}, {"X-Dmrdash": "0"})
        self.assertEqual(status, 400)

    def test_pin_required(self):
        status, data = self.req("POST", "/api/bm", {"action": "clearEvents"})
        self.assertEqual(status, 401)
        self.assertTrue(json.loads(data)["pin"])

    def test_pin_accepted(self):
        status, data = self.req("POST", "/api/bm", {"action": "clearEvents"}, {"X-Dmrdash-Pin": "4321"})
        self.assertEqual(status, 200, data)

    def test_pin_lockout(self):
        for _ in range(5):
            self.assertEqual(self.req("POST", "/api/bm", {"action": "clearEvents"}, {"X-Dmrdash-Pin": "0000"})[0], 401)
        # locked for a minute, even with the right PIN
        self.assertEqual(self.req("POST", "/api/bm", {"action": "clearEvents"}, {"X-Dmrdash-Pin": "4321"})[0], 429)

    def test_add_body_has_group(self):
        # BM's live API wants "group"; its published spec only lists "talkgroup"
        self.assertEqual(dmrdash.tg_body(2, 268912), {"slot": 2, "group": 268912, "talkgroup": 268912})

    def test_rf_ber_history(self):
        dmrdash.STATE["rfber"].clear()
        dmrdash.handle_line(100, "I", "    RX Offset: -350Hz")
        dmrdash.handle_line(101, "M", "DMR Slot 2, received RF voice header from 2680513 to TG 268911")
        dmrdash.handle_line(104, "M", "DMR Slot 2, RF voice transmission lost from 2680513 to TG 268911, 2.6 seconds, BER: 9.9%")
        dmrdash.handle_line(110, "M", "DMR Slot 1, received network end of voice transmission from 2680875 to TG 268, 6.6 seconds, 0% packet loss, BER: 0.1%")
        s = json.loads(self.req("GET", "/api/state")[1])
        self.assertEqual(s["rf"]["offset"], -350)
        self.assertEqual(s["rf"]["overs"], [{"ts": 104, "ber": 9.9, "dur": 2.6, "lost": True, "slot": 2,
                                             "dst": "268911", "group": True, "off": -350}])

    def test_tune_search(self):
        # BER by offset as measured on the real hotspot on 2026-10-20 (0 Hz was where it started)
        ber = {0: 3.0, 150: 8.9, -150: 0.7, -300: 0.25, -450: 0.4, -375: 0.2}
        steps, step, path = [{"off": 0, "bers": [ber[0]]}], 150, []
        while True:
            nxt, step = dmrdash.tune_next(steps, step, 0)
            if nxt is None:
                break
            path.append(nxt)
            steps.append({"off": nxt, "bers": [ber[nxt]]})
        self.assertEqual(path, [-150, -300, -450, -375])  # -375 is then bracketed by measured offsets

    def test_tune_run(self):
        req = dmrdash.REQUEST_PATH
        st = dmrdash.STATE
        dmrdash.handle_line(1000, "I", "    RX Offset: 0Hz")
        over = lambda ts, ber, secs=4.0: (
            dmrdash.handle_line(ts, "M", "DMR Slot 1, received RF voice header from 2680513 to 2680718"),
            dmrdash.handle_line(ts + secs, "M", f"DMR Slot 1, received RF end of voice transmission from 2680513 to 2680718, {secs} seconds, BER: {ber}%"))
        self.assertEqual(self.req("POST", "/api/bm", {"action": "tuneStart"}, {"X-Dmrdash-Pin": "4321"})[0], 200)
        over(1010, 3.0, secs=0.5)  # too short to count
        for t, b in ((1020, 3.1), (1030, 2.9), (1040, 3.0)):
            over(t, b)
        t = dmrdash.TUNE
        self.assertEqual((t["target"], t["pending"], len(t["steps"])), (-150, True, 2))
        with open(req) as f:
            self.assertEqual(f.read(), "-150\n")
        over(1050, 0.1)  # DMRHost hasn't restarted on -150 yet: ignored
        self.assertEqual(t["steps"][-1]["bers"], [])
        dmrdash.handle_line(1060, "I", "    RX Offset: -150Hz")
        self.assertFalse(t["pending"])
        self.assertEqual(st["rxoffset"], -150)
        s = json.loads(self.req("GET", "/api/state")[1])
        self.assertEqual(s["rf"]["tune"]["state"], "running")
        self.assertEqual(self.req("POST", "/api/bm", {"action": "tuneStop"}, {"X-Dmrdash-Pin": "4321"})[0], 200)
        self.assertEqual(dmrdash.TUNE["state"], "idle")
        with open(req) as f:
            self.assertEqual(f.read(), "0\n")  # back to where it started
        status, data = self.req("POST", "/api/bm", {"action": "tryOffset", "hz": 99999}, {"X-Dmrdash-Pin": "4321"})
        self.assertEqual(status, 400)

    def test_helper_edits_only_modem_rxoffset(self):
        spec = importlib.util.spec_from_file_location("rxoffset", os.path.join(ROOT, "rxoffset.py"))
        rx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rx)
        ini = os.path.join(TMP, "MMDVM.ini")
        with open(ini, "w") as f:
            f.write("[General]\nRXOffset=7\n[Modem]\nPort=/dev/ttyAMA0\nRXOffset=0\nTXOffset=0\n[DMR]\nColorCode=1\n")
        self.assertTrue(rx.set_offset(ini, -350))
        with open(ini) as f:
            self.assertEqual(f.read(), "[General]\nRXOffset=7\n[Modem]\nPort=/dev/ttyAMA0\nRXOffset=-350\nTXOffset=0\n[DMR]\nColorCode=1\n")
        self.assertFalse(rx.set_offset(ini, -350))

    def test_power_request(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        self.assertTrue(json.loads(self.req("GET", "/api/state")[1])["can_power"])
        status, data = self.req("POST", "/api/bm", {"action": "power", "what": "reboot"}, pin)
        self.assertEqual(status, 200, data)
        with open(dmrdash.POWER_PATH) as f:
            self.assertEqual(f.read(), "reboot\n")
        for what in ("halt", "reboot; rm -rf /", None):
            self.assertEqual(self.req("POST", "/api/bm", {"action": "power", "what": what}, pin)[0], 400)
        with open(dmrdash.POWER_PATH) as f:
            self.assertEqual(f.read(), "reboot\n")  # refused requests never reach the file

    def test_power_helper_checks_request(self):
        spec = importlib.util.spec_from_file_location("power", os.path.join(ROOT, "power.py"))
        pw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pw)
        pw.REQUEST = os.path.join(TMP, "power.test")
        with open(pw.REQUEST, "w") as f:
            f.write("poweroff\n")
        self.assertEqual(pw.read_request(), "poweroff")
        old = os.stat(pw.REQUEST).st_mtime - 120
        os.utime(pw.REQUEST, (old, old))
        with self.assertRaises(SystemExit):  # stale: never acts on a leftover file
            pw.read_request()
        with open(pw.REQUEST, "w") as f:
            f.write("halt\n")
        with self.assertRaises(SystemExit):
            pw.read_request()


if __name__ == "__main__":
    unittest.main()
