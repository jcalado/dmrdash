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
        self.assertLess(s["booted"], s["now"])  # the Pi row's uptime, from /proc/uptime

    def test_log(self):
        status, data = self.req("GET", "/api/log?after=0")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["lines"], [])
        self.assertEqual(self.req("GET", "/api/log?after=x")[0], 400)

    def test_unknown_host_refused(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "192.168.1.5:8080"})[0], 200)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "[fe80::1]:8080"})[0], 200)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "deadbeef:8080"})[0], 403)  # hex, but not an address
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "1.2.3.4.5"})[0], 403)

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
        for mid in (999, "2141", 99999, None, True):  # not four digits, or not a number
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

    def test_master_from_startup_log(self):
        # BrandMeister lags a move by minutes; DMRHost's own startup listing names the master at once
        dmrdash.BM["device"] = {"lastKnownMaster": 2141}
        dmrdash.BM["masters"] = [{"id": 2682, "country": "PT", "address": "193.137.237.12"}]
        for line in ("Display Parameters", "    Address: 127.0.0.1", "DMR Network Parameters",
                     "    Address: 2681.master.brandmeister.network", "    Port: 62031", "Info Parameters"):
            dmrdash.handle_line(2000, "I", line)
        self.assertEqual(json.loads(self.req("GET", "/api/state")[1])["master"]["current"], 2681)
        dmrdash.handle_line(2001, "I", "DMR Network Parameters")
        dmrdash.handle_line(2001, "I", "    Address: 193.137.237.12")  # an IP: looked up in BM's list
        with dmrdash.LOCK:
            self.assertEqual(dmrdash.current_master(), 2682)
        dmrdash.STATE["network"]["address"] = None
        dmrdash.BM["device"] = None

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

    def test_dmrhost_restart(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        status, data = self.req("POST", "/api/bm", {"action": "power", "what": "dmrhost"}, pin)
        self.assertEqual(status, 200, data)
        with open(dmrdash.POWER_PATH) as f:
            self.assertEqual(f.read(), "dmrhost\n")
        asked = dmrdash.RESTART["requested"]
        self.assertEqual(json.loads(self.req("GET", "/api/state")[1])["restart"], asked)
        # one DMRHost restart at a time, whichever way it is asked for
        for body in ({"action": "power", "what": "dmrhost"}, {"action": "rflevel", "pct": 50}, {"action": "master", "id": 2141}):
            status, data = self.req("POST", "/api/bm", body, pin)
            self.assertEqual((status, json.loads(data)["error"]), (400, "DMRHost is already restarting"), body)
        dmrdash.handle_line(asked - 5, "M", "DMRHost-20260101 is running")  # from before the request
        self.assertEqual(dmrdash.RESTART["requested"], asked)
        dmrdash.handle_line(asked + 3, "M", "DMRHost-20260101 is running")
        self.assertIsNone(dmrdash.RESTART["requested"])
        self.assertEqual(dmrdash.STATE["events"][0]["text"], "DMRHost restarted")

    def test_rflevel_request(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        for line in ("Modem Parameters", "    RX Offset: 0Hz", "    RF Level: 100.0%"):
            dmrdash.handle_line(3000, "I", line)
        s = json.loads(self.req("GET", "/api/state")[1])
        self.assertEqual(s["rflevel"], {"level": 100.0, "can": True, "target": None})
        for pct in (0, 101, "50", 50.5, True, None, 100):  # 100: already running at it
            self.assertEqual(self.req("POST", "/api/bm", {"action": "rflevel", "pct": pct}, pin)[0], 400, pct)
        self.assertFalse(os.path.exists(dmrdash.RFLEVEL_PATH))
        self.assertEqual(self.req("POST", "/api/bm", {"action": "rflevel", "pct": 25}, pin)[0], 200)
        with open(dmrdash.RFLEVEL_PATH) as f:
            self.assertEqual(f.read(), "25\n")
        self.assertEqual(self.req("POST", "/api/bm", {"action": "tuneStart"}, pin)[0], 400)
        dmrdash.handle_line(3010, "I", "    RF Level: 25.0%")
        self.assertIsNone(dmrdash.RFLEVEL["target"])
        self.assertEqual(dmrdash.STATE["events"][0]["text"], "RF power now 25%")

    def test_rflevel_helper_edits_only_modem_rflevel(self):
        spec = importlib.util.spec_from_file_location("rflevel", os.path.join(ROOT, "rflevel.py"))
        rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rl)
        ini = os.path.join(TMP, "MMDVM-rf.ini")
        with open(ini, "w") as f:
            f.write("[General]\nRFLevel=7\n[Modem]\nRXOffset=0\nRFLevel=100\n# DMRTXLevel=50\n[DMR]\nColorCode=1\n")
        self.assertTrue(rl.set_level(ini, 25))
        with open(ini) as f:
            self.assertEqual(f.read(), "[General]\nRFLevel=7\n[Modem]\nRXOffset=0\nRFLevel=25\n# DMRTXLevel=50\n[DMR]\nColorCode=1\n")
        self.assertFalse(rl.set_level(ini, 25))
        rl.REQUEST = os.path.join(TMP, "rflevel.test")
        for raw, ok in (("25\n", True), ("0", False), ("101", False), ("-5", False), ("25.5", False), ("25 x", False)):
            with open(rl.REQUEST, "w") as f:
                f.write(raw)
            if ok:
                self.assertEqual(rl.read_request(), 25)
            else:
                with self.assertRaises(SystemExit):
                    rl.read_request()

    def ws_open(self, path, origin="http://localhost"):
        """A raw WebSocket handshake: (socket, status line, the bytes after the headers)."""
        import socket
        s = socket.create_connection(("127.0.0.1", self.srv.server_port), timeout=5)
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return s, head.decode(), rest

    def test_shell(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        self.assertEqual(self.req("POST", "/api/bm", {"action": "shellTicket"}, pin)[0], 400)  # off by default
        self.assertFalse(json.loads(self.req("GET", "/api/state")[1])["ssh"]["shell"])
        real = dmrdash.can_shell, dmrdash.shell_cmd
        dmrdash.can_shell = lambda: True
        dmrdash.shell_cmd = lambda: ["sh", "-c", "stty -echo; printf ready; read x; printf 'got:%s' \"$x\""]
        try:
            self.assertEqual(self.req("POST", "/api/bm", {"action": "shellTicket"})[0], 401)  # the PIN applies
            ticket = lambda: json.loads(self.req("POST", "/api/bm", {"action": "shellTicket"}, pin)[1])["ticket"]
            t = ticket()
            s, status, _ = self.ws_open(f"/api/shell?t={t}&cols=80&rows=24", origin="http://evil.example")
            self.assertIn(" 400 ", status)  # another site's page can't open one
            s.close()
            s, status, rest = self.ws_open(f"/api/shell?t={t}&cols=80&rows=24")
            self.assertIn(" 101 ", status)
            # the RFC 6455 example key, so the accept value is known
            self.assertIn("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", status)

            def frame(op, data):
                mask = b"\x01\x02\x03\x04"
                return bytes([0x80 | op, 0x80 | len(data)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            got = rest
            while b"ready" not in got:
                got += s.recv(4096)
            s.sendall(frame(1, b'{"cols": 100, "rows": 30}') + frame(2, b"hello\r"))
            while b"got:hello" not in got:
                chunk = s.recv(4096)
                self.assertTrue(chunk, got)
                got += chunk
            s.close()
            s, status, _ = self.ws_open(f"/api/shell?t={t}&cols=80&rows=24")
            self.assertIn(" 403 ", status)  # a ticket works once
            s.close()
        finally:
            dmrdash.can_shell, dmrdash.shell_cmd = real

    def test_ssh_user_must_be_a_plain_name(self):
        for u, ok in (("joel", "joel"), ("pi-star", "pi-star"), ("-oProxyCommand=x", ""), ("joel@evil", ""), ("Joel", ""), (7, "")):
            dmrdash.CONF["ssh_user"] = u
            self.assertEqual(dmrdash.ssh_user(), ok, u)
        dmrdash.CONF["ssh_user"] = ""

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

    def test_update_check(self):
        repo = os.path.join(TMP, "Packages")
        with open(repo, "w") as f:
            f.write("Package: dmrdash\nVersion: 1.10.0\n\nPackage: other\nVersion: 99.0\n\n"
                    "Package: dmrdash\nVersion: 1.9.2\n")
        dmrdash.PACKAGES_URL = "file://" + repo
        self.assertEqual(dmrdash.update_check()["latest"], "1.10.0")  # by number, not as text
        with dmrdash.LOCK:
            self.assertTrue(dmrdash.update_available())
        self.assertEqual(dmrdash.STATE["events"][0]["text"], "dmrdash 1.10.0 is available")
        dmrdash.PACKAGES_URL = "file://" + os.path.join(TMP, "missing")
        r = dmrdash.update_check()
        self.assertFalse(r["ok"])
        self.assertEqual(dmrdash.UPDATE["latest"], "1.10.0")  # a failed check forgets nothing
        self.assertTrue(json.loads(self.req("GET", "/api/state")[1])["update"]["error"].startswith("Can't check"))
        dmrdash.UPDATE.update(latest=None, error=None, checked=None)

    def test_update_request(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        dmrdash.DPKG_LIST = os.path.join(TMP, "missing.list")
        dmrdash.UPDATE["latest"] = "99.0.0"
        status, data = self.req("POST", "/api/bm", {"action": "update"}, pin)
        self.assertEqual((status, json.loads(data)["error"]), (400, "Updates need dmrdash installed as a package; rerun install.sh"))
        dmrdash.DPKG_LIST = os.path.join(TMP, "dmrdash.list")
        open(dmrdash.DPKG_LIST, "w").close()
        u = json.loads(self.req("GET", "/api/state")[1])["update"]
        self.assertEqual((u["can"], u["available"], u["updating"]), (True, True, False))
        dmrdash.UPDATE["latest"] = dmrdash.VERSION
        self.assertEqual(self.req("POST", "/api/bm", {"action": "update"}, pin)[0], 400)  # nothing newer
        self.assertFalse(os.path.exists(dmrdash.UPDATE_PATH))
        dmrdash.UPDATE["latest"] = "99.0.0"
        status, data = self.req("POST", "/api/bm", {"action": "update"}, pin)
        self.assertEqual(status, 200, data)
        with open(dmrdash.UPDATE_PATH) as f:
            self.assertEqual(f.read(), "dmrdash\n")
        self.assertTrue(json.loads(self.req("GET", "/api/state")[1])["update"]["updating"])
        status, data = self.req("POST", "/api/bm", {"action": "update"}, pin)
        self.assertEqual((status, json.loads(data)["error"]), (400, "dmrdash is already updating"))

        # the helper's reports: running, then failed
        def report(**st):
            with open(dmrdash.UPDATE_STATUS, "w") as f:
                json.dump(dict(st, ts=dmrdash.time.time()), f)
            os.utime(dmrdash.UPDATE_STATUS, (st.get("n", 0) + 1e9, st.get("n", 0) + 1e9))  # a new mtime each time
            with dmrdash.LOCK:
                dmrdash.read_update_status()
        report(state="running", old=dmrdash.VERSION, n=1)
        self.assertTrue(json.loads(self.req("GET", "/api/state")[1])["update"]["updating"])
        report(state="failed", old=dmrdash.VERSION, new=dmrdash.VERSION, error="E: Unable to locate package dmrdash", n=2)
        u = json.loads(self.req("GET", "/api/state")[1])["update"]
        self.assertEqual((u["updating"], u["failed"]), (False, "E: Unable to locate package dmrdash"))
        self.assertTrue(dmrdash.STATE["events"][0]["text"].startswith("The dmrdash update failed: E: Unable"))
        # asking again clears the failure; this time it lands
        self.assertEqual(self.req("POST", "/api/bm", {"action": "update"}, pin)[0], 200)
        self.assertIsNone(json.loads(self.req("GET", "/api/state")[1])["update"]["failed"])
        report(state="done", old=dmrdash.VERSION, new="99.0.0", n=3)
        self.assertEqual(dmrdash.STATE["events"][0]["text"], f"dmrdash updated from {dmrdash.VERSION} to 99.0.0")
        self.assertFalse(json.loads(self.req("GET", "/api/state")[1])["update"]["updating"])
        dmrdash.UPDATE.update(latest=None, requested=None, failed=None)

    def test_update_report_from_before_a_restart(self):
        # a new dashboard process mentions the update that restarted it, but not an old one
        with open(dmrdash.UPDATE_STATUS + ".x", "w") as f:
            json.dump({"state": "done", "old": "1.0.0", "new": "1.1.0", "ts": dmrdash.time.time() - 7200}, f)
        os.replace(dmrdash.UPDATE_STATUS + ".x", dmrdash.UPDATE_STATUS)
        dmrdash.UPDATE.update(status=None, status_mtime=None)
        events = len(dmrdash.STATE["events"])
        with dmrdash.LOCK:
            dmrdash.read_update_status()
        self.assertEqual(len(dmrdash.STATE["events"]), events)
        dmrdash.UPDATE.update(status=None, status_mtime=None)

    def test_update_helper_takes_only_dmrdash(self):
        spec = importlib.util.spec_from_file_location("update", os.path.join(ROOT, "update.py"))
        up = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(up)
        up.REQUEST = os.path.join(TMP, "update.test")
        for raw, ok in (("dmrdash\n", True), ("dmrhost", False), ("dmrdash; reboot", False), ("", False)):
            with open(up.REQUEST, "w") as f:
                f.write(raw)
            if ok:
                up.read_request()
            else:
                with self.assertRaises(SystemExit):
                    up.read_request()
        with open(up.REQUEST, "w") as f:
            f.write("dmrdash")
        os.utime(up.REQUEST, (1, 1))
        with self.assertRaises(SystemExit):
            up.read_request()  # stale
        # refreshing only dmrdash's repo must not delete the other repos' lists
        self.assertIn("APT::Get::List-Cleanup=0", up.APT_UPDATE)

    INI = ("[General]\r\nCallsign=CT7BLE\r\nId=268051301\r\nTimeout=180\r\n\r\n[Modem]\r\n# Valid values are uart, udp\r\n"
           "Protocol=uart\r\nRXOffset=-350\r\nRFLevel=100\r\n\r\n[DMR]\r\nColorCode=1\r\n\r\n[DMR Network]\r\n"
           "Address=2682.master.brandmeister.network\r\nPassword = s3cret\r\n")

    def config_setup(self):
        """A CRLF MMDVM.ini with a password, and config.py pointed at the test's request and status files."""
        ini = os.path.join(TMP, "MMDVM-edit.ini")
        with open(ini, "w", newline="") as f:
            f.write(self.INI)
        for p in (ini + ".dmrdash-prev", dmrdash.CONFIG_STATUS, dmrdash.CONFIG_PATH):
            if os.path.exists(p):
                os.remove(p)
        dmrdash.CONF["mmdvm_ini"] = ini
        spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.py"))
        cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfg)
        cfg.REQUEST, cfg.STATUS, cfg.VIEW = dmrdash.CONFIG_PATH, dmrdash.CONFIG_STATUS, dmrdash.CONFIG_VIEW

        def kick():  # what the path unit does on the Pi, for the dashboard's read requests
            req = cfg.read_request()
            if req and req["op"] == "read":
                cfg.view(ini)
        dmrdash.CONFIG_KICK[0] = kick
        self.addCleanup(lambda: (dmrdash.CONFIG.update(id=None, op=None, auto=False), dmrdash.CONFIG_LAST.clear(),
                                 dmrdash.CONFIG_KICK.__setitem__(0, lambda: None)))
        return ini, cfg

    def config_read(self):
        status, data = self.req("POST", "/api/bm", {"action": "configRead"}, {"X-Dmrdash-Pin": "4321"})
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def helper_runs(self, cfg, ini, running=True):
        """config.py takes the request and reports done; DMRHost logs that it's running again."""
        req = cfg.read_request()
        self.assertIsNone(cfg.read_request())  # it empties the request once read
        digest = (cfg.apply if req["op"] == "apply" else cfg.restore)(ini, req)
        cfg.write_status(state="done", op=req["op"], hash=digest)
        if running:
            dmrdash.handle_line(dmrdash.time.time() + 1, "M", "DMRHost-20260101 is running")
        with dmrdash.LOCK:
            dmrdash.config_tick(dmrdash.time.time())
        return req

    def test_config_read_masks_passwords(self):
        ini, cfg = self.config_setup()
        self.assertEqual(self.req("POST", "/api/bm", {"action": "configRead"})[0], 401)  # the PIN applies
        status, data = self.req("POST", "/api/bm", {"action": "configRead"}, {"X-Dmrdash-Pin": "4321"})
        j = json.loads(data)
        self.assertNotIn("s3cret", data.decode())
        self.assertIn("\nPassword = " + dmrdash.MASK + "\n", j["text"])
        self.assertNotIn("\r", j["text"])
        with open(ini, "rb") as f:
            digest = dmrdash.sha256(f.read())
        self.assertNotIn(digest, data.decode())  # with the masked text, the file's hash would let the password be guessed
        self.assertEqual(j["keys"]["DMR"]["ColorCode"]["max"], 15)

    def test_config_write_undo(self):
        pin = {"X-Dmrdash-Pin": "4321"}
        ini, cfg = self.config_setup()
        j = self.config_read()
        write = lambda text, base=j["base"]: self.req("POST", "/api/bm", {"action": "configWrite", "text": text, "base": base}, pin)
        status, data = write(j["text"].replace("ColorCode=1", "ColorCode=16"))
        self.assertEqual((status, json.loads(data)["error"]), (400, "Line 13: A whole number from 0 to 15"))
        status, data = write(j["text"].replace("Password = " + dmrdash.MASK, "Secret=" + dmrdash.MASK))
        self.assertEqual(status, 400)  # a mask stands only for a password the file has
        status, data = write(j["text"], "0" * 64)
        self.assertEqual((status, json.loads(data)["stale"]), (400, True))
        self.assertEqual(write(j["text"])[0], 400)  # nothing changed
        with open(dmrdash.CONFIG_PATH) as f:
            self.assertEqual(f.read(), "")  # refused writes never reach config.py (reads leave it emptied)
        self.assertIsNone(dmrdash.CONFIG["id"])

        edited = j["text"].replace("Timeout=180", "Timeout=240").replace("RXOffset=-350", "# tuned\nRXOffset=-400")
        status, data = write(edited)
        self.assertEqual(status, 200, data)
        with open(dmrdash.CONFIG_PATH) as f:
            raw = f.read()
        self.assertNotIn("s3cret", raw)  # the password stays masked; config.py puts it back
        self.assertEqual(json.loads(raw)["op"], "apply")
        status, data = self.req("POST", "/api/bm", {"action": "rflevel", "pct": 25}, pin)
        self.assertEqual((status, json.loads(data)["error"]), (400, "Wait for the MMDVM.ini change to finish"))
        self.assertEqual(json.loads(self.req("GET", "/api/state")[1])["config"]["op"], "apply")

        with open(ini, "rb") as f:
            original = f.read()
        self.helper_runs(cfg, ini)
        with open(ini, "rb") as f:
            self.assertEqual(f.read(), original.replace(b"Timeout=180", b"Timeout=240").replace(b"RXOffset=-350", b"# tuned\r\nRXOffset=-400"))
        with open(ini + ".dmrdash-prev", "rb") as f:
            self.assertEqual(f.read(), original)
        s = json.loads(self.req("GET", "/api/state")[1])["config"]
        self.assertIsNone(s["op"])
        self.assertEqual((s["last"]["ok"], s["last"]["undo"]), (True, True))
        self.assertNotIn("hash", s["last"])  # /api/state needs no PIN
        self.assertEqual(s["last"]["text"], "Wrote MMDVM.ini: Timeout 240 · RXOffset -400 · comments or layout. DMRHost is running with it.")

        status, data = self.req("POST", "/api/bm", {"action": "configUndo", "id": "nope"}, pin)
        self.assertEqual(status, 400)
        self.assertEqual(self.req("POST", "/api/bm", {"action": "configUndo", "id": s["last"]["id"]}, pin)[0], 200)
        self.helper_runs(cfg, ini)
        with open(ini, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(dmrdash.CONFIG_LAST["text"], "Put the previous MMDVM.ini back. DMRHost is running with it.")
        self.assertEqual(self.req("POST", "/api/bm", {"action": "configUndo", "id": dmrdash.CONFIG_LAST["id"]}, pin)[0], 400)

    def test_config_puts_old_file_back_when_dmrhost_stays_down(self):
        ini, cfg = self.config_setup()
        j = self.config_read()
        status, data = self.req("POST", "/api/bm", {"action": "configWrite", "text": j["text"].replace("RFLevel=100", "RFLevel=50"), "base": j["base"]},
                                {"X-Dmrdash-Pin": "4321"})
        self.assertEqual(status, 200, data)
        self.helper_runs(cfg, ini, running=False)
        self.assertEqual(dmrdash.CONFIG["op"], "apply")  # written, waiting for DMRHost
        with dmrdash.LOCK:
            dmrdash.config_tick(dmrdash.time.time() + dmrdash.CONFIG_WAIT + 1)
        self.assertEqual((dmrdash.CONFIG["op"], dmrdash.CONFIG["auto"]), ("restore", True))
        self.helper_runs(cfg, ini)
        with open(ini, newline="") as f:
            self.assertEqual(f.read(), self.INI)
        self.assertFalse(dmrdash.CONFIG_LAST["ok"])
        self.assertTrue(dmrdash.STATE["events"][0]["text"].startswith("DMRHost didn't come back with the edited MMDVM.ini"))

    def test_config_helper_refuses(self):
        ini, cfg = self.config_setup()
        with open(ini, "rb") as f:
            base = dmrdash.sha256(f.read())
        text = self.INI.replace("\r\n", "\n").replace("s3cret", dmrdash.MASK)

        def ask(**req):
            with open(cfg.REQUEST, "w") as f:
                json.dump(dict({"id": "abc", "ts": dmrdash.time.time(), "op": "apply", "base": base, "text": text}, **req), f)
        for bad in ({"ts": 1}, {"op": "delete"}, {"base": "x"}, {"text": None}, {"text": text + "[Bad\n"},
                    {"text": text.replace("Timeout=180", "Timeout=180\x1b")}, {"text": "x" * 70000}):
            ask(**bad)
            with self.assertRaises(SystemExit, msg=str(bad)[:60]):
                cfg.read_request()
        ask(base="0" * 64)
        with self.assertRaises(SystemExit):
            cfg.apply(ini, cfg.read_request())  # made against another file
        with open(dmrdash.CONFIG_STATUS) as f:
            self.assertEqual(json.load(f)["state"], "failed")
        ask(text=text.replace("ColorCode=1", "Passphrase=" + dmrdash.MASK))
        with self.assertRaises(SystemExit):
            cfg.apply(ini, cfg.read_request())  # a mask with no password behind it
        ask()
        with self.assertRaises(SystemExit):
            cfg.apply(ini, cfg.read_request())  # no change
        ask(op="restore")
        with self.assertRaises(SystemExit):
            cfg.restore(ini, cfg.read_request())  # nothing to put back
        with open(ini, newline="") as f:
            self.assertEqual(f.read(), self.INI)
        # a read of a file that isn't there comes back as the reason
        dmrdash.CONF["mmdvm_ini"] = ini + ".missing"
        cfg.view(ini + ".missing")
        with open(cfg.VIEW) as f:
            self.assertTrue(json.load(f)["error"].startswith("Can't read"))

if __name__ == "__main__":
    unittest.main()
