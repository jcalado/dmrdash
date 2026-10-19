#!/usr/bin/env python3
"""dmrdash - small LAN dashboard for BrandMeister DMRHost.

Follows the DMRHost journal for live call activity and polls the BrandMeister
API for device status and talkgroup subscriptions. Static talkgroups can be
managed with a SelfCare API key, which never leaves this process.

Standard library only; all state is kept in memory. The one exception is talkgroup
presets, a small JSON file written only when a preset is saved or deleted.

https://github.com/jcalado/dmrdash - MIT licence
"""

import collections
import hmac
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_PATH = os.environ.get("DMRDASH_CONF", "/etc/dmrdash.conf")
HERE = os.path.dirname(os.path.abspath(__file__))
BM_API = "https://api.brandmeister.network/v2"
VERSION = "1.0.0"
USER_AGENT = "dmrdash/" + VERSION

DEFAULTS = {
    "device_id": 0,
    "api_key": "",
    "listen": "0.0.0.0",
    "port": 8080,
    "user_csv": "/usr/local/etc/user.csv",
    "unit": "dmrhost",
    "lastheard_size": 50,
    "bm_poll_seconds": 60,
    "allowed_hosts": [],
    "pin": "",  # when set, talkgroup changes need it (sent as X-Dmrdash-Pin)
}


def load_conf():
    conf = dict(DEFAULTS)
    with open(CONF_PATH) as f:
        conf.update(json.load(f))
    conf["device_id"] = int(conf["device_id"])
    conf["pin"] = str(conf["pin"] or "")
    return conf


CONF = load_conf()
LOCK = threading.Lock()
ACTION_LOCK = threading.Lock()  # one BrandMeister change sequence at a time (a preset is several calls)


# --- talkgroup presets ------------------------------------------------------

PRESETS_PATH = os.path.join(os.environ.get("STATE_DIRECTORY", "/var/lib/dmrdash"), "presets.json")
MAX_PRESETS = 12


def load_presets():
    try:
        with open(PRESETS_PATH) as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, dict) and isinstance(p.get("name"), str)][:MAX_PRESETS]
    except (OSError, ValueError):
        return []


PRESETS = load_presets()


def save_presets():
    tmp = PRESETS_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(PRESETS, f, indent=1)
        os.replace(tmp, PRESETS_PATH)
    except OSError as e:
        raise BMError(f"Can't save presets on the Pi: {e.strerror or e}")


# --- callsign lookup: binary search on the sorted radioid.net CSV ---------

class UserDB:
    """Looks up IDs in the radioid CSV on disk instead of loading it into RAM."""

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.cache = {}

    def find(self, dmr_id):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            return None
        if mtime != self.mtime:
            self.mtime, self.cache = mtime, {}
        if dmr_id in self.cache:
            return self.cache[dmr_id]
        result = self._search(dmr_id)
        if len(self.cache) > 2000:
            self.cache.clear()
        self.cache[dmr_id] = result
        return result

    def _search(self, dmr_id):
        with open(self.path, "rb") as f:
            f.seek(0, os.SEEK_END)
            lo, hi = 0, f.tell()
            while lo < hi:
                mid = (lo + hi) // 2
                f.seek(mid)
                if mid:
                    f.readline()  # skip to the start of the next full line
                line = f.readline()
                if not line:
                    hi = mid
                    continue
                key = line.split(b",", 1)[0]
                if not key.isdigit():  # header row
                    lo = mid + 1
                    continue
                if int(key) < dmr_id:
                    lo = mid + 1
                else:
                    hi = mid
            f.seek(lo)
            if lo:
                f.readline()
            for _ in range(3):
                line = f.readline()
                if not line:
                    break
                parts = line.decode("utf-8", "replace").rstrip("\r\n").split(",")
                if parts[0].isdigit() and int(parts[0]) == dmr_id:
                    return {"callsign": parts[1] if len(parts) > 1 else "",
                            "name": parts[2] if len(parts) > 2 else ""}
        return None


USERS = UserDB(CONF["user_csv"])


# --- live state ------------------------------------------------------------

def empty_slot():
    return {"active": False, "source": None, "src": None, "dst": None,
            "group": True, "start": None, "alias": None}


STATE = {
    "started": time.time(),
    "host": {"status": "unknown", "since": None, "version": None},
    "modem": {"status": "unknown", "description": None, "since": None},
    "network": {"status": "unknown", "since": None},
    "slots": {1: empty_slot(), 2: empty_slot()},
    "lastheard": collections.deque(maxlen=int(CONF["lastheard_size"])),
    "events": collections.deque(maxlen=60),  # the page collapses retry storms, so keep enough to show what came before one
}

BM = {"device": None, "profile": None, "talkgroups": {}, "updated": None,
      "error": None, "tg_names_updated": 0}

SLOT_RE = re.compile(r"^DMR Slot (\d), (.*)$")
SRC_DST = r"from (\S+) to (TG )?(\S+?)"
RE_START = re.compile(r"received (RF|network) (?:voice header|late entry) " + SRC_DST + r"$")
RE_END = re.compile(
    r"(?:received (RF|network) end of voice transmission|(RF) voice transmission lost) "
    + SRC_DST + r", ([\d.]+) seconds(?:, (\d+)% packet loss)?, BER: ([\d.]+)%"
    r"(?:, RSSI: (-?\d+)/(-?\d+)/(-?\d+) dBm)?$")
RE_WATCHDOG = re.compile(r"network watchdog has expired(?:, ([\d.]+) seconds, (\d+)% packet loss, BER: ([\d.]+)%)?$")
RE_ALIAS = re.compile(r'Talker Alias "(.*)"$')
RE_REJECT = re.compile(r"RF user (\d+) rejected(?: for using TG (\d+))?$")
RE_LOGLINE = re.compile(r"^DMRHost: ([DMIWEF]): \S+ \S+ (.*)$")

# Raw log lines for the page's live log view: a RAM ring buffer fed by the same journal stream
# (the journal itself is volatile), so showing the log costs no SD card writes.
LOG = collections.deque(maxlen=400)
LOG_SEQ = [0]


def add_log(ts, level, text):
    LOG_SEQ[0] += 1
    LOG.append({"n": LOG_SEQ[0], "ts": ts, "l": level, "t": text})


def who(dmr_id):
    try:
        n = int(dmr_id)
    except (TypeError, ValueError):
        return {"id": dmr_id, "callsign": None, "name": None}
    u = USERS.find(n) or {}
    return {"id": n, "callsign": u.get("callsign"), "name": u.get("name")}


def add_event(ts, level, text):
    STATE["events"].appendleft({"ts": ts, "level": level, "text": text})


def handle_line(ts, level, msg):
    m = SLOT_RE.match(msg)
    if m:
        handle_slot(ts, int(m.group(1)), m.group(2))
        return
    if msg.startswith("DMRHost-") and msg.endswith(" is starting"):
        STATE["host"].update(status="starting", since=ts, version=msg.split()[0])
        STATE["slots"] = {1: empty_slot(), 2: empty_slot()}
        STATE["network"].update(status="connecting", since=ts)
        STATE["modem"].update(status="opening", since=ts)
    elif msg.endswith(" is running"):
        STATE["host"].update(status="running", since=ts)
    elif "exited on receipt of" in msg or msg.endswith("is exiting"):
        STATE["host"].update(status="stopped", since=ts)
        STATE["network"].update(status="down", since=ts)
        STATE["modem"].update(status="closed", since=ts)
    elif msg.startswith("MMDVM protocol version"):
        desc = msg.split("description: ", 1)[-1]
        STATE["modem"].update(status="ok", description=desc, since=ts)
    elif msg == "DMR, Logged into the master successfully":
        STATE["network"].update(status="connected", since=ts)
        add_event(ts, "M", "Logged into the BrandMeister master")
    elif msg.startswith("DMR, Login to the master has failed"):
        STATE["network"].update(status="login failed", since=ts)
    elif msg.startswith("DMR, Connection to the master has timed out") or "Socket has failed" in msg:
        STATE["network"].update(status="reconnecting", since=ts)
    elif msg == "DMR, Master is closing down":
        STATE["network"].update(status="master closing", since=ts)
    elif msg == "DMR, Closing DMR Network":
        STATE["network"].update(status="down", since=ts)
    elif "modem" in msg.lower() and level in ("E", "W"):
        STATE["modem"].update(status="error", since=ts)
    if level in ("E", "W", "F"):
        add_event(ts, level, msg)


def finish_call(ts, slot, source, src, dst, group, secs, loss, ber, rssi=None, lost=False):
    s = STATE["slots"][slot]
    STATE["lastheard"].appendleft({
        "ts": ts, "slot": slot, "source": source.lower() if source else s["source"],
        "who": who(src), "dst": dst, "group": group, "to": None if group else who(dst),
        "duration": secs, "loss": loss, "ber": ber, "rssi": rssi,
        "alias": s["alias"], "lost": lost,
    })
    STATE["slots"][slot] = empty_slot()


def handle_slot(ts, slot, rest):
    s = STATE["slots"][slot]
    m = RE_START.match(rest)
    if m:
        src, dst = m.group(2), m.group(4)
        if s["active"] and s["src"] == src and s["dst"] == dst:
            return  # late entry for the call we already track
        STATE["slots"][slot] = {"active": True, "source": m.group(1).lower(), "src": src,
                                "who": who(src), "dst": dst, "group": bool(m.group(3)),
                                "to": None if m.group(3) else who(dst), "start": ts, "alias": None}
        return
    m = RE_END.match(rest)
    if m:
        source = m.group(1) or m.group(2)
        rssi = None
        if m.group(9):
            rssi = {"min": -abs(int(m.group(9))), "max": -abs(int(m.group(10))), "avg": -abs(int(m.group(11)))}
        finish_call(ts, slot, source, m.group(3), m.group(5), bool(m.group(4)),
                    float(m.group(6)), int(m.group(7)) if m.group(7) else None,
                    float(m.group(8)), rssi, lost=bool(m.group(2)))
        return
    m = RE_WATCHDOG.match(rest)
    if m:
        if s["active"]:
            finish_call(ts, slot, "network", s["src"], s["dst"], s["group"],
                        float(m.group(1)) if m.group(1) else round(ts - s["start"], 1),
                        int(m.group(2)) if m.group(2) else None,
                        float(m.group(3)) if m.group(3) else None, lost=True)
        return
    m = RE_ALIAS.match(rest)
    if m:
        if s["active"] and m.group(1).strip():
            s["alias"] = m.group(1).strip()
        return
    m = RE_REJECT.match(rest)
    if m:
        txt = f"Slot {slot}: RF user {m.group(1)} rejected" + (f" for TG {m.group(2)}" if m.group(2) else "")
        add_event(ts, "W", txt)
        return
    if "has timed out" in rest:
        add_event(ts, "W", f"Slot {slot}: {rest}")


def follow_journal():
    cmd = ["journalctl", "-u", CONF["unit"], "-o", "json", "-f", "-n", "5000", "--no-pager"]
    while True:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for raw in proc.stdout:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                msg = entry.get("MESSAGE")
                if not isinstance(msg, str):
                    continue
                m = RE_LOGLINE.match(msg)
                if not m:
                    continue
                ts = int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1e6
                with LOCK:
                    add_log(ts, m.group(1), m.group(2))
                    handle_line(ts, m.group(1), m.group(2))
            proc.wait()
        except Exception as e:  # keep following no matter what
            with LOCK:
                add_event(time.time(), "E", f"dashboard: journal reader failed: {e}")
        time.sleep(5)


# --- BrandMeister API -------------------------------------------------------

class BMError(Exception):
    pass


def bm_request(method, path, body=None, auth=False):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if auth:
        if not CONF["api_key"]:
            raise BMError("No SelfCare API key configured in " + CONF_PATH)
        headers["Authorization"] = "Bearer " + CONF["api_key"]
    req = urllib.request.Request(BM_API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise BMError(f"BrandMeister refused the API key (HTTP {e.code})")
        raise BMError(f"BrandMeister API error HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BMError(f"Cannot reach BrandMeister API: {e}")
    return json.loads(raw) if raw.strip() else {}


def bm_refresh(names=False):
    dev = CONF["device_id"]
    try:
        device = bm_request("GET", f"/device/{dev}")
        profile = bm_request("GET", f"/device/{dev}/profile")
        tgs = None
        if names or time.time() - BM["tg_names_updated"] > 86400:
            tgs = bm_request("GET", "/talkgroup")
        with LOCK:
            BM.update(device=device, profile=profile, updated=time.time(), error=None)
            if tgs:
                BM["talkgroups"] = tgs
                BM["tg_names_updated"] = time.time()
    except (BMError, ValueError) as e:
        with LOCK:
            BM["error"] = str(e)


def poll_bm():
    while True:
        bm_refresh()
        time.sleep(max(15, int(CONF["bm_poll_seconds"])))


def parse_slot_tg(body):
    try:
        slot, tg = int(body["slot"]), int(body["talkgroup"])
    except (KeyError, TypeError, ValueError):
        raise BMError("slot and talkgroup must be numbers")
    if slot not in (1, 2):
        raise BMError("slot must be 1 or 2")
    if not 1 <= tg <= 16777215:
        raise BMError("talkgroup out of range")
    return slot, tg


def tg_body(slot, tg):
    # The published v2 spec names the field "talkgroup", but the live server rejects that with
    # HTTP 455 "The group field is required." Send both so either reading works.
    return {"slot": slot, "group": tg, "talkgroup": tg}


def bm_action(body):
    dev = CONF["device_id"]
    action = body.get("action")
    if action == "add":
        slot, tg = parse_slot_tg(body)
        bm_request("POST", f"/device/{dev}/talkgroup", tg_body(slot, tg), auth=True)
    elif action == "remove":
        slot, tg = parse_slot_tg(body)
        bm_request("DELETE", f"/device/{dev}/talkgroup/{slot}/{tg}", auth=True)
    elif action in ("dropDynamicGroups", "dropCallRoute"):
        slot = int(body.get("slot", 0))
        if slot not in (1, 2):
            raise BMError("slot must be 1 or 2")
        bm_request("GET", f"/device/{dev}/action/{action}/{slot}", auth=True)
    elif action == "dropAutoStaticGroup":
        bm_request("GET", f"/device/{dev}/action/dropAutoStaticGroup", auth=True)
    elif action == "clearEvents":  # local only: empties the in-memory list, never the journal
        with LOCK:
            STATE["events"].clear()
        return {"ok": True}
    elif action == "presetSave":
        return preset_save(body)
    elif action == "presetDelete":
        return preset_delete(body)
    elif action == "presetApply":
        return preset_apply(body)
    else:
        raise BMError("unknown action")
    bm_refresh()


def preset_name(body):
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BMError("Give the preset a name")
    name = " ".join(name.split())
    if len(name) > 32:
        raise BMError("Preset names are limited to 32 characters")
    return name


def statics_now():
    """Fresh static TGs per slot, straight from BrandMeister; never diff against stale data."""
    bm_refresh()
    with LOCK:
        if BM["error"]:
            raise BMError(BM["error"])
        subs = (BM["profile"] or {}).get("staticSubscriptions") or []
    cur = {1: set(), 2: set()}
    for t in subs:
        if int(t.get("slot", 0)) in cur:
            cur[int(t["slot"])].add(int(t["talkgroup"]))
    return cur


def preset_save(body):
    name, cur = preset_name(body), statics_now()
    preset = {"name": name, "slots": {str(s): sorted(cur[s]) for s in (1, 2)}, "drop_dynamic": bool(body.get("drop_dynamic"))}
    with LOCK:
        rest = [p for p in PRESETS if p["name"].lower() != name.lower()]
        if len(rest) >= MAX_PRESETS:
            raise BMError(f"Up to {MAX_PRESETS} presets; delete one first")
        PRESETS[:] = rest + [preset]
        save_presets()
    return {"ok": True, "preset": preset}


def preset_delete(body):
    name = preset_name(body)
    with LOCK:
        PRESETS[:] = [p for p in PRESETS if p["name"].lower() != name.lower()]
        save_presets()
    return {"ok": True}


def preset_apply(body):
    """Make the static TGs match a preset (by name) or explicit slots (used by Undo)."""
    if "slots" in body:
        raw, drop = body["slots"], False
    else:
        name = preset_name(body)
        with LOCK:
            p = next((p for p in PRESETS if p["name"].lower() == name.lower()), None)
        if not p:
            raise BMError("No preset called " + name)
        raw, drop = p["slots"], p.get("drop_dynamic", False)
    try:
        target = {s: {int(t) for t in raw.get(str(s), [])} for s in (1, 2)}
    except (AttributeError, TypeError, ValueError):
        raise BMError("slots must map 1 and 2 to talkgroup lists")
    for tgs in target.values():
        if any(not 1 <= t <= 16777215 for t in tgs) or len(tgs) > 30:
            raise BMError("talkgroup out of range")
    dev = CONF["device_id"]
    cur = statics_now()
    added, removed, failed = [], [], []
    for s in (1, 2):
        for tg in sorted(target[s] - cur[s]):
            try:
                bm_request("POST", f"/device/{dev}/talkgroup", tg_body(s, tg), auth=True)
                added.append([s, tg])
            except BMError as e:
                failed.append(f"add TG {tg} on TS{s}: {e}")
        for tg in sorted(cur[s] - target[s]):
            try:
                bm_request("DELETE", f"/device/{dev}/talkgroup/{s}/{tg}", auth=True)
                removed.append([s, tg])
            except BMError as e:
                failed.append(f"remove TG {tg} from TS{s}: {e}")
        if drop:
            try:
                bm_request("GET", f"/device/{dev}/action/dropDynamicGroups/{s}", auth=True)
            except BMError as e:
                failed.append(f"drop dynamics on TS{s}: {e}")
    bm_refresh()
    return {"ok": not failed, "added": added, "removed": removed, "failed": failed,
            "previous": {str(s): sorted(cur[s]) for s in (1, 2)}}


# --- HTTP -------------------------------------------------------------------

def local_ip():
    """Primary LAN address (no packets are sent; UDP connect only picks a route)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def snapshot():
    with LOCK:
        names = BM["talkgroups"]
        profile = BM["profile"] or {}
        return {
            "now": time.time(),
            "version": VERSION,
            "device_id": CONF["device_id"],
            "hostname": os.uname().nodename,
            "ip": local_ip(),
            "can_manage": bool(CONF["api_key"]),
            "pin": bool(CONF["pin"]),
            "host": dict(STATE["host"]),
            "modem": dict(STATE["modem"]),
            "network": dict(STATE["network"]),
            "slots": {k: dict(v) for k, v in STATE["slots"].items()},
            "lastheard": list(STATE["lastheard"]),
            "events": list(STATE["events"]),
            "bm": {
                "device": BM["device"],
                "static": profile.get("staticSubscriptions") or [],
                "dynamic": profile.get("dynamicSubscriptions") or [],
                "timed": profile.get("timedSubscriptions") or [],
                "autoStatic": profile.get("autoStatic"),
                "updated": BM["updated"],
                "error": BM["error"],
            },
            "tg_names": names,
            "presets": [dict(p) for p in PRESETS],
        }


PIN_FAILS = collections.deque(maxlen=5)  # times of the last wrong PINs


def pin_check(given):
    """None when the change may go ahead, else (status, body). Five wrong PINs in a minute lock changes for that minute."""
    if not CONF["pin"]:
        return None
    now = time.time()
    with LOCK:
        if len(PIN_FAILS) == PIN_FAILS.maxlen and now - PIN_FAILS[0] < 60:
            return 429, {"error": "Too many wrong PINs. Try again in a minute."}
        if given and hmac.compare_digest(given.encode(), CONF["pin"].encode()):
            return None
        if given:
            PIN_FAILS.append(now)
    return 401, {"error": "Wrong PIN" if given else "PIN required", "pin": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "dmrdash"

    def log_message(self, fmt, *args):
        pass  # keep the journal quiet

    def host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        allowed = {"localhost", "dmrhost", "dmrhost.local", os.uname().nodename.lower(),
                   os.uname().nodename.lower() + ".local"} | {h.lower() for h in CONF["allowed_hosts"]}
        # IP literals are fine; unknown hostnames are refused (DNS rebinding guard)
        return host in allowed or re.fullmatch(r"[\d.]+|[0-9a-f:]+", host or "x") is not None

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "unknown host"})
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self.send(200, snapshot())
        if self.path.startswith("/api/log"):
            m = re.fullmatch(r"/api/log(?:\?after=(\d+))?", self.path)
            if not m:
                return self.send(400, {"error": "bad request"})
            after = int(m.group(1) or 0)
            with LOCK:
                body = {"lines": [x for x in LOG if x["n"] > after], "last": LOG_SEQ[0]}
            return self.send(200, body)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.host_ok():
            return self.send(403, {"error": "unknown host"})
        # custom header + JSON body: browsers cannot send this cross-site without CORS
        if self.path != "/api/bm" or self.headers.get("X-Dmrdash") != "1" \
                or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(400, {"error": "bad request"})
        denied = pin_check(self.headers.get("X-Dmrdash-Pin") or "")
        if denied:
            return self.send(*denied)
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise TypeError
            with ACTION_LOCK:
                result = bm_action(body)
        except BMError as e:
            return self.send(400, {"error": str(e)})
        except (ValueError, TypeError):
            return self.send(400, {"error": "invalid JSON"})
        self.send(200, result or {"ok": True})


def main():
    threading.Thread(target=follow_journal, daemon=True).start()
    threading.Thread(target=poll_bm, daemon=True).start()
    srv = ThreadingHTTPServer((CONF["listen"], int(CONF["port"])), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
