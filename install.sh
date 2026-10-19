#!/bin/sh
# Install or update dmrdash on the Pi. Run from this directory: sudo ./install.sh
set -e
id dmrdash >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin dmrdash
install -d -m 0755 /opt/dmrdash
install -m 0644 dmrdash.py index.html /opt/dmrdash/
if [ ! -f /etc/dmrdash.conf ]; then
  install -m 0640 -g dmrdash dmrdash.conf.example /etc/dmrdash.conf
fi
chown root:dmrdash /etc/dmrdash.conf && chmod 0640 /etc/dmrdash.conf
install -m 0644 dmrdash.service /etc/systemd/system/dmrdash.service
systemctl daemon-reload
systemctl enable dmrdash >/dev/null 2>&1
systemctl restart dmrdash
echo "dmrdash running on http://$(hostname).local:$(python3 -c 'import json;print(json.load(open("/etc/dmrdash.conf")).get("port",8080))')/"
