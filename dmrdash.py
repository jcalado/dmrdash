#!/usr/bin/env python3
"""dmrdash - small LAN dashboard for BrandMeister DMRHost.

Follows the DMRHost journal for live call activity and polls the BrandMeister
API for device status and talkgroup subscriptions. Static talkgroups can be
managed with a SelfCare API key, which never leaves this process.

Standard library only; all state is kept in memory. The one exception is talkgroup
presets, a small JSON file written only when a preset is saved or deleted.

https://github.com/jcalado/dmrdash - MIT licence
"""

import base64
import collections
import fcntl
import hashlib
import hmac
import itertools
import json
import os
import pty
import re
import secrets
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import termios
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_PATH = os.environ.get("DMRDASH_CONF", "/etc/dmrdash.conf")
HERE = os.path.dirname(os.path.abspath(__file__))
BM_API = "https://api.brandmeister.network/v2"
VERSION = "1.5.0"
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
    "ssh_user": "",  # the Pi account the SSH link and console log in as
    "shell": False,  # the in-browser console; off unless set
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

STATE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/dmrdash")
PRESETS_PATH = os.path.join(STATE_DIR, "presets.json")
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
    "network": {"status": "unknown", "since": None, "address": None},  # address: the master DMRHost logged at startup
    "slots": {1: empty_slot(), 2: empty_slot()},
    "lastheard": collections.deque(maxlen=int(CONF["lastheard_size"])),
    "events": collections.deque(maxlen=60),  # the page collapses retry storms, so keep enough to show what came before one
    # BER of each RF over (the owner's radio into the hotspot), oldest first, tagged with the modem
    # RX offset DMRHost last reported, so the page can show the uplink trend across offset changes
    "rfber": collections.deque(maxlen=200),
    "rxoffset": None,
    "rflevel": None,  # RF power in %, as DMRHost logged it at startup
}

BM = {"device": None, "profile": None, "talkgroups": {}, "masters": [], "updated": None,
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
RE_RXOFFSET = re.compile(r"^\s*RX Offset: (-?\d+)Hz$")
RE_RFLEVEL = re.compile(r"^\s*RF Level: ([\d.]+)%$")
RE_LOGLINE = re.compile(r"^DMRHost: ([DMIWEF]): \S+ \S+ (.*)$")
RE_PARAM_ADDR = re.compile(r"^\s+Address: (\S+)$")
LOG_SECTION = [None]  # the "... Parameters" block DMRHost is listing at startup

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
    m = RE_RXOFFSET.match(msg)
    if m:
        STATE["rxoffset"] = int(m.group(1))
        tune_offset_seen(ts, STATE["rxoffset"])
        return
    m = RE_RFLEVEL.match(msg)
    if m:
        STATE["rflevel"] = float(m.group(1))
        rflevel_seen(STATE["rflevel"])
        return
    if msg.endswith(" Parameters"):
        LOG_SECTION[0] = msg
    elif LOG_SECTION[0] == "DMR Network Parameters" and RE_PARAM_ADDR.match(msg):
        STATE["network"]["address"] = RE_PARAM_ADDR.match(msg).group(1)
    if msg.startswith("DMRHost-") and msg.endswith(" is starting"):
        STATE["host"].update(status="starting", since=ts, version=msg.split()[0])
        STATE["slots"] = {1: empty_slot(), 2: empty_slot()}
        STATE["network"].update(status="connecting", since=ts)
        STATE["modem"].update(status="opening", since=ts)
    elif msg.endswith(" is running"):
        STATE["host"].update(status="running", since=ts)
        if RESTART["requested"] and ts >= RESTART["requested"]:
            add_event(ts, "M", "DMRHost restarted")
            RESTART["requested"] = None
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
        if MASTER["target"] and master_of(STATE["network"]["address"]) == MASTER["target"]:
            master_seen(MASTER["target"])
        elif MASTER["target"]:  # ask BM soon which master it now sees us on, rather than at the next poll
            threading.Timer(5, bm_refresh).start()
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
    source = source.lower() if source else s["source"]
    if source == "rf" and ber is not None:
        over = {"ts": ts, "ber": ber, "dur": secs, "lost": lost, "slot": slot,
                "dst": dst, "group": group, "off": STATE["rxoffset"]}
        STATE["rfber"].append(over)
        tune_over(over)
    STATE["lastheard"].appendleft({
        "ts": ts, "slot": slot, "source": source,
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
        tgs = masters = None
        if names or time.time() - BM["tg_names_updated"] > 86400:
            tgs = bm_request("GET", "/talkgroup")
            masters = bm_request("GET", "/master/")
        with LOCK:
            BM.update(device=device, profile=profile, updated=time.time(), error=None)
            if tgs:
                BM["talkgroups"] = tgs
                BM["tg_names_updated"] = time.time()
            if isinstance(masters, list) and masters:
                BM["masters"] = sorted(({"id": int(m["id"]), "country": str(m.get("country") or ""), "address": str(m.get("address") or "")}
                                        for m in masters if str(m.get("id", "")).isdigit()), key=lambda m: m["id"])
            master_seen(device.get("lastKnownMaster"))
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


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def timed_bodies(body):
    """BrandMeister bodies for a scheduled static TG, one per UTC day part.

    Not in BM's published API spec: this is what its own dashboard sends to
    POST /device/{id}/timedtalkgroup. Each record is one weekday with start/stop in seconds
    of that UTC day, live between startDate and endDate (epoch seconds). The page splits a
    local-time window into these parts, so a window crossing UTC midnight is two records.
    """
    slot, tg = parse_slot_tg(body)
    if tg <= 90:
        raise BMError("Scheduled talkgroups must be above 90")
    try:
        start_date, end_date = int(body["startDate"]), int(body["endDate"])
        parts = [(p["day"], int(p["start"]), int(p["stop"])) for p in body["parts"]]
    except (KeyError, TypeError, ValueError):
        raise BMError("startDate, endDate and parts (day, start, stop) are required")
    if not 0 < start_date < end_date or end_date < time.time():
        raise BMError("The schedule must end after it starts, and in the future")
    if not 1 <= len(parts) <= 14:
        raise BMError("A schedule has 1 to 14 day parts")
    out = []
    for day, start, stop in parts:
        if day not in DAYS or not 0 <= start < stop <= 86400:
            raise BMError("Each part needs a weekday and a start before its stop, within one day")
        out.append({"talkgroup": tg, "slot": slot, "startDate": start_date, "endDate": end_date,
                    **{d: int(d == day) for d in DAYS}, "start": start, "stop": stop})
    return out


def record_ids(body):
    ids = body.get("recordids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 14 or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids):
        raise BMError("recordids must be a list of schedule ids")
    return ids


def add_timed(dev, entries):
    added = 0
    try:
        for entry in entries:
            bm_request("POST", f"/device/{dev}/timedtalkgroup", entry, auth=True)
            added += 1
    except BMError as e:
        bm_refresh()
        raise BMError(f"{e} (after adding {added} of the schedule's parts)" if added else str(e))


def bm_action(body):
    dev = CONF["device_id"]
    action = body.get("action")
    if action == "add":
        slot, tg = parse_slot_tg(body)
        bm_request("POST", f"/device/{dev}/talkgroup", tg_body(slot, tg), auth=True)
    elif action == "remove":
        slot, tg = parse_slot_tg(body)
        bm_request("DELETE", f"/device/{dev}/talkgroup/{slot}/{tg}", auth=True)
    elif action == "addTimed":
        add_timed(dev, timed_bodies(body))
    elif action == "removeTimed":
        for rid in record_ids(body):
            bm_request("DELETE", f"/device/{dev}/timedtalkgroup/{rid}", auth=True)
    elif action == "editTimed":
        # BrandMeister has no update for schedules: add the new one, and only then remove the old,
        # so a refused change leaves the old schedule in place
        ids, entries = record_ids(body), timed_bodies(body)
        add_timed(dev, entries)
        for rid in ids:
            try:
                bm_request("DELETE", f"/device/{dev}/timedtalkgroup/{rid}", auth=True)
            except BMError as e:
                bm_refresh()
                raise BMError(f"Added the new schedule, but couldn't remove the old one: {e}")
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
    elif action in ("tuneStart", "tuneStop", "tuneKeep", "tuneBack", "tryOffset"):
        return tune_action(action, body)
    elif action == "master":
        return request_master(body.get("id"))
    elif action == "power":
        return request_power(body.get("what"))
    elif action == "rflevel":
        return request_rflevel(body.get("pct"))
    elif action == "shellTicket":
        return shell_ticket()
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


# --- RX offset tuning -------------------------------------------------------
# The dashboard can't edit MMDVM.ini or restart DMRHost itself. It writes the wanted offset to a
# request file, and the root-run dmrdash-rxoffset.path/.service (rxoffset.py) checks and applies it.
# DMRHost then logs "RX Offset: N Hz" as it starts, which is how a change is seen to have landed.

REQUEST_PATH = os.path.join(STATE_DIR, "rxoffset.request")
OFFSET_LIMIT = 3000      # Hz either side, matching rxoffset.py
TUNE_STEP = 150          # first step; halved around the best offset once it is bracketed
TUNE_MIN_STEP = 50       # stop once the step would be finer than this
TUNE_OVERS = 3           # overs measured at each offset
TUNE_MAX_STEPS = 8
TUNE_MIN_SECS = 1.5      # shorter overs carry too few frames for a fair BER
TUNE_IDLE = 300          # no key-up for this long: give up and go back to the start offset
APPLY_WAIT = 45          # DMRHost should report the new offset within this

TUNE = {"state": "idle", "note": None, "note_ts": None, "err": False, "sticky": False}


def hz_text(hz):
    return ("+" if hz > 0 else "\u2212" if hz < 0 else "") + f"{abs(hz)} Hz"


def can_tune():
    return os.path.exists(os.path.join(HERE, "rxoffset.py"))


def request_offset(hz):
    """Ask the root helper for an RX offset. False when DMRHost already runs with it."""
    if not isinstance(hz, int) or isinstance(hz, bool) or abs(hz) > OFFSET_LIMIT:
        raise BMError(f"The RX offset must be a whole number of Hz within ±{OFFSET_LIMIT}")
    if hz == STATE["rxoffset"]:
        return False
    if not can_tune():
        raise BMError("RX offset changes need the dmrdash-rxoffset helper; rerun install.sh")
    try:
        with open(REQUEST_PATH, "w") as f:
            f.write(f"{hz}\n")
    except OSError as e:
        raise BMError(f"Can't write the RX offset request on the Pi: {e.strerror or e}")
    return True


def tune_next(steps, step, start):
    """The next offset to measure, and the step it was found at; None when the search is done.

    Walk out from the best offset so far while the BER improves, then halve the step around it.
    With both neighbours untried, head towards the second-best offset (the minimum lies between).
    """
    med = {s["off"]: statistics.median(s["bers"]) for s in steps if s["bers"]}
    while step >= TUNE_MIN_STEP:
        best = min(med, key=lambda o: (med[o], abs(o - start)))
        cands = [c for c in (best - step, best + step) if c not in med and abs(c) <= OFFSET_LIMIT]
        if len(cands) == 2:
            others = sorted((o for o in med if o != best), key=lambda o: (med[o], abs(o - best)))
            if others and others[0] > best:
                cands.reverse()
        if cands:
            return cands[0], step
        step //= 2
    return None, step


def tune_goto(hz, ts):
    t = TUNE
    t["target"], t["since"] = hz, ts
    t["pending"] = request_offset(hz)
    t["requested"] = ts if t["pending"] else None


def tune_start(ts):
    if TUNE["state"] == "running":
        raise BMError("A tuning run is already going")
    cur = STATE["rxoffset"]
    if cur is None:
        raise BMError("DMRHost hasn't reported its RX offset since the dashboard started")
    if not can_tune():
        raise BMError("RX offset changes need the dmrdash-rxoffset helper; rerun install.sh")
    TUNE.clear()
    TUNE.update(state="running", note=None, note_ts=None, err=False, sticky=False, start=cur, step=TUNE_STEP, steps=[{"off": cur, "bers": []}],
                target=cur, pending=False, requested=None, since=ts, best=None)


def tune_finish(ts):
    t = TUNE
    done = [s for s in t["steps"] if len(s["bers"]) >= TUNE_OVERS]
    best = min(done, key=lambda s: (statistics.median(s["bers"]), abs(s["off"] - t["start"])))["off"]
    t.update(state="done", best=best)
    tune_goto(best, ts)  # stay on the best while the owner decides


def tune_advance(ts):
    t = TUNE
    nxt, t["step"] = tune_next(t["steps"], t["step"], t["start"])
    if nxt is None or len(t["steps"]) >= TUNE_MAX_STEPS:
        return tune_finish(ts)
    t["steps"].append({"off": nxt, "bers": []})
    tune_goto(nxt, ts)


def tune_over(over):
    t = TUNE
    if t["state"] != "running" or t["pending"] or over["off"] != t["target"] or over["dur"] < TUNE_MIN_SECS:
        return
    step = t["steps"][-1]
    step["bers"].append(over["ber"])
    t["since"] = over["ts"]
    if len(step["bers"]) >= TUNE_OVERS:
        try:
            tune_advance(over["ts"])
        except BMError as e:
            tune_end(f"Tuning stopped: {e}", revert=False, err=True)


def tune_offset_seen(ts, hz):
    t = TUNE
    if t["state"] in ("running", "done") and t.get("pending") and hz == t["target"]:
        t["pending"], t["since"] = False, ts


def tune_note(note, err=False, sticky=False):
    """sticky (and every error) stays on the page until closed; other notes are a brief confirmation."""
    TUNE.update(note=note, note_ts=time.time(), err=err, sticky=sticky or err)


def tune_end(note, revert=True, err=False, sticky=False):
    start = TUNE.get("start")
    TUNE.clear()
    TUNE.update(state="idle")
    tune_note(note, err, sticky)
    if revert and start is not None:
        request_offset(start)


def tune_watch():
    """Give up on a run nobody is keying up for, or one whose offset change never landed."""
    while True:
        time.sleep(5)
        now = time.time()
        with LOCK:
            t = TUNE
            try:
                if t["state"] in ("running", "done") and t.get("pending") and now - t["requested"] > APPLY_WAIT:
                    tune_end(f"DMRHost didn't come back with RX offset {hz_text(t['target'])}. "
                             "Check the helper: journalctl -u dmrdash-rxoffset", revert=False, err=True)
                elif t["state"] == "running" and not t["pending"] and now - t["since"] > TUNE_IDLE:
                    tune_end(f"No key-up for {TUNE_IDLE // 60} minutes, so tuning stopped and went back to {hz_text(t['start'])}", sticky=True)
            except BMError as e:
                tune_note(str(e), err=True)


def tune_action(action, body):
    with LOCK:
        t, now = TUNE, time.time()
        if action in ("tuneStart", "tryOffset") and (MASTER["target"] or RFLEVEL["target"] or RESTART["requested"]):
            raise BMError(restart_pending())
        if action == "tuneStart":
            tune_start(now)
        elif action == "tuneStop":
            if t["state"] != "running":
                raise BMError("No tuning run is going")
            tune_end(f"Tuning stopped; back to {hz_text(t['start'])}")
        elif action == "tuneKeep":
            if t["state"] != "done":
                raise BMError("No finished tuning run to keep")
            tune_end(f"Kept RX offset {hz_text(t['best'])}", revert=False)
        elif action == "tuneBack":
            if t["state"] != "done":
                raise BMError("No finished tuning run")
            tune_end(f"Back to RX offset {hz_text(t['start'])}")
        elif action == "tryOffset":
            if t["state"] in ("running", "done"):
                raise BMError("Finish the tuning run first")
            hz = body.get("hz")
            if not request_offset(hz):
                raise BMError(f"DMRHost already runs with RX offset {hz_text(hz)}")
            tune_note(f"Trying RX offset {hz_text(hz)}: key up a few times to see whether the BER drops")
    return {"ok": True}


# --- BrandMeister master ----------------------------------------------------
# Same split again: the dashboard writes a master ID to a request file, and the root-run
# dmrdash-master.path/.service (master.py) sets [DMR Network] Address=<id>.master.brandmeister.network
# and restarts DMRHost. The move has landed once BrandMeister reports the hotspot on that master.

MASTER_PATH = os.path.join(STATE_DIR, "master.request")
MASTER_WAIT = 120  # DMRHost should be logged in to the new master, and BM saying so, within this

MASTER = {"target": None, "requested": None}


def can_master():
    return os.path.exists(os.path.join(HERE, "master.py"))


def master_of(address):
    """The master ID behind an Address= DMRHost logged: <id>.master.brandmeister.network, or a listed master's IP."""
    if not address:
        return None
    m = re.fullmatch(r"(\d{4})\.master\.brandmeister\.network\.?", address.lower())
    if m:
        return int(m.group(1))
    return next((x["id"] for x in BM["masters"] if x.get("address") == address), None)


def current_master():
    """Under LOCK: the master DMRHost was started with, else the one BrandMeister last saw (which lags)."""
    return master_of(STATE["network"]["address"]) or (BM["device"] or {}).get("lastKnownMaster")


def master_seen(master):
    """Called under LOCK with BrandMeister's lastKnownMaster after each refresh, or the logged master on login."""
    if MASTER["target"] and master == MASTER["target"]:
        add_event(time.time(), "M", f"Moved to BrandMeister master {master}")
        MASTER.update(target=None, requested=None)


def request_master(mid):
    # Not checked against BM's list, which leaves some live masters out (2681). master.py only ever writes
    # <id>.master.brandmeister.network, so an ID BrandMeister doesn't run simply never logs in.
    if not isinstance(mid, int) or isinstance(mid, bool) or not 1000 <= mid <= 9999:
        raise BMError("A master ID is four digits")
    if not can_master():
        raise BMError("Switching masters needs the dmrdash-master helper; rerun install.sh")
    with LOCK:
        if restart_pending():
            raise BMError(restart_pending())
        if current_master() == mid and STATE["network"]["status"] == "connected":
            raise BMError(f"The hotspot is already on master {mid}")
        try:
            with open(MASTER_PATH, "w") as f:
                f.write(f"{mid}\n")
        except OSError as e:
            raise BMError(f"Can't write the master request on the Pi: {e.strerror or e}")
        MASTER.update(target=mid, requested=time.time())
    return {"ok": True}


def master_watch():
    """Report a move that never landed, and stop waiting for it."""
    while True:
        time.sleep(5)
        with LOCK:
            if MASTER["target"] and time.time() - MASTER["requested"] > MASTER_WAIT:
                add_event(time.time(), "E", f"The hotspot didn't come up on master {MASTER['target']} within "
                          f"{MASTER_WAIT // 60} minutes. Check the helper: journalctl -u dmrdash-master")
                MASTER.update(target=None, requested=None)


# --- restart / shut down ----------------------------------------------------
# Same split as the RX offset: the dashboard only writes "reboot" or "poweroff" to a request file,
# and the root-run dmrdash-power.path/.service (power.py) checks it and asks systemd.

POWER_PATH = os.path.join(STATE_DIR, "power.request")


def can_power():
    return os.path.exists(os.path.join(HERE, "power.py"))


def request_power(what):
    """reboot or poweroff the Pi, or restart just DMRHost ("dmrhost")."""
    if what not in ("reboot", "poweroff", "dmrhost"):
        raise BMError("what must be reboot, poweroff or dmrhost")
    if not can_power():
        raise BMError("Restarts and shut down need the dmrdash-power helper; rerun install.sh")
    with LOCK:
        if restart_pending():
            raise BMError(restart_pending())
        try:
            with open(POWER_PATH, "w") as f:
                f.write(what + "\n")
        except OSError as e:
            raise BMError(f"Can't write the power request on the Pi: {e.strerror or e}")
        if what == "dmrhost":
            RESTART["requested"] = time.time()
    return {"ok": True}


# --- RF power ---------------------------------------------------------------
# The RX offset split once more: the dashboard writes a percentage to a request file, and the root-run
# dmrdash-rflevel.path/.service (rflevel.py) sets [Modem] RFLevel= and restarts DMRHost. DMRHost
# logs "RF Level: N%" as it starts, which is how the change is seen to have landed.

RFLEVEL_PATH = os.path.join(STATE_DIR, "rflevel.request")
RFLEVEL = {"target": None, "requested": None}


def can_rflevel():
    return os.path.exists(os.path.join(HERE, "rflevel.py"))


def request_rflevel(pct):
    # 0 would stop the modem transmitting at all; rflevel.py refuses it too
    if not isinstance(pct, int) or isinstance(pct, bool) or not 1 <= pct <= 100:
        raise BMError("The RF power is a whole percentage from 1 to 100")
    if not can_rflevel():
        raise BMError("RF power changes need the dmrdash-rflevel helper; rerun install.sh")
    with LOCK:
        if restart_pending():
            raise BMError(restart_pending())
        if STATE["rflevel"] == pct:
            raise BMError(f"DMRHost already runs at {pct}% RF power")
        try:
            with open(RFLEVEL_PATH, "w") as f:
                f.write(f"{pct}\n")
        except OSError as e:
            raise BMError(f"Can't write the RF power request on the Pi: {e.strerror or e}")
        RFLEVEL.update(target=pct, requested=time.time())
    return {"ok": True}


def rflevel_seen(level):
    """Under LOCK, with the RF level DMRHost just logged."""
    if RFLEVEL["target"] is not None and level == RFLEVEL["target"]:
        add_event(time.time(), "M", f"RF power now {RFLEVEL['target']}%")
        RFLEVEL.update(target=None, requested=None)


# Every change above restarts DMRHost, so only one may be under way at a time.
RESTART = {"requested": None}  # a plain DMRHost restart from the dashboard, until DMRHost says it is running
RESTART_WAIT = 60


def restart_pending():
    """Under LOCK: why DMRHost can't be restarted again yet, or None."""
    if TUNE["state"] in ("running", "done"):
        return "Finish the RX offset tuning run first"
    if MASTER["target"]:
        return f"Wait for the move to master {MASTER['target']} to finish"
    if RFLEVEL["target"] is not None:
        return f"Wait for the change to {RFLEVEL['target']}% RF power to finish"
    if RESTART["requested"]:
        return "DMRHost is already restarting"
    return None


def restart_watch():
    """Report an RF power change or a DMRHost restart that never landed, and stop waiting for it."""
    while True:
        time.sleep(5)
        now = time.time()
        with LOCK:
            if RFLEVEL["target"] is not None and now - RFLEVEL["requested"] > APPLY_WAIT:
                add_event(now, "E", f"DMRHost didn't come back at {RFLEVEL['target']}% RF power. "
                          "Check the helper: journalctl -u dmrdash-rflevel")
                RFLEVEL.update(target=None, requested=None)
            if RESTART["requested"] and now - RESTART["requested"] > RESTART_WAIT:
                add_event(now, "E", f"DMRHost didn't report running within {RESTART_WAIT} seconds of the restart. "
                          "Check it: journalctl -u dmrdash-power -u " + CONF["unit"])
                RESTART["requested"] = None


# --- console ----------------------------------------------------------------
# The browser's terminal drives `ssh <ssh_user>@localhost` on a pty here. The Pi's own sshd asks for and
# checks the password, so the dashboard gains no rights: it stays the sandboxed dmrdash user until sshd
# logs the reader in, and a Pi set to key-only logins simply refuses. Off unless "shell" is true.
#
# Opening one takes a one-time ticket from POST /api/bm {"action": "shellTicket"}, which has the same custom
# header and PIN checks as every other change. The WebSocket at /api/shell?t=<ticket> must then come from
# this dashboard's own origin, because browsers don't apply CORS to WebSockets.

SHELL_TICKETS = {}       # ticket -> expiry
SHELL_TICKET_TTL = 30    # seconds between asking for a ticket and connecting with it
SHELL_MAX = 2            # sessions at once
SHELL = {"sessions": 0}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ssh_user():
    """The configured account name, or "" when it isn't a plain Unix user name (it reaches ssh's argv)."""
    u = str(CONF["ssh_user"] or "")
    return u if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", u) else ""


def can_shell():
    return CONF["shell"] is True and bool(ssh_user()) and shutil.which("ssh") is not None


def shell_cmd():
    # no ssh config of its own (-F /dev/null), host keys kept in the state folder, and only password logins:
    # the dmrdash user has no keys, and must never borrow any
    return ["ssh", "-F", "/dev/null", "-o", "UserKnownHostsFile=" + os.path.join(STATE_DIR, "known_hosts"),
            "-o", "StrictHostKeyChecking=accept-new", "-o", "PubkeyAuthentication=no", "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=30", "-l", ssh_user(), "localhost"]


def shell_ticket():
    if not can_shell():
        raise BMError('The console is off. Set "shell": true and "ssh_user" in /etc/dmrdash.conf')
    now = time.time()
    with LOCK:
        for t in [t for t, exp in SHELL_TICKETS.items() if exp < now]:
            del SHELL_TICKETS[t]
        if len(SHELL_TICKETS) > 20:
            raise BMError("Too many console requests; try again in a minute")
        ticket = secrets.token_urlsafe(24)
        SHELL_TICKETS[ticket] = now + SHELL_TICKET_TTL
    return {"ok": True, "ticket": ticket}


def ws_send(sock, lock, opcode, payload=b""):
    n = len(payload)
    size = bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, "big") if n < 65536 else bytes([127]) + n.to_bytes(8, "big")
    with lock:
        sock.sendall(bytes([0x80 | opcode]) + size + payload)


def ws_recv(rfile):
    """One frame from the browser as (opcode, payload), or None once the connection is gone or misbehaves."""
    head = rfile.read(2)
    if len(head) < 2:
        return None
    op, masked, n = head[0] & 0x0F, head[1] & 0x80, head[1] & 0x7F
    if n == 126:
        n = int.from_bytes(rfile.read(2), "big")
    elif n == 127:
        n = int.from_bytes(rfile.read(8), "big")
    if not masked or n > 1 << 16:  # browsers always mask; a terminal never needs a bigger frame
        return None
    mask, data = rfile.read(4), rfile.read(n)
    if len(mask) < 4 or len(data) < n:
        return None
    return op, bytes(a ^ b for a, b in zip(data, itertools.cycle(mask)))


def set_winsize(fd, cols, rows):
    cols, rows = max(1, min(int(cols), 500)), max(1, min(int(rows), 300))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def shell_session(rfile, sock, cols, rows):
    """Bridge one WebSocket and one ssh on a pty until either side ends. Binary frames carry the terminal's
    bytes both ways; a text frame from the browser is a resize, {"cols": c, "rows": r}."""
    master, slave = pty.openpty()
    set_winsize(master, cols, rows)
    try:
        # its own session, with the pty as controlling terminal: ssh reads the password from /dev/tty
        proc = subprocess.Popen(shell_cmd(), stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
                                env={"TERM": "xterm-256color", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"})
    except OSError as e:
        os.close(master)
        raise BMError(f"Can't start ssh: {e.strerror or e}")
    finally:
        os.close(slave)
    lock = threading.Lock()

    def pump():
        try:
            while True:
                data = os.read(master, 16384)
                if not data:
                    break
                ws_send(sock, lock, 2, data)
        except OSError:
            pass  # EIO once ssh has exited and the pty is gone
        try:
            ws_send(sock, lock, 8, (1000).to_bytes(2, "big"))
            sock.shutdown(socket.SHUT_RDWR)  # unblocks the reader below
        except OSError:
            pass

    out = threading.Thread(target=pump, daemon=True)
    out.start()
    try:
        last = 2
        while True:
            frame = ws_recv(rfile)
            if frame is None:
                break
            op, data = frame
            if op == 0:
                op = last  # a continuation of the frame before
            elif op in (1, 2):
                last = op
            if op == 8:
                break
            if op == 9:
                ws_send(sock, lock, 10, data)
            elif op == 2:
                while data:
                    data = data[os.write(master, data):]
            elif op == 1:
                try:
                    j = json.loads(data)
                    set_winsize(master, j["cols"], j["rows"])
                except (ValueError, KeyError, TypeError):
                    pass
    except OSError:
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGHUP)
            proc.wait(timeout=3)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        out.join(timeout=3)
        os.close(master)


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


def booted():
    """When the Pi booted, from /proc/uptime; None where there is no /proc."""
    try:
        with open("/proc/uptime") as f:
            return time.time() - float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
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
            "booted": booted(),
            "can_manage": bool(CONF["api_key"]),
            "can_power": can_power(),
            "ssh": {"user": ssh_user(), "shell": can_shell()},
            "pin": bool(CONF["pin"]),
            "host": dict(STATE["host"]),
            "modem": dict(STATE["modem"]),
            "network": dict(STATE["network"]),
            "slots": {k: dict(v) for k, v in STATE["slots"].items()},
            "lastheard": list(STATE["lastheard"]),
            "events": list(STATE["events"]),
            "rf": {"overs": list(STATE["rfber"]), "offset": STATE["rxoffset"], "can_tune": can_tune(),
                   "tune": json.loads(json.dumps(TUNE)), "tune_overs": TUNE_OVERS, "tune_max_steps": TUNE_MAX_STEPS,
                   "tune_min_secs": TUNE_MIN_SECS, "tune_idle": TUNE_IDLE},
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
            "masters": BM["masters"],
            "rflevel": {"level": STATE["rflevel"], "can": can_rflevel(), "target": RFLEVEL["target"]},
            "restart": RESTART["requested"],
            "master": {"can": can_master(), "current": current_master(), "target": MASTER["target"], "requested": MASTER["requested"]},
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


VENDOR = {"/vendor/xterm.mjs": "text/javascript; charset=utf-8", "/vendor/addon-fit.mjs": "text/javascript; charset=utf-8",
          "/vendor/xterm.css": "text/css; charset=utf-8"}


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

    def send(self, code, body, ctype="application/json", cache="no-store"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
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
        if self.path in VENDOR:
            # the console's terminal (xterm.js), fetched only when it opens; a version bump changes the page, not these names
            with open(os.path.join(HERE, "vendor", self.path.rsplit("/", 1)[1]), "rb") as f:
                return self.send(200, f.read(), VENDOR[self.path], cache="max-age=86400")
        if self.path.startswith("/api/shell"):
            return self.shell()
        if self.path.startswith("/api/log"):
            m = re.fullmatch(r"/api/log(?:\?after=(\d+))?", self.path)
            if not m:
                return self.send(400, {"error": "bad request"})
            after = int(m.group(1) or 0)
            with LOCK:
                body = {"lines": [x for x in LOG if x["n"] > after], "last": LOG_SEQ[0]}
            return self.send(200, body)
        self.send(404, {"error": "not found"})

    def shell(self):
        m = re.fullmatch(r"/api/shell\?t=([\w-]{16,64})&cols=(\d{1,3})&rows=(\d{1,3})", self.path)
        host, key = self.headers.get("Host") or "", self.headers.get("Sec-WebSocket-Key") or ""
        # browsers don't apply CORS to WebSockets: only this dashboard's own page may open one
        if not m or not key or (self.headers.get("Upgrade") or "").lower() != "websocket" \
                or (self.headers.get("Origin") or "") not in ("http://" + host, "https://" + host):
            return self.send(400, {"error": "bad request"})
        refused = None
        with LOCK:
            if SHELL_TICKETS.pop(m.group(1), 0) < time.time():
                refused = 403, {"error": "No or expired console ticket"}
            elif SHELL["sessions"] >= SHELL_MAX:
                refused = 429, {"error": f"Already {SHELL_MAX} consoles open"}
            else:
                SHELL["sessions"] += 1
        if refused:
            return self.send(*refused)
        self.close_connection = True
        try:
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode())
            self.end_headers()
            self.wfile.flush()
            shell_session(self.rfile, self.connection, int(m.group(2)), int(m.group(3)))
        except (OSError, BMError):
            pass
        finally:
            with LOCK:
                SHELL["sessions"] -= 1

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
    threading.Thread(target=tune_watch, daemon=True).start()
    threading.Thread(target=master_watch, daemon=True).start()
    threading.Thread(target=restart_watch, daemon=True).start()
    srv = ThreadingHTTPServer((CONF["listen"], int(CONF["port"])), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
