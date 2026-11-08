#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/phone-bridge"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/phone-bridge"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "==> Installing phone-bridge into $DATA_DIR"
python3 -m venv "$DATA_DIR/venv"
"$DATA_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$DATA_DIR/venv/bin/pip" install "$ROOT"

echo "==> Writing configuration to $CONFIG_DIR/config.toml"
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    TOKEN="$("$DATA_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    mkdir -p "$CONFIG_DIR"
    sed "s/token = \"change-me\"/token = \"$TOKEN\"/" "$ROOT/config.example.toml" > "$CONFIG_DIR/config.toml"
    echo "    shared secret: $TOKEN"
else
    echo "    existing config kept"
fi

echo "==> Installing systemd user unit"
mkdir -p "$UNIT_DIR"
sed "s|%h/.local/bin/phone-bridge|$DATA_DIR/venv/bin/phone-bridge|" \
    "$ROOT/systemd/phone-bridge.service" > "$UNIT_DIR/phone-bridge.service"

systemctl --user daemon-reload
systemctl --user enable --now phone-bridge
echo "==> Done. Status:"
systemctl --user --no-pager status phone-bridge || true
