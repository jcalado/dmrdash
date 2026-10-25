#!/usr/bin/python3 -I
"""Apply an RF power level the dashboard asked for: check it, set RFLevel in MMDVM.ini, restart DMRHost.

Runs as root from dmrdash-rflevel.service, which dmrdash-rflevel.path starts whenever the
unprivileged dashboard writes /var/lib/dmrdash/rflevel.request. The request holds one whole
percentage, 1 to 100, and nothing else is trusted from it, so the web server never gets write
access to the modem config.

https://github.com/jcalado/dmrdash - MIT licence
"""

import json
import os
import re
import stat
import subprocess
import sys

REQUEST = "/var/lib/dmrdash/rflevel.request"
CONF = "/etc/dmrdash.conf"


def fail(msg):
    print("rflevel: " + msg, file=sys.stderr)
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
    # 0 is refused: the modem would stop transmitting, and the hotspot would look broken rather than quiet
    if not re.fullmatch(r"\d{1,3}", raw) or not 1 <= int(raw) <= 100:
        fail("refused: the RF level must be a whole percentage from 1 to 100")
    return int(raw)


def set_level(ini, pct):
    with open(ini) as f:
        lines = f.read().split("\n")
    section, hit = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
        elif section == "[Modem]" and re.match(r"RFLevel\s*=", s):
            hit = i
            break
    if hit is None:
        fail(f"no RFLevel= line in the [Modem] section of {ini}")
    if lines[hit].strip() == f"RFLevel={pct}":
        return False
    lines[hit] = f"RFLevel={pct}"
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
    pct = read_request()
    changed = set_level(ini, pct)
    print(f"rflevel: RFLevel={pct} in {ini}" + ("" if changed else " (already set)") + f", restarting {unit}")
    # restart even when unchanged: the dashboard waits for DMRHost to report the level it runs with
    subprocess.run(["systemctl", "restart", unit + ".service"], check=True)


if __name__ == "__main__":
    main()
