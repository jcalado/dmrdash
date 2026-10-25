#!/bin/sh
# Build dmrdash_<version>_all.deb into dist/. Needs dpkg-deb. Run from anywhere.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VER=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/dmrdash.py")
[ -n "$VER" ] || { echo "no VERSION in dmrdash.py" >&2; exit 1; }
OUT="$ROOT/dist"
PKG=$(mktemp -d)
chmod 0755 "$PKG"
trap 'rm -rf "$PKG"' EXIT

install -d "$PKG/DEBIAN" "$PKG/opt/dmrdash" "$PKG/opt/dmrdash/vendor" "$PKG/usr/bin" "$PKG/usr/lib/systemd/system" "$PKG/usr/share/doc/dmrdash"
install -m 0644 "$ROOT/dmrdash.py" "$ROOT/index.html" "$PKG/opt/dmrdash/"
install -m 0644 "$ROOT/vendor/xterm.mjs" "$ROOT/vendor/addon-fit.mjs" "$ROOT/vendor/xterm.css" "$PKG/opt/dmrdash/vendor/"
install -m 0755 "$ROOT/rxoffset.py" "$ROOT/rflevel.py" "$ROOT/master.py" "$ROOT/power.py" "$ROOT/tui.py" "$PKG/opt/dmrdash/"
ln -s /opt/dmrdash/tui.py "$PKG/usr/bin/dmrdash-tui"
install -m 0644 "$ROOT/dmrdash.service" "$ROOT/dmrdash-rxoffset.path" "$ROOT/dmrdash-rxoffset.service" "$ROOT/dmrdash-master.path" "$ROOT/dmrdash-master.service" "$ROOT/dmrdash-power.path" "$ROOT/dmrdash-power.service" "$ROOT/dmrdash-rflevel.path" "$ROOT/dmrdash-rflevel.service" "$PKG/usr/lib/systemd/system/"
install -m 0644 "$ROOT/dmrdash.conf.example" "$ROOT/README.md" "$ROOT/NOTICE" "$ROOT/licenses/Saira-OFL.txt" "$ROOT/licenses/DSEG-OFL.txt" "$ROOT/licenses/xterm-LICENSE.txt" "$PKG/usr/share/doc/dmrdash/"
install -m 0644 "$ROOT/LICENSE" "$PKG/usr/share/doc/dmrdash/copyright"
for s in postinst prerm postrm; do install -m 0755 "$ROOT/packaging/$s" "$PKG/DEBIAN/"; done

cat > "$PKG/DEBIAN/control" <<CTL
Package: dmrdash
Version: $VER
Architecture: all
Maintainer: Joel Calado <joelcalado@gmail.com>
Depends: python3 (>= 3.9), systemd
Section: hamradio
Priority: optional
Homepage: https://github.com/jcalado/dmrdash
Description: LAN dashboard for BrandMeister DMRHost hotspots
 Live calls per timeslot from the DMRHost journal, last heard, a RAM-only
 live log, static talkgroup management through the BrandMeister API, and an
 RF BER trend with RX offset tuning, BrandMeister master switching, and
 restart / shut down of the Pi.
 A terminal view, dmrdash-tui, shows the same over SSH.
 Python standard library only; nothing is written to the SD card in normal use.
CTL

mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$PKG" "$OUT/dmrdash_${VER}_all.deb"
