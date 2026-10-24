#!/usr/bin/python3 -I
"""Move the hotspot to the BrandMeister master the dashboard asked for, and restart DMRHost.

Runs as root from dmrdash-master.service, which dmrdash-master.path starts whenever the
unprivileged dashboard writes /var/lib/dmrdash/master.request. The request holds one master
ID (four digits) and nothing else is trusted from it: the address written is always that
master's own name, <id>.master.brandmeister.network, so the hotspot can't be pointed
anywhere outside BrandMeister.

https://github.com/jcalado/dmrdash - MIT licence
"""

import json
import os
import re
import stat
import subprocess
import sys

REQUEST = "/var/lib/dmrdash/master.request"
CONF = "/etc/dmrdash.conf"


def fail(msg):
    print("master: " + msg, file=sys.stderr)
    sys.exit(1)


def read_request():
    try:
        fd = os.open(REQUEST, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        fail(f"can't open the request: {e.strerror}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 16:
            fail("the request is not a small regular file")
        raw = os.read(fd, 16).decode("ascii", "replace").strip()
    finally:
        os.close(fd)
    if not re.fullmatch(r"[1-9]\d{3}", raw):
        fail("refused: the request must be a four-digit BrandMeister master ID")
    return int(raw)


def set_master(ini, master):
    with open(ini) as f:
        lines = f.read().split("\n")
    section, hit = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
        elif section == "[DMR Network]" and re.match(r"Address\s*=", s):
            hit = i
            break
    if hit is None:
        fail(f"no Address= line in the [DMR Network] section of {ini}")
    want = f"Address={master}.master.brandmeister.network"
    if lines[hit].strip() == want:
        return False
    lines[hit] = want
    st = os.stat(ini)
    tmp = ini + ".dmrdash"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, st.st_mode & 0o7777), "w") as f:
        f.write("\n".join(lines))
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, ini)
    return True


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
    master = read_request()
    changed = set_master(ini, master)
    print(f"master: {master}.master.brandmeister.network in {ini}" + ("" if changed else " (already set)") + f", restarting {unit}")
    # restart even when unchanged: the dashboard waits for DMRHost to log in again
    subprocess.run(["systemctl", "restart", unit + ".service"], check=True)


if __name__ == "__main__":
    main()
