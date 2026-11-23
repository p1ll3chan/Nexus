# Packaging and Install Paths (Day 27)

## Paths

| Target | Method | Runs as | Service |
|---|---|---|---|
| Arch / Manjaro (per user) | `phone-bridge/scripts/install.sh` → venv in `~/.local/share/phone-bridge/venv` | user | `~/.config/systemd/user/phone-bridge.service` |
| Arch / Manjaro (system package) | `packaging/arch/PKGBUILD` → `/usr/bin/phone-bridge`, `/usr/bin/nexus` | user | `/usr/lib/systemd/user/phone-bridge.service` (enable per user) |
| Ubuntu 24.04+ | `install.sh` (needs `python3-venv`) | user | per-user unit |
| Ubuntu 22.04 | `install.sh` with `PYTHON=python3.11` (system Python 3.10 is refused) | user | per-user unit |

- **Update:** re-run `install.sh` (keeps config, applies `nexus.db` migrations with a backup
  of an older schema, restarts the service). Arch package: `makepkg -si` again.
- **Uninstall:** `scripts/uninstall.sh` (keeps config/data) or `--purge`. Arch: `pacman -R nexus-phone-bridge`
  (user data under `~` is never touched by pacman).
- **Never root:** both scripts refuse to run as root. No capabilities, setuid or sudo are used.
- **Not public by default:** the installer binds the WebSocket to the detected Tailscale IP,
  else `127.0.0.1` (with a note to edit). The map stays on `127.0.0.1`.
- **Data:** `config.toml` 0600, data dir 0700, `nexus.db` 0600; service `UMask=0077`.

## Service hardening (user unit)

`NoNewPrivileges`, `UMask=0077`, `LockPersonality`, `RestrictRealtime`, `RestrictSUIDSGID`,
`SystemCallArchitectures=native`, `RestrictNamespaces`. Verified on the author's Manjaro host
with `systemd-run --user -p … phone-bridge remote run system.status` (ran normally).
`MemoryDenyWriteExecute` was dropped because it can break Python C extensions using libffi.
`PrivateTmp`/`ProtectSystem` are documented but commented out (need mount namespaces that
unprivileged user managers may lack).

## Test evidence (2026-11-23)

| Environment | What ran | Result |
|---|---|---|
| `ubuntu:24.04` container, Python 3.12 | install (`NEXUS_NO_SYSTEMD=1`) → modes 600/700/600 → `nexus remote pair` → `remote run system.status`/`process.list` → re-run installer (update) → `uninstall.sh` (data kept) → `uninstall.sh --purge` (nothing left) | pass |
| `ubuntu:22.04` container, Python 3.10 | install | correctly refused: "Python 3.11+ is required" |
| `archlinux:latest` container | `makepkg` (build + `check()`: **124 tests OK**) → `pacman -U` → new user: `init-config`, `remote pair`, `remote run system.status`; unit has `ExecStart=/usr/bin/phone-bridge serve` | pass |
| Manjaro host (author) | hardening flags via `systemd-run --user` | pass |

**Not tested:** systemd *enable/start* inside containers (no user systemd there); a real
Ubuntu desktop session; the `PKGBUILD` download step (the `v0.3.0a1` tag does not exist yet;
the test used a locally built tarball, `sha256sums=('SKIP')` must be replaced at release).
The author's live install was deliberately not modified.
