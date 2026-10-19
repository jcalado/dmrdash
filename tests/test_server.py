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


if __name__ == "__main__":
    unittest.main()
