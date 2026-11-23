#!/usr/bin/env bash
# Nexus (phone-bridge) per-user installer for Arch and Ubuntu.
#
#   ./scripts/install.sh            install or update
#   NEXUS_NO_SYSTEMD=1 ./scripts/install.sh   install without touching systemd
#
# Never runs as root. Everything lives in the user's home:
#   venv    ~/.local/share/phone-bridge/venv
#   data    ~/.local/share/phone-bridge/{locations.db,nexus.db}   (mode 0600)
#   config  ~/.config/phone-bridge/config.toml                    (mode 0600)
#   unit    ~/.config/systemd/user/phone-bridge.service
#   links   ~/.local/bin/{phone-bridge,nexus}
set -euo pipefail
umask 077

if [ "$(id -u)" -eq 0 ]; then
    echo "error: do not run the installer as root; Nexus runs as your user." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/phone-bridge"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/phone-bridge"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN_DIR="$HOME/.local/bin"
PYTHON="${PYTHON:-python3}"

distro="unknown"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" arch "*) distro="arch" ;;
        *" ubuntu "*|*" debian "*) distro="ubuntu" ;;
    esac
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: python3 not found." >&2
    [ "$distro" = arch ] && echo "  sudo pacman -S python" >&2
    [ "$distro" = ubuntu ] && echo "  sudo apt install python3 python3-venv" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "error: Python 3.11+ is required (found $("$PYTHON" -V 2>&1))." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    echo "error: the Python venv module is missing." >&2
    [ "$distro" = ubuntu ] && echo "  sudo apt install python3-venv" >&2
    exit 1
fi

echo "==> Installing Nexus (phone-bridge) into $DATA_DIR/venv  [distro: $distro]"
mkdir -p "$DATA_DIR" "$CONFIG_DIR" "$BIN_DIR"
chmod 700 "$DATA_DIR" "$CONFIG_DIR"
if [ ! -x "$DATA_DIR/venv/bin/python" ]; then
    "$PYTHON" -m venv "$DATA_DIR/venv"
fi
"$DATA_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$DATA_DIR/venv/bin/python" -m pip install --quiet --upgrade "$ROOT"
ln -sf "$DATA_DIR/venv/bin/phone-bridge" "$BIN_DIR/phone-bridge"
ln -sf "$DATA_DIR/venv/bin/phone-bridge" "$BIN_DIR/nexus"

CONFIG="$CONFIG_DIR/config.toml"
if [ ! -f "$CONFIG" ]; then
    echo "==> Writing configuration to $CONFIG"
    TOKEN="$("$DATA_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    HOST="127.0.0.1"
    if command -v tailscale >/dev/null 2>&1; then
        TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
        [ -n "$TS_IP" ] && HOST="$TS_IP"
    fi
    NAME="$(uname -n | tr -c 'A-Za-z0-9._\n-' '-' | cut -c1-64)"
    sed -e "s|^token = \"change-me\"|token = \"$TOKEN\"|" \
        -e "s|^host = \"0.0.0.0\"|host = \"$HOST\"|" \
        -e "s|^device_name = \"arch-laptop\"|device_name = \"${NAME:-linux}\"|" \
        "$ROOT/config.example.toml" > "$CONFIG"
    echo "    listening address: $HOST:8765"
    [ "$HOST" = "127.0.0.1" ] && echo "    (Tailscale IP not detected: edit 'host' in $CONFIG so your phone can connect)"
    echo "    legacy shared secret written to the config (pair devices instead: see below)"
else
    echo "==> Keeping existing configuration $CONFIG"
    if grep -qiE '^\s*token\s*=\s*"(change-me|changeme|change_me)"' "$CONFIG"; then
        echo "    WARNING: token is the placeholder; Nexus will refuse to start until you set one:" >&2
        echo "             phone-bridge token   # then paste into $CONFIG" >&2
    fi
fi
chmod 600 "$CONFIG"

# Apply nexus.db migrations now (backs up an older schema first).
"$DATA_DIR/venv/bin/phone-bridge" --config "$CONFIG" remote migrate >/dev/null

if [ "${NEXUS_NO_SYSTEMD:-0}" = "1" ] || ! command -v systemctl >/dev/null 2>&1; then
    echo "==> Skipping systemd (run manually: phone-bridge serve)"
else
    echo "==> Installing systemd user unit"
    mkdir -p "$UNIT_DIR"
    sed "s|@EXEC@|$DATA_DIR/venv/bin/phone-bridge|" \
        "$ROOT/systemd/phone-bridge.service" > "$UNIT_DIR/phone-bridge.service"
    systemctl --user daemon-reload
    systemctl --user enable phone-bridge >/dev/null
    systemctl --user restart phone-bridge
    systemctl --user --no-pager --lines=0 status phone-bridge || true
fi

cat <<EOF

Done. Next steps:
  1. Pair your phone (prints a one-time credential to paste into the app):
       phone-bridge remote pair my-phone --grant remote.read
  2. In the Android app: Settings > Nexus device credential, then "Save & connect".
  3. Open "Nexus Remote" on the phone.
Revoke at any time:  phone-bridge remote revoke <device-id>   (or: remote revoke-all)
EOF
