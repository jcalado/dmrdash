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

    def test_timed_bodies(self):
        # the shape BrandMeister's own dashboard POSTs to /device/{id}/timedtalkgroup, one record per day part
        body = {"slot": 1, "talkgroup": 3100, "startDate": 1792450800, "endDate": 2108070000,
                "parts": [{"day": "monday", "start": 81000, "stop": 86400}, {"day": "tuesday", "start": 0, "stop": 3600}]}
        out = dmrdash.timed_bodies(body)
        self.assertEqual(out[0], {"talkgroup": 3100, "slot": 1, "startDate": 1792450800, "endDate": 2108070000,
                                  "monday": 1, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0, "saturday": 0, "sunday": 0,
                                  "start": 81000, "stop": 86400})
        self.assertEqual((out[1]["tuesday"], out[1]["monday"], out[1]["stop"]), (1, 0, 3600))
        bad = [dict(body, talkgroup=9), dict(body, endDate=1000), dict(body, startDate=2108070001),
               dict(body, parts=[]), dict(body, parts=[{"day": "funday", "start": 0, "stop": 60}]),
               dict(body, parts=[{"day": "monday", "start": 600, "stop": 600}]), dict(body, parts=[{"day": "monday", "start": 0, "stop": 90000}]),
               dict(body, parts="x"), {k: v for k, v in body.items() if k != "endDate"}]
        for b in bad:
            with self.assertRaises(dmrdash.BMError, msg=b):
                dmrdash.timed_bodies(b)

    def test_remove_timed_checks_ids(self):
        for ids in (None, [], ["1"], [0], [True], list(range(1, 16))):
            status, _ = self.req("POST", "/api/bm", {"action": "removeTimed", "recordids": ids}, {"X-Dmrdash-Pin": "4321"})
            self.assertEqual(status, 400, ids)

    def test_edit_timed_adds_before_removing(self):
        calls, real = [], dmrdash.bm_request
        def fake(method, path, body=None, auth=False):
            calls.append((method, path))
            if method == "DELETE":
                raise dmrdash.BMError("BrandMeister API error HTTP 500")
            return {}
        dmrdash.bm_request = fake
        try:
            body = {"action": "editTimed", "recordids": [104686], "slot": 1, "talkgroup": 91, "startDate": 1792268966,
                    "endDate": 2109024000, "parts": [{"day": "saturday", "start": 61200, "stop": 72000}]}
            status, data = self.req("POST", "/api/bm", body, {"X-Dmrdash-Pin": "4321"})
            self.assertEqual(status, 400)
            self.assertIn("couldn't remove the old one", json.loads(data)["error"])
            writes = [c for c in calls if c[0] != "GET"]
            self.assertEqual(writes, [("POST", "/device/268000000/timedtalkgroup"), ("DELETE", "/device/268000000/timedtalkgroup/104686")])
            calls.clear()
            status, _ = self.req("POST", "/api/bm", dict(body, parts=[]), {"X-Dmrdash-Pin": "4321"})
            self.assertEqual(status, 400)  # a bad new schedule is refused before anything is removed
            self.assertEqual([c for c in calls if c[0] != "GET"], [])
        finally:
            dmrdash.bm_request = real

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

    def test_master_request(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        dmrdash.BM["masters"] = [{"id": 2682, "country": "PT"}, {"id": 2141, "country": "ES"}]
        s = json.loads(self.req("GET", "/api/state")[1])
        self.assertEqual((s["masters"][0]["id"], s["master"]["can"], s["master"]["target"]), (2682, True, None))
        for mid in (2999, "2141", 99999, None, True):  # not a listed master, or not a number
            self.assertEqual(self.req("POST", "/api/bm", {"action": "master", "id": mid}, pin)[0], 400)
        self.assertFalse(os.path.exists(dmrdash.MASTER_PATH))
        status, data = self.req("POST", "/api/bm", {"action": "master", "id": 2141}, pin)
        self.assertEqual(status, 200, data)
        with open(dmrdash.MASTER_PATH) as f:
            self.assertEqual(f.read(), "2141\n")
        self.assertEqual(self.req("POST", "/api/bm", {"action": "master", "id": 2682}, pin)[0], 400)  # one move at a time
        self.assertEqual(self.req("POST", "/api/bm", {"action": "power", "what": "reboot"}, pin)[0], 400)
        with dmrdash.LOCK:
            dmrdash.master_seen(2682)  # BM still sees the old master
            self.assertEqual(dmrdash.MASTER["target"], 2141)
            dmrdash.master_seen(2141)
        self.assertIsNone(dmrdash.MASTER["target"])
        self.assertEqual(dmrdash.STATE["events"][0]["text"], "Moved to BrandMeister master 2141")

    def test_master_helper_edits_only_dmr_network_address(self):
        spec = importlib.util.spec_from_file_location("master", os.path.join(ROOT, "master.py"))
        ms = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ms)
        ini = os.path.join(TMP, "MMDVM-master.ini")
        with open(ini, "w") as f:
            f.write("[Info]\nAddress=Lisbon\n[DMR Network]\nEnable=1\nAddress=193.137.237.12\nPort=62031\n[POCSAG Network]\nAddress=x\n")
        self.assertTrue(ms.set_master(ini, 2141))
        with open(ini) as f:
            self.assertEqual(f.read(), "[Info]\nAddress=Lisbon\n[DMR Network]\nEnable=1\n"
                             "Address=2141.master.brandmeister.network\nPort=62031\n[POCSAG Network]\nAddress=x\n")
        self.assertFalse(ms.set_master(ini, 2141))
        ms.REQUEST = os.path.join(TMP, "master.test")
        for raw, ok in (("2682\n", True), ("0123", False), ("2682 evil.example", False), ("268", False)):
            with open(ms.REQUEST, "w") as f:
                f.write(raw)
            if ok:
                self.assertEqual(ms.read_request(), 2682)
            else:
                with self.assertRaises(SystemExit):
                    ms.read_request()

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
