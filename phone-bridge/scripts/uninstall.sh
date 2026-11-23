#!/usr/bin/env bash
# Remove the per-user Nexus (phone-bridge) install.
#
#   ./scripts/uninstall.sh          remove program + service, KEEP config and data
#   ./scripts/uninstall.sh --purge  also delete config, GPS history, device registry and audit log
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "error: run as the user who installed Nexus, not root." >&2
    exit 1
fi

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/phone-bridge"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/phone-bridge"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/phone-bridge.service"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

if command -v systemctl >/dev/null 2>&1 && [ -f "$UNIT" ]; then
    echo "==> Stopping and disabling the user service"
    systemctl --user disable --now phone-bridge 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload || true
fi

echo "==> Removing program files"
rm -rf "$DATA_DIR/venv"
for link in "$HOME/.local/bin/phone-bridge" "$HOME/.local/bin/nexus"; do
    if [ -L "$link" ]; then rm -f "$link"; fi
done

if [ "$PURGE" = 1 ]; then
    echo "==> Purging configuration and data (GPS history, paired devices, audit log)"
    rm -rf "$CONFIG_DIR"
    rm -f "$DATA_DIR"/locations.db* "$DATA_DIR"/nexus.db*
    rmdir "$DATA_DIR" 2>/dev/null || true
else
    echo "==> Kept $CONFIG_DIR and data in $DATA_DIR (use --purge to delete)"
fi
echo "Done. Also remove the app from your phone, or revoke it first if you keep the data."
