#!/usr/bin/env bash
# provision.sh — one-time setup for the Gemini Brain portal on this EC2 instance.
# Both the API and the built React UI are served from this same instance via nginx.
#
# NOTE: this instance is shared with other, unrelated services (an API on
# :8000, an OCR pipeline on :8001, Ollama on :11434, vLLM, Docker containers).
# This script deliberately does NOT touch firewall rules (no ufw -- AWS
# Security Groups are the only firewall layer here; enabling ufw on a shared
# box risks silently cutting off those other services) and does NOT install a
# DB-tunnel systemd unit (the tunnel to 106.51.80.81 is already running ad hoc
# on 127.0.0.1:5435 and is intentionally left alone).
#
# Usage (run as root, from inside the repo checked out at /opt/gemini-brain):
#   git clone <your-repo-url> /opt/gemini-brain   # or rsync/scp the project here
#   cd /opt/gemini-brain
#   sudo bash deploy/provision.sh
#
# What it does:
#   1. Installs system packages (python3, node -- nginx/certbot are already
#      present on this box, apt-get install is a harmless no-op for those)
#   2. Creates a dedicated non-root `geminibrain` system user (app never runs as root)
#   3. Builds a Python venv + installs the package
#   4. Builds the React UI (npm run build)
#   5. Installs the gemini-brain-api systemd unit (port 8010, not 8000)
#
# Nginx site installation and TLS are separate manual steps (see
# deploy/nginx-gemini-brain.conf) since this box's existing nginx config needs
# to be checked first rather than overwritten.
#
# Debian/Ubuntu only (uses apt).

set -euo pipefail

APP_DIR="/opt/gemini-brain"
APP_USER="geminibrain"

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root (sudo bash deploy/provision.sh)" >&2
  exit 1
fi

if [[ "$(pwd)" != "$APP_DIR" ]]; then
  echo "Expected to be run from $APP_DIR (clone/copy the repo there first)." >&2
  echo "Currently in $(pwd)." >&2
  exit 1
fi

echo "==> Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip curl git ca-certificates

if ! command -v node >/dev/null 2>&1; then
  echo "==> Installing Node.js 20.x"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

echo "==> Creating system user '$APP_USER'"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Building Python venv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -e "$APP_DIR"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "==> No .env found -- copying .env.example. YOU MUST EDIT IT with real secrets before starting the API."
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

echo "==> Building UI"
cd "$APP_DIR/ui"
sudo -u "$APP_USER" npm ci
sudo -u "$APP_USER" npm run build
cd "$APP_DIR"

echo "==> Installing systemd unit (API only -- DB tunnel is left as the existing ad hoc process)"
cp "$APP_DIR/deploy/gemini-brain-api.service" /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "========================================================================"
echo "Provisioning done. Remaining manual steps:"
echo "  1. Edit $APP_DIR/.env with real secrets (GEMINI_API_KEY, JWT_SECRET,"
echo "     DB_PASSWORD, ACCUTAX_AUTH_TOKEN, etc). Keep DB_HOST=127.0.0.1 and"
echo "     DB_PORT=5435 -- that already matches the existing ad hoc tunnel on"
echo "     this box. Confirm it's still up: ss -tlnp | grep 5435"
echo "  2. Start the service:"
echo "       systemctl enable --now gemini-brain-api"
echo "  3. Check status/logs:"
echo "       systemctl status gemini-brain-api"
echo "       journalctl -u gemini-brain-api -f"
echo "       curl http://127.0.0.1:8010/api/v1/health"
echo "  4. Check the EXISTING nginx config before installing our site --"
echo "     see the warning at the top of deploy/nginx-gemini-brain.conf."
echo "     Once safe: cp it to /etc/nginx/sites-available/, symlink into"
echo "     sites-enabled, nginx -t, systemctl reload nginx."
echo "  5. Get a TLS cert for this box's public DNS name (EC2 gives you a free"
echo "     one already -- no domain purchase needed), or your own domain if"
echo "     you point one at this instance's IP:"
echo "       certbot --nginx -d ec2-XX-XX-XX-XX.<region>.compute.amazonaws.com"
echo "  6. Visit https://<that-domain>/ -- the UI and API are both served here."
echo "========================================================================"
