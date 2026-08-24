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

NODE_BIN="$(command -v node || true)"
NODE_MAJOR="$([[ -n "$NODE_BIN" ]] && "$NODE_BIN" -e 'console.log(process.versions.node.split(".")[0])' || echo 0)"
if [[ "$NODE_MAJOR" -lt 18 ]]; then
  # Don't touch the system-wide node (this box's default is v12, and other
  # services here may depend on it) -- install an isolated Node 20 just for
  # building this project's UI.
  echo "==> System node is too old (or missing) for Vite -- installing an isolated Node 20 at /opt/gemini-brain-node20"
  if [[ ! -x /opt/gemini-brain-node20/bin/node ]]; then
    curl -fsSL https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-x64.tar.xz -o /tmp/node20.tar.xz
    mkdir -p /opt/gemini-brain-node20
    tar -xf /tmp/node20.tar.xz -C /opt/gemini-brain-node20 --strip-components=1
    rm -f /tmp/node20.tar.xz
  fi
  NODE_BIN="/opt/gemini-brain-node20/bin/node"
fi

echo "==> Creating system user '$APP_USER'"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# The repo directory must stay owned by whoever runs `git pull` here
# (typically the login user, e.g. `ubuntu`) -- a full chown to $APP_USER
# breaks `.git` permissions for that user. Instead, add $APP_USER to the
# repo owner's group and share access via group perms.
REPO_OWNER="$(stat -c '%U' "$APP_DIR")"
if [[ "$REPO_OWNER" != "root" && "$REPO_OWNER" != "$APP_USER" ]]; then
  usermod -aG "$REPO_OWNER" "$APP_USER"
  chgrp -R "$REPO_OWNER" "$APP_DIR"
  chmod -R g+rwX "$APP_DIR"
else
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

echo "==> Building Python venv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -e "$APP_DIR"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "==> No .env found -- copying .env.example. YOU MUST EDIT IT with real secrets before starting the API."
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

echo "==> Building UI (using $NODE_BIN)"
NODE_DIR="$(dirname "$NODE_BIN")"
cd "$APP_DIR/ui"
sudo -u "$APP_USER" env PATH="$NODE_DIR:$PATH" npm ci
sudo -u "$APP_USER" env PATH="$NODE_DIR:$PATH" npm run build
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
echo "  4. Install the nginx site (uses 13-205-100-89.sslip.io as server_name,"
echo "     NOT this instance's own amazonaws.com DNS name -- Let's Encrypt"
echo "     refuses to issue certs for *.amazonaws.com. sslip.io resolves to"
echo "     this instance's IP with zero setup. Coexists with the existing"
echo "     'accutax' site -- see deploy/nginx-gemini-brain.conf):"
echo "       sudo apt-get install -y certbot python3-certbot-nginx"
echo "       sudo cp deploy/nginx-gemini-brain.conf /etc/nginx/sites-available/gemini-brain"
echo "       sudo ln -s /etc/nginx/sites-available/gemini-brain /etc/nginx/sites-enabled/"
echo "       sudo nginx -t && sudo systemctl reload nginx"
echo "  5. Get a free TLS cert for that hostname:"
echo "       sudo certbot --nginx -d 13-205-100-89.sslip.io"
echo "  6. Visit https://13-205-100-89.sslip.io/"
echo "========================================================================"
