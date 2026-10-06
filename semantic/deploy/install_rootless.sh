#!/usr/bin/env bash
# Install the Cube semantic layer as rootless Podman Quadlet units for a dedicated "cube" user.
#
# Run as root from the repo checkout on the VM:
#   semantic/deploy/install_rootless.sh            # install or update, do not (re)start
#   semantic/deploy/install_rootless.sh --start    # install or update, then (re)start
#
# Needs semantic/cube.env (copy cube.env.example, fill in, chmod 600) and the
# cube_reader database role (semantic/dba/cube_reader.sql, run by the DBA).
# Idempotent. Phase 3's refresh worker (deploy/quadlet/phase3) is not installed.
set -euo pipefail

CUBE_USER=cube
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # .../semantic

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f "$SRC/cube.env" ]] || { echo "Missing $SRC/cube.env (copy cube.env.example and fill it in)." >&2; exit 1; }
grep -Eq '^CUBEJS_API_SECRET=.{32,}$' "$SRC/cube.env" \
  || { echo "CUBEJS_API_SECRET in cube.env is missing or shorter than 32 characters." >&2; exit 1; }
command -v podman >/dev/null || { echo "podman is not installed." >&2; exit 1; }

if ! id "$CUBE_USER" &>/dev/null; then
  useradd --create-home --shell /sbin/nologin "$CUBE_USER"
fi
# Rootless containers need subordinate UID/GID ranges.
grep -q "^$CUBE_USER:" /etc/subuid || usermod --add-subuids 200000-265535 "$CUBE_USER"
grep -q "^$CUBE_USER:" /etc/subgid || usermod --add-subgids 200000-265535 "$CUBE_USER"
# Keep the user's services running without a login session, and start them on boot.
loginctl enable-linger "$CUBE_USER"

HOME_DIR="$(getent passwd "$CUBE_USER" | cut -d: -f6)"
UNIT_DIR="$HOME_DIR/.config/containers/systemd"
install -d -o "$CUBE_USER" -g "$CUBE_USER" -m 700 "$HOME_DIR/semantic" "$HOME_DIR/.config" \
  "$HOME_DIR/.config/containers" "$UNIT_DIR"

rm -rf "$HOME_DIR/semantic/model"
cp -a "$SRC/model" "$HOME_DIR/semantic/model"
install -m 644 "$SRC/cube.js" "$HOME_DIR/semantic/cube.js"
install -m 600 "$SRC/cube.env" "$HOME_DIR/semantic/cube.env"
chown -R "$CUBE_USER:$CUBE_USER" "$HOME_DIR/semantic"
install -o "$CUBE_USER" -g "$CUBE_USER" -m 644 \
  "$SRC/deploy/quadlet/cube.network" "$SRC/deploy/quadlet/cubestore.volume" \
  "$SRC/deploy/quadlet/cubestore.container" "$SRC/deploy/quadlet/cube-api.container" "$UNIT_DIR/"

CUBE_UID="$(id -u "$CUBE_USER")"
as_cube() { sudo -u "$CUBE_USER" XDG_RUNTIME_DIR="/run/user/$CUBE_UID" "$@"; }
# Wait for the user's systemd manager that enable-linger starts.
for _ in $(seq 1 20); do [[ -S "/run/user/$CUBE_UID/systemd/private" ]] && break; sleep 0.5; done
as_cube systemctl --user daemon-reload

if [[ "${1:-}" == "--start" ]]; then
  as_cube systemctl --user restart cubestore.service cube-api.service
  as_cube systemctl --user --no-pager --lines=0 status cube-api.service || true
fi
echo "Installed for user $CUBE_USER."
echo "Logs:   sudo -u $CUBE_USER XDG_RUNTIME_DIR=/run/user/$CUBE_UID journalctl --user -u cube-api -f"
echo "Status: sudo -u $CUBE_USER XDG_RUNTIME_DIR=/run/user/$CUBE_UID systemctl --user status cube-api cubestore"
