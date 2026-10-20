#!/usr/bin/python3 -I
"""Apply an RX offset the dashboard asked for: check it, set RXOffset in MMDVM.ini, restart DMRHost.

Runs as root from dmrdash-rxoffset.service, which dmrdash-rxoffset.path starts whenever the
unprivileged dashboard writes /var/lib/dmrdash/rxoffset.request. The request holds one number
in Hz and nothing else is trusted from it, so the web server never gets write access to the
modem config.

https://github.com/jcalado/dmrdash - MIT licence
"""

import json
import os
import re
import stat
import subprocess
import sys

REQUEST = "/var/lib/dmrdash/rxoffset.request"
CONF = "/etc/dmrdash.conf"
LIMIT = 3000  # Hz either side; dmrdash refuses anything outside this too


def fail(msg):
    print("rxoffset: " + msg, file=sys.stderr)
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
    if not re.fullmatch(r"-?\d{1,4}", raw) or abs(int(raw)) > LIMIT:
        fail(f"refused: the offset must be a whole number of Hz within ±{LIMIT}")
    return int(raw)


def set_offset(ini, hz):
    with open(ini) as f:
        lines = f.read().split("\n")
    section, hit = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
        elif section == "[Modem]" and re.match(r"RXOffset\s*=", s):
            hit = i
            break
    if hit is None:
        fail(f"no RXOffset= line in the [Modem] section of {ini}")
    if lines[hit].strip() == f"RXOffset={hz}":
        return False
    lines[hit] = f"RXOffset={hz}"
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
    hz = read_request()
    changed = set_offset(ini, hz)
    print(f"rxoffset: RXOffset={hz} in {ini}" + ("" if changed else " (already set)") + f", restarting {unit}")
    # restart even when unchanged: the dashboard waits for DMRHost to report the offset it runs with
    subprocess.run(["systemctl", "restart", unit + ".service"], check=True)


if __name__ == "__main__":
    main()
