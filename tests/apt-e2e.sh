#!/bin/sh
# The package's whole life, in a throwaway Debian (as root, no systemd running):
# install.sh installs it from a local apt repo signed with a test key, the dashboard sees a newer
# version in that repo, update.py upgrades to it, and removing and purging it leave apt working.
#
#   docker run --rm -v "$PWD":/src:ro debian:trixie sh /src/tests/apt-e2e.sh
set -eu
SRC=${1:-/src}
W=$(mktemp -d)
PORT=8765
export DEBIAN_FRONTEND=noninteractive GNUPGHOME="$W/gnupg"
ok() { printf 'ok: %s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

apt-get update -qq && apt-get install -y -qq apt-utils gpg python3 systemd >/dev/null

# a test key, and two releases of the tree that trust it and point at the local repo
mkdir -m 0700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key "dmrdash test" ed25519 sign never 2>/dev/null
gpg --export "dmrdash test" > "$W/keyring.gpg"
OLD=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/dmrdash.py")
NEW="$OLD.1"
for v in "$OLD" "$NEW"; do
  mkdir -p "$W/tree-$v" && (cd "$SRC" && tar --exclude=.git --exclude=dist --exclude=node_modules -cf - .) | tar -xf - -C "$W/tree-$v"
  sed -i "s/^VERSION = .*/VERSION = \"$v\"/" "$W/tree-$v/dmrdash.py"
  sed -i "s|^URIs: .*|URIs: http://localhost:$PORT|" "$W/tree-$v/packaging/dmrdash.sources"
  cp "$W/keyring.gpg" "$W/tree-$v/packaging/dmrdash-archive-keyring.gpg"
  sh "$W/tree-$v/packaging/build-deb.sh" >/dev/null
done

mkdir "$W/repo"
sh "$SRC/packaging/apt-repo.sh" "$W/repo" "$W/tree-$OLD/dist/dmrdash_${OLD}_all.deb" >/dev/null
(cd "$W/repo" && exec python3 -m http.server "$PORT" >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1

# --- install.sh, from an unpacked release: the package, from the repo
printf '[General]\nId=268051301\n' > /etc/MMDVM.ini
lists() { find /var/lib/apt/lists -name '*_Packages*' | wc -l; }
LISTS=$(lists)
sh "$W/tree-$OLD/install.sh" --yes >/dev/null
[ "$(dpkg-query -W -f='${Version}' dmrdash)" = "$OLD" ] || die "install.sh didn't install $OLD"
grep -q '"device_id": 268051301' /etc/dmrdash.conf || die "device ID not taken from MMDVM.ini"
grep -q "localhost:$PORT" /etc/apt/sources.list.d/dmrdash.sources || die "no apt source"
systemctl is-enabled -q dmrdash-update.path || die "update helper not enabled"
NOTES=$(apt-get update 2>&1 | grep -E '^(W|E|N):' || true)
[ -z "$NOTES" ] || die "apt complains about the repo: $NOTES"
ok "install.sh installed $OLD from the repo"

# --- a new release: the dashboard sees it in binary-all, update.py installs it
sh "$SRC/packaging/apt-repo.sh" "$W/repo" "$W/tree-$NEW/dist/dmrdash_${NEW}_all.deb" >/dev/null
mkdir -p "$W/state"
LATEST=$(DMRDASH_CONF=/etc/dmrdash.conf STATE_DIRECTORY="$W/state" python3 -I -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dmrdash", "/opt/dmrdash/dmrdash.py")
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
d.PACKAGES_URL = sys.argv[1] + "/dists/stable/main/binary-all/Packages"
r = d.update_check()
print(r["latest"] if d.update_available() else "none: %s" % r)' "http://localhost:$PORT")
[ "$LATEST" = "$NEW" ] || die "the dashboard saw $LATEST, not $NEW"
ok "the dashboard sees $NEW"

mkdir -p /var/lib/dmrdash
echo stale > /var/lib/dmrdash/update.request
python3 -I /opt/dmrdash/update.py 2>/dev/null && die "update.py took a bad request"
echo dmrdash > /var/lib/dmrdash/update.request
touch -d '2 minutes ago' /var/lib/dmrdash/update.request
python3 -I /opt/dmrdash/update.py 2>/dev/null && die "update.py took a stale request"
echo dmrdash > /var/lib/dmrdash/update.request
python3 -I /opt/dmrdash/update.py >/dev/null || die "update.py failed: $(cat /var/lib/dmrdash/update.status)"
[ "$(dpkg-query -W -f='${Version}' dmrdash)" = "$NEW" ] || die "not upgraded to $NEW"
python3 -I -c 'import json, sys; s = json.load(open("/var/lib/dmrdash/update.status")); sys.exit(not (s["state"], s["old"], s["new"]) == ("done",) + tuple(sys.argv[1:]))' "$OLD" "$NEW" \
  || die "status: $(cat /var/lib/dmrdash/update.status)"
[ "$(lists)" -ge "$LISTS" ] || die "the update dropped other repos' lists"
grep -q '"device_id": 268051301' /etc/dmrdash.conf || die "the update lost the config"
ok "update.py upgraded $OLD to $NEW"

# --- "Enabled: no" survives an upgrade (here, a reinstall)
echo 'Enabled: no' >> /etc/apt/sources.list.d/dmrdash.sources
apt-get install -y -qq --reinstall dmrdash >/dev/null
grep -qx 'Enabled: no' /etc/apt/sources.list.d/dmrdash.sources || die "Enabled: no was dropped"
ok "Enabled: no kept"

# --- remove takes the source with the keyring; purge the rest
apt-get remove -y -qq dmrdash >/dev/null
[ ! -e /etc/apt/sources.list.d/dmrdash.sources ] || die "remove left the apt source"
apt-get update -qq || die "apt update fails after remove"
[ -f /etc/dmrdash.conf ] || die "remove dropped the config"
apt-get purge -y -qq dmrdash >/dev/null
if [ -e /etc/dmrdash.conf ] || id dmrdash >/dev/null 2>&1; then die "purge left the config or the user"; fi
ok "remove and purge"
