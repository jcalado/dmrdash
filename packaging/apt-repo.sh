#!/bin/sh
# Add packages to dmrdash's apt repo and sign it: packaging/apt-repo.sh REPO_DIR DEB...
#
# REPO_DIR is a checkout of the gh-pages branch, which GitHub Pages serves as https://jcalado.github.io/dmrdash.
# Needs apt-ftparchive (apt-utils) and gpg with the signing key; APT_KEY_ID picks the key when there are several.
# Keeps the newest $KEEP versions in the pool, so going back a release stays possible.
#
# The key, once: gpg --batch --passphrase '' --quick-gen-key "dmrdash apt <joelcalado@gmail.com>" ed25519 sign never
#   gpg --export "dmrdash apt" > packaging/dmrdash-archive-keyring.gpg              (committed; the package ships it)
#   gpg --armor --export-secret-keys "dmrdash apt" | gh secret set APT_SIGNING_KEY   (for the release workflow)
set -eu
[ $# -ge 2 ] || { echo "usage: $0 REPO_DIR DEB..." >&2; exit 1; }
REPO=$1; shift
KEEP=${KEEP:-5}
ARCHS="all amd64 arm64 armhf i386"
POOL=pool/main/d/dmrdash

mkdir -p "$REPO/$POOL"
for deb in "$@"; do cp "$deb" "$REPO/$POOL/"; done
cd "$REPO"
# shellcheck disable=SC2012
ls "$POOL" | sed -n 's/^dmrdash_\(.*\)_all\.deb$/\1/p' | sort -V | head -n "-$KEEP" | while read -r v; do
  rm -f "$POOL/dmrdash_${v}_all.deb"
done

rm -rf dists
for a in $ARCHS; do
  # an arch: all package belongs in every architecture's index: apt reads its own, the dashboard reads binary-all
  mkdir -p "dists/stable/main/binary-$a"
  apt-ftparchive --arch "$a" packages pool > "dists/stable/main/binary-$a/Packages"
  gzip -9nk "dists/stable/main/binary-$a/Packages"
done
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=dmrdash -o APT::FTPArchive::Release::Label=dmrdash \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o "APT::FTPArchive::Release::Architectures=$ARCHS" -o APT::FTPArchive::Release::Components=main \
  -o "APT::FTPArchive::Release::Description=dmrdash, a LAN dashboard for BrandMeister DMRHost hotspots" \
  release dists/stable > Release.tmp
mv Release.tmp dists/stable/Release
set -- --batch --yes ${APT_KEY_ID:+--local-user "$APT_KEY_ID"}
gpg "$@" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg "$@" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
touch .nojekyll  # serve the files as they are
ls "$POOL"
