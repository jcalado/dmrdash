#!/bin/sh
# Install, update or remove dmrdash.
#
#   curl -fsSL https://github.com/jcalado/dmrdash/releases/latest/download/install.sh | sudo sh
#   sudo ./install.sh                 from a checkout or unpacked release
#   sudo ./install.sh --uninstall     keeps /etc/dmrdash.conf and saved presets
#   sudo ./install.sh --purge         removes those too
#
# Where apt is, a release installs the dmrdash package from its apt repo, which the dashboard can then update.
# A checkout, a pinned version, --files, or a system without apt copies the files instead (no updates from the dashboard).
#
# Options: --yes skips the questions (set the key later in /etc/dmrdash.conf). --files copies the files.
# DMRDASH_VERSION=v1.0.0 pins the release a piped install downloads.
set -eu

REPO=jcalado/dmrdash
CONF=/etc/dmrdash.conf
DEST=/opt/dmrdash
UNIT=/etc/systemd/system/dmrdash.service
HELPER_UNITS="dmrdash-rxoffset.path dmrdash-rxoffset.service dmrdash-master.path dmrdash-master.service dmrdash-power.path dmrdash-power.service dmrdash-rflevel.path dmrdash-rflevel.service"

say() { printf '%s\n' "$*"; }
die() { printf 'dmrdash: %s\n' "$*" >&2; exit 1; }

MODE=install ASK=1 FILES=0
for a in "$@"; do
  case "$a" in
    --uninstall) MODE=uninstall ;;
    --purge) MODE=purge ;;
    -y|--yes) ASK=0 ;;
    --files) FILES=1 ;;
    -h|--help) sed -n '2,13p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $a" ;;
  esac
done

[ "$(id -u)" = 0 ] || die "run as root (sudo)"

packaged() { dpkg-query -W -f='${Status}' dmrdash 2>/dev/null | grep -q 'ok installed'; }

if [ "$MODE" != install ]; then
  if packaged; then
    if [ "$MODE" = purge ]; then apt-get purge -y dmrdash; else apt-get remove -y dmrdash; fi
  fi
  systemctl disable --now dmrdash dmrdash-rxoffset.path dmrdash-master.path dmrdash-power.path dmrdash-rflevel.path >/dev/null 2>&1 || true
  rm -f "$UNIT"
  for u in $HELPER_UNITS; do rm -f "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  rm -rf "$DEST"
  rm -f /usr/local/bin/dmrdash-tui
  if [ "$MODE" = purge ]; then
    rm -f "$CONF"
    rm -rf /var/lib/dmrdash
    userdel dmrdash >/dev/null 2>&1 || true
    say "dmrdash removed, with its settings and presets."
  else
    say "dmrdash removed. Settings kept in $CONF and presets in /var/lib/dmrdash (--purge removes them)."
  fi
  exit 0
fi

# Piped from curl there are no files next to this script: fetch a release and run its installer.
SRC=$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo /nonexistent)
if [ ! -f "$SRC/dmrdash.py" ]; then
  if [ -n "${DMRDASH_VERSION:-}" ]; then
    URL="https://github.com/$REPO/releases/download/$DMRDASH_VERSION/dmrdash.tar.gz"
  else
    URL="https://github.com/$REPO/releases/latest/download/dmrdash.tar.gz"
  fi
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  say "Downloading $URL"
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$URL" -o "$TMP/d.tgz"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$TMP/d.tgz" "$URL"
  else die "needs curl or wget"; fi
  tar -xzf "$TMP/d.tgz" -C "$TMP"
  [ -f "$TMP/dmrdash/install.sh" ] || die "release archive has no installer"
  sh "$TMP/dmrdash/install.sh" "$@"
  exit 0
fi

# --- prerequisites -----------------------------------------------------------
{ command -v systemctl && command -v journalctl; } >/dev/null 2>&1 || die "needs systemd"
command -v python3 >/dev/null 2>&1 || die "needs python3 (apt install python3)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "needs Python 3.9 or newer"

# the package where apt can have it, else the files; a checkout means its own files
if [ "$FILES" = 0 ] && [ -z "${DMRDASH_VERSION:-}" ] && [ ! -e "$SRC/.git" ] && command -v apt-get >/dev/null 2>&1 \
    && [ -f "$SRC/packaging/dmrdash.sources" ]; then
  METHOD=apt
else
  METHOD=files
fi
packaged && [ "$METHOD" = files ] && die "dmrdash is installed as a package; update it from the dashboard or with apt"

# --- settings ----------------------------------------------------------------
id dmrdash >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin dmrdash
NEW=0
if [ ! -f "$CONF" ]; then
  install -m 0640 -g dmrdash "$SRC/dmrdash.conf.example" "$CONF"
  NEW=1
fi
chown root:dmrdash "$CONF" && chmod 0640 "$CONF"

# conf KEY [VALUE]: read a setting, or write one (VALUE goes through the environment, never argv)
conf() {
  if [ $# = 1 ]; then
    K="$1" P="$CONF" python3 -I -c 'import json, os; print(json.load(open(os.environ["P"])).get(os.environ["K"], ""))'
  else
    K="$1" V="$2" P="$CONF" python3 -I -c '
import json, os
p, k, v = os.environ["P"], os.environ["K"], os.environ["V"]
c = json.load(open(p))
c[k] = int(v) if k == "device_id" else v
t = p + ".new"
with os.fdopen(os.open(t, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), "w") as f:
    json.dump(c, f, indent=2)
    f.write("\n")
os.chown(t, 0, os.stat(p).st_gid)
os.replace(t, p)'
  fi
}

# Device ID: take it from the hotspot's own config when ours has none.
if [ "$(conf device_id)" = 0 ]; then
  for ini in /etc/MMDVM.ini /etc/mmdvmhost /etc/MMDVMHost.ini; do
    [ -r "$ini" ] || continue
    ID=$(awk -F= '/^\[/{s=$0} s=="[General]" && $1=="Id"{gsub(/[^0-9]/,"",$2); print $2; exit}' "$ini")
    if [ -n "$ID" ]; then conf device_id "$ID"; say "Device ID $ID (from $ini)"; break; fi
  done
fi

# Hidden answer from the terminal, so questions work under curl | sh too.
secret() {
  printf '%s' "$1" >/dev/tty
  stty -echo </dev/tty 2>/dev/null || true
  read -r ANSWER </dev/tty || ANSWER=
  stty echo </dev/tty 2>/dev/null || true
  printf '\n' >/dev/tty
}

if [ "$ASK" = 1 ] && (: </dev/tty) 2>/dev/null; then
  if [ "$(conf device_id)" = 0 ]; then
    printf 'BrandMeister device ID (DMR ID plus SSID, e.g. 268051301): ' >/dev/tty
    read -r ID </dev/tty || ID=
    case "$ID" in ''|*[!0-9]*) say "Skipped; set device_id in $CONF" ;; *) conf device_id "$ID" ;; esac
  fi
  if [ -z "$(conf api_key)" ]; then
    secret 'SelfCare API key, to manage talkgroups (Enter to stay read-only): '
    [ -n "$ANSWER" ] && conf api_key "$ANSWER"
  fi
  if [ "$NEW" = 1 ] && [ -n "$(conf api_key)" ]; then
    secret 'PIN for talkgroup changes (Enter for none: anyone on your network could change them): '
    [ -n "$ANSWER" ] && conf pin "$ANSWER"
  fi
  ANSWER=
fi

if [ "$METHOD" = apt ]; then
  # --- the package, from dmrdash's apt repo ----------------------------------
  # the package ships these two as well; they're here first so apt can find it and check its signature
  install -m 0644 "$SRC/packaging/dmrdash-archive-keyring.gpg" /usr/share/keyrings/dmrdash-archive-keyring.gpg
  install -m 0644 "$SRC/packaging/dmrdash.sources" /etc/apt/sources.list.d/dmrdash.sources
  apt-get update -o Dir::Etc::SourceList=/etc/apt/sources.list.d/dmrdash.sources -o Dir::Etc::SourceParts=- \
    -o APT::Get::List-Cleanup=0
  # files from an earlier install.sh are replaced, and its units removed, by the package
  DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::=--force-confold dmrdash
else
  # --- files -----------------------------------------------------------------
  install -d -m 0755 "$DEST"
  install -m 0644 "$SRC/dmrdash.py" "$SRC/index.html" "$DEST/"
  install -d -m 0755 "$DEST/vendor"
  install -m 0644 "$SRC/vendor/xterm.mjs" "$SRC/vendor/addon-fit.mjs" "$SRC/vendor/xterm.css" "$DEST/vendor/"
  install -m 0755 "$SRC/rxoffset.py" "$SRC/rflevel.py" "$SRC/master.py" "$SRC/power.py" "$SRC/tui.py" "$DEST/"
  ln -sf "$DEST/tui.py" /usr/local/bin/dmrdash-tui
  install -m 0644 "$SRC/dmrdash.service" "$UNIT"
  # root-run helpers: three edit MMDVM.ini as the dashboard asks (only RXOffset=, RFLevel= or the master Address=, then a DMRHost restart),
  # the fourth restarts DMRHost, or restarts or shuts down the Pi
  for u in $HELPER_UNITS; do install -m 0644 "$SRC/$u" "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  systemctl enable dmrdash >/dev/null 2>&1
  systemctl restart dmrdash
  systemctl enable --now dmrdash-rxoffset.path dmrdash-master.path dmrdash-power.path dmrdash-rflevel.path >/dev/null 2>&1
fi

HOSTUNIT=$(conf unit)
HOSTUNIT=${HOSTUNIT:-dmrhost}
systemctl cat "$HOSTUNIT.service" >/dev/null 2>&1 \
  || say "Note: no $HOSTUNIT.service here. Set \"unit\" in $CONF to the hotspot's systemd unit."
CSV=$(conf user_csv)
CSV=${CSV:-/usr/local/etc/user.csv}
[ -r "$CSV" ] || say "Note: $CSV not found, so calls show IDs without names. Point \"user_csv\" at DMRHost's DMR ID file."
[ "$(conf device_id)" != 0 ] || say "Note: set device_id in $CONF, then: sudo systemctl restart dmrdash"

PORT=$(conf port)
say "dmrdash $(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$DEST/dmrdash.py") running on http://$(hostname).local:${PORT:-8080}/"
