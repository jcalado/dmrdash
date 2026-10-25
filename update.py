#!/usr/bin/python3 -I
"""Update dmrdash from its apt repo when the dashboard asks.

Runs as root from dmrdash-update.service, which dmrdash-update.path starts whenever the
unprivileged dashboard writes /var/lib/dmrdash/update.request. The request holds the one word
"dmrdash" and nothing else is trusted from it: what gets installed is whatever apt accepts from
the repo in /etc/apt/sources.list.d/dmrdash.sources, whose signature apt checks against the
packaged keyring. A request older than a minute is ignored, so a leftover file can never act on its own.

How it went is written to /var/lib/dmrdash/update.status, which the dashboard reads back.
The package's postinst restarts the dashboard partway through; this runs in its own unit, so it carries on.

https://github.com/jcalado/dmrdash - MIT licence
"""

import json
import os
import stat
import subprocess
import sys
import time

REQUEST = "/var/lib/dmrdash/update.request"
STATUS = "/var/lib/dmrdash/update.status"
SOURCES = "/etc/apt/sources.list.d/dmrdash.sources"
MAX_AGE = 60  # seconds

# Refresh only dmrdash's own repo, and leave the other repos' lists alone (List-Cleanup would delete them).
APT_UPDATE = ["apt-get", "update", "-o", "Dir::Etc::SourceList=" + SOURCES, "-o", "Dir::Etc::SourceParts=-",
              "-o", "APT::Get::List-Cleanup=0"]
# Waits for an unattended upgrade holding the lock instead of failing; keeps an edited config file.
APT_INSTALL = ["apt-get", "install", "-y", "--only-upgrade", "-o", "DPkg::Lock::Timeout=300",
               "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold", "dmrdash"]


def fail(msg):
    print("update: " + msg, file=sys.stderr)
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
    if raw != "dmrdash":
        fail("refused: the request must be dmrdash")


def installed():
    r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "dmrdash"], capture_output=True, text=True)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def write_status(**status):
    status["ts"] = time.time()
    tmp = STATUS + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w") as f:
        json.dump(status, f)
    os.replace(tmp, STATUS)


def run(cmd):
    """Run an apt step with its output in the journal. The last lines, when it fails."""
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    print(r.stdout, end="")
    if r.returncode == 0:
        return None
    lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    return " / ".join(lines[-3:]) or f"{cmd[1]} exited with status {r.returncode}"


def main():
    read_request()
    before = installed()
    if not before:
        fail("refused: dmrdash isn't installed as a package")
    print(f"update: dmrdash {before}, update requested from the dashboard")
    write_status(state="running", old=before)
    error = run(APT_UPDATE) or run(APT_INSTALL)
    after = installed()
    if error:
        write_status(state="failed", old=before, new=after, error=error)
        fail(error)
    write_status(state="done", old=before, new=after)
    print(f"update: dmrdash {after}" + (" (no newer version)" if after == before else ""))


if __name__ == "__main__":
    main()
