#!/usr/bin/python3 -I
"""Restart or shut down the Pi when the dashboard asks.

Runs as root from dmrdash-power.service, which dmrdash-power.path starts whenever the
unprivileged dashboard writes /var/lib/dmrdash/power.request. The request holds one word,
"reboot" or "poweroff", and nothing else is trusted from it. A request older than a minute
is ignored, so a leftover file can never act on its own.

https://github.com/jcalado/dmrdash - MIT licence
"""

import os
import stat
import subprocess
import sys
import time

REQUEST = "/var/lib/dmrdash/power.request"
MAX_AGE = 60  # seconds


def fail(msg):
    print("power: " + msg, file=sys.stderr)
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
    if abs(time.time() - st.st_mtime) > MAX_AGE:
        fail("ignored: the request is stale")
    if raw not in ("reboot", "poweroff"):
        fail("refused: the request must be reboot or poweroff")
    return raw


def main():
    what = read_request()
    print(f"power: {what} requested from the dashboard")
    subprocess.run(["systemctl", what], check=True)


if __name__ == "__main__":
    main()
