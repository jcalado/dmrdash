#!/usr/bin/python3 -I
"""Read MMDVM.ini for the dashboard, apply its edit, or put the previous file back, then restart DMRHost.

Runs as root from dmrdash-config.service, which dmrdash-config.path starts whenever the unprivileged
dashboard writes /var/lib/dmrdash/config.request. MMDVM.ini is often root's alone, so the dashboard never
opens it itself. The request is JSON, one of:

  {"op": "read", "id": ..., "ts": ...}
  {"op": "apply", "id": ..., "ts": ..., "base": <sha256 of the file the edit was made against>, "text": <the edited file>}
  {"op": "restore", "id": ..., "ts": ..., "base": <sha256 of the file as it is now>}

"read" leaves the file, with every password replaced by a mask, and its hash in /var/lib/dmrdash/config.view.
"apply" takes the edited text in that same masked form: it applies only to the exact file it was made against,
only when every line is blank, a comment, a [section] or key=value, and each mask goes back to the password the
file has. The file as it was is kept beside it as MMDVM.ini.dmrdash-prev, and "restore" swaps the two back.
A request older than a minute is ignored, and the request is emptied once read, so a password typed on the
page doesn't stay on the SD card.

How a change went is written to /var/lib/dmrdash/config.status, which the dashboard reads back.

https://github.com/jcalado/dmrdash - MIT licence
"""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time

REQUEST = "/var/lib/dmrdash/config.request"
STATUS = "/var/lib/dmrdash/config.status"
VIEW = "/var/lib/dmrdash/config.view"
CONF = "/etc/dmrdash.conf"
PREV = ".dmrdash-prev"
MAX_SIZE = 512 * 1024   # the request
MAX_INI = 64 * 1024     # MMDVM.ini, as the dashboard limits it
MAX_AGE = 60  # seconds
MASK = "●" * 8     # the dashboard's stand-in for a password
SECRET = re.compile(r"(?i)pass|secret|token|apikey")
HASH = re.compile(r"[0-9a-f]{64}")
KV = re.compile(r"^(\s*([^=]*?)\s*=\s*)(.*)$")
# blank, a # or ; comment, a [section], or key=value
LINE = re.compile(r"[ \t]*(?:[#;].*|\[[^\]]+\][ \t]*|[^\s=\[#;][^=]*=.*)?")
CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

REQ_ID = [None]


def write_json(dest, mode, **out):
    # readable by the dashboard: the state folder itself is the dmrdash user's alone (0750)
    out.update(id=REQ_ID[0], ts=time.time())
    tmp = dest + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        json.dump(out, f)
    os.replace(tmp, dest)


def write_status(**status):
    write_json(STATUS, 0o644, **status)


def fail(msg):
    print("config: " + msg, file=sys.stderr)
    if REQ_ID[0]:
        try:
            write_status(state="failed", error=msg)
        except OSError:
            pass
    sys.exit(1)


def read_request():
    """The checked request, or None when it is empty (as this helper leaves it)."""
    try:
        fd = os.open(REQUEST, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        fail(f"can't open the request: {e.strerror}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SIZE:
            fail("the request is not a regular file under 512 KiB")
        raw = b""
        while len(raw) <= MAX_SIZE:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        if raw.strip():
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)
    if not raw.strip():
        return None
    try:
        req = json.loads(raw)
    except ValueError:
        fail("refused: the request is not JSON")
    if not isinstance(req, dict) or not re.fullmatch(r"[0-9a-f]{1,32}", str(req.get("id"))):
        fail("refused: the request has no id")
    REQ_ID[0] = req["id"]
    ts = req.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or abs(time.time() - ts) > MAX_AGE:
        fail("ignored: the request is stale")
    if req.get("op") not in ("read", "apply", "restore"):
        fail("refused: the request must be read, apply or restore")
    if req["op"] != "read" and not HASH.fullmatch(str(req.get("base"))):
        fail("refused: a change needs the hash of the file it was made against")
    if req["op"] == "apply":
        text = req.get("text")
        if not isinstance(text, str) or len(text.encode()) > MAX_INI:
            fail("refused: the edited file is missing or over 64 KiB")
        for n, line in enumerate(text.split("\n"), 1):
            if CONTROL.search(line) or not LINE.fullmatch(line):
                fail(f"refused: line {n} is not blank, a comment, a [section] or key=value")
    return req


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def split(data):
    """The file's lines and its line ending."""
    text = data.decode("utf-8")
    return text.replace("\r\n", "\n").split("\n"), ("\r\n" if "\r\n" in text else "\n")


def keys(lines):
    """(section, key, value, line index, the part up to the value) for each key=value line."""
    sec = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        if s[0] == "[":
            sec = s[1:-1] if s.endswith("]") and len(s) > 2 else sec
            continue
        m = KV.match(line)
        if m and m.group(2):
            yield sec, m.group(2), m.group(3).strip(), i, m.group(1)


def mask(lines):
    out = list(lines)
    for sec, key, val, i, pre in keys(lines):
        if val and SECRET.search(key):
            out[i] = pre + MASK
    return out


def unmask(old, new):
    """The edited lines, each masked password put back from the file as it is."""
    found = {}
    for sec, key, val, i, pre in keys(old):
        if val and SECRET.search(key):
            found.setdefault((sec, key), val)
    out = list(new)
    for sec, key, val, i, pre in keys(new):
        if val == MASK:
            if (sec, key) not in found:
                raise ValueError(f"{key} in [{sec}] is masked, but the file has no password there")
            out[i] = pre + found[(sec, key)]
    return out


def write_like(path, data, like):
    """Replace path with data in one step, with the owner and mode of the file like."""
    st = os.stat(like)
    tmp = path + ".dmrdash"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, st.st_mode & 0o7777), "wb") as f:
        f.write(data)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


def read_ini(ini, base=None):
    with open(ini, "rb") as f:
        data = f.read(MAX_INI + 1)
    if base and sha256(data) != base:
        fail(f"refused: {ini} changed since the dashboard read it")
    return data


def view(ini):
    """The masked copy for the dashboard, or why there's none."""
    try:
        data = read_ini(ini)
        if len(data) > MAX_INI:
            raise ValueError(f"{ini} is over 64 KiB, too big to edit here")
        lines, eol = split(data)
    except OSError as e:
        return write_json(VIEW, 0o644, error=f"Can't read {ini}: {e.strerror or e}")
    except UnicodeDecodeError:
        return write_json(VIEW, 0o644, error=f"{ini} isn't UTF-8 text, so it can't be edited here")
    except ValueError as e:
        return write_json(VIEW, 0o644, error=str(e))
    write_json(VIEW, 0o644, path=ini, base=sha256(data), text="\n".join(mask(lines)))


def apply(ini, req):
    """Write the edit. The hash of the file it gives."""
    data = read_ini(ini, req["base"])
    try:
        lines, eol = split(data)
        new = eol.join(unmask(lines, req["text"].split("\n"))).encode()
    except (UnicodeDecodeError, ValueError) as e:
        fail(f"refused: {e}")
    if new == data:
        fail("refused: the edit doesn't change the file")
    write_like(ini + PREV, data, ini)
    write_like(ini, new, ini)
    return sha256(new)


def restore(ini, req):
    data = read_ini(ini, req["base"])
    prev = ini + PREV
    if not os.path.isfile(prev) or os.path.islink(prev):
        fail(f"there's no {prev} to put back")
    with open(prev, "rb") as f:
        old = f.read()
    write_like(prev, data, ini)  # so the edit can come back in turn
    write_like(ini, old, ini)
    return sha256(old)


def main():
    try:
        with open(CONF) as f:
            conf = json.load(f)
    except (OSError, ValueError):
        conf = {}
    ini = conf.get("mmdvm_ini") or "/etc/MMDVM.ini"
    unit = conf.get("unit") or "dmrhost"
    if not re.fullmatch(r"[\w@.-]+", unit):
        fail("refused: odd unit name in " + CONF)
    req = read_request()
    if req is None:
        return  # emptied after the last request, which wakes this up once more
    if req["op"] == "read":
        return view(ini)
    digest = (apply if req["op"] == "apply" else restore)(ini, req)
    print(f"config: {'edited' if req['op'] == 'apply' else 'put back the previous'} {ini}, restarting {unit}")
    r = subprocess.run(["systemctl", "restart", unit + ".service"])
    if r.returncode:
        fail(f"wrote {ini}, but systemctl couldn't restart {unit} (status {r.returncode})")
    write_status(state="done", op=req["op"], hash=digest)


if __name__ == "__main__":
    main()
