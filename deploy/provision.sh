#!/usr/bin/env bash
# provision.sh — one-time setup for the Gemini Brain portal on this EC2 instance.
# Both the API and the built React UI are served from this same instance via nginx.
#
# Usage (run as root, from inside the repo checked out at /opt/gemini-brain):
#   git clone <your-repo-url> /opt/gemini-brain   # or rsync/scp the project here
#   cd /opt/gemini-brain
#   sudo bash deploy/provision.sh
#
# What it does:
#   1. Installs system packages (python3, node, nginx, certbot, ufw)
#   2. Creates a dedicated non-root `geminibrain` system user (app never runs as root)
#   3. Generates an SSH keypair for the DB tunnel -- you must add the printed
#      pubkey to 106.51.80.81's authorized_keys yourself; this script has no
#      way to reach that host on your behalf.
#   4. Builds a Python venv + installs the package
#   5. Builds the React UI (npm run build)
#   6. Installs the systemd units + nginx site from deploy/
#   7. Opens ufw for SSH/80/443 only -- the DB tunnel and API stay on 127.0.0.1,
#      never exposed outside this instance.
#
# Debian/Ubuntu only (uses apt + ufw).

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
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw curl git ca-certificates

if ! command -v node >/dev/null 2>&1; then
  echo "==> Installing Node.js 20.x"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

echo "==> Creating system user '$APP_USER'"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

SSH_KEY="/home/$APP_USER/.ssh/id_ed25519"
if [[ ! -f "$SSH_KEY" ]]; then
  echo "==> Generating SSH key for the DB tunnel"
  sudo -u "$APP_USER" mkdir -p "/home/$APP_USER/.ssh"
  sudo -u "$APP_USER" ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "gemini-brain-db-tunnel"
fi
echo ""
echo "########################################################################"
echo "# ACTION NEEDED: add this public key to authorized_keys on 106.51.80.81"
echo "# (as the 'root' user there, sshd on port 7676):"
echo "########################################################################"
cat "$SSH_KEY.pub"
echo "########################################################################"
echo ""

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

echo "==> Installing systemd units"
cp "$APP_DIR/deploy/gemini-brain-db-tunnel.service" /etc/systemd/system/
cp "$APP_DIR/deploy/gemini-brain-api.service" /etc/systemd/system/
systemctl daemon-reload

echo "==> Installing nginx site"
cp "$APP_DIR/deploy/nginx-gemini-brain.conf" /etc/nginx/sites-available/gemini-brain
ln -sf /etc/nginx/sites-available/gemini-brain /etc/nginx/sites-enabled/gemini-brain
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Configuring firewall (ufw): SSH + HTTP/HTTPS only"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "========================================================================"
echo "Provisioning done. Remaining manual steps:"
echo "  1. Add the SSH pubkey printed above to 106.51.80.81's authorized_keys."
echo "  2. Edit $APP_DIR/.env with real secrets (GEMINI_API_KEY, JWT_SECRET,"
echo "     DB_PASSWORD, ACCUTAX_AUTH_TOKEN, etc). Keep DB_HOST=127.0.0.1 and"
echo "     DB_PORT=5435 -- that's the tunnel this script set up."
echo "  3. Start the services:"
echo "       systemctl enable --now gemini-brain-db-tunnel"
echo "       systemctl enable --now gemini-brain-api"
echo "  4. Check status/logs:"
echo "       systemctl status gemini-brain-db-tunnel gemini-brain-api"
echo "       journalctl -u gemini-brain-api -f"
echo "  5. Get a TLS cert for this box's public DNS name (EC2 gives you a free"
echo "     one already -- no domain purchase needed), or your own domain if"
echo "     you point one at this instance's IP:"
echo "       certbot --nginx -d ec2-XX-XX-XX-XX.<region>.compute.amazonaws.com"
echo "  6. Visit https://<that-domain>/ -- the UI and API are both served here."
echo "========================================================================"
