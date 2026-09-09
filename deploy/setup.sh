#!/usr/bin/env bash
#
# One-time setup on a fresh Ubuntu 24.04 server.
# Run as root:   bash setup.sh yourdomain.com you@email.com
#
# Installs Python + Chromium deps, creates a service user, sets up the
# nightly engine run and the web app, and puts Caddy in front for HTTPS
# and a login prompt.
set -euo pipefail

DOMAIN="${1:?usage: setup.sh <domain> <email>}"
EMAIL="${2:?usage: setup.sh <domain> <email>}"
APP_USER=tender
APP_DIR=/opt/tn-tender

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl sudo \
    debian-keyring debian-archive-keyring apt-transport-https

echo "==> Installing Caddy (HTTPS + auth in front of the app)"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq && apt-get install -y -qq caddy

echo "==> Creating service user and directory"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
mkdir -p "$APP_DIR" && chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Installing Python deps + Chromium as $APP_USER"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip playwright
# Two steps, deliberately: install-deps runs apt and must be root, while the
# browser download must be the service user so it lands in that user's cache
# where the engine will actually look for it. "--with-deps" as the service
# user fails, because it cannot apt-get.
"$APP_DIR/venv/bin/playwright" install-deps chromium
sudo -u "$APP_USER" "$APP_DIR/venv/bin/playwright" install chromium

echo "==> Verifying the engine can drive Chromium here"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" - <<'PYCHECK'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
print("    Chromium launches OK")
PYCHECK

echo "==> Installing systemd units"
cp "$(dirname "$0")"/tn-tender-app.service /etc/systemd/system/
cp "$(dirname "$0")"/tn-tender-engine.service /etc/systemd/system/
cp "$(dirname "$0")"/tn-tender-engine.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tn-tender-app.service
systemctl enable --now tn-tender-engine.timer

echo "==> Configuring Caddy for $DOMAIN"
sed -e "s/DOMAIN_PLACEHOLDER/$DOMAIN/" -e "s/EMAIL_PLACEHOLDER/$EMAIL/" \
    "$(dirname "$0")"/Caddyfile > /etc/caddy/Caddyfile
echo
echo "Set a login password now (you'll type it when opening the site):"
read -rsp "  password: " PW; echo
HASH=$(caddy hash-password --plaintext "$PW")
sed -i "s|PASSWORD_HASH_PLACEHOLDER|$HASH|" /etc/caddy/Caddyfile
systemctl restart caddy

echo
echo "Done."
echo "  Site:    https://$DOMAIN   (username: kim)"
echo "  Engine:  runs nightly at 02:30 server time"
echo "  Logs:    journalctl -u tn-tender-engine -n 50"
echo
echo "Now run the first backfill by hand (it takes 2-3 hours):"
echo "  systemctl start tn-tender-engine.service"
