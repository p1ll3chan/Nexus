# Nexus 0.3.0-alpha1 — Nexus Remote 0.1 (pre-release)

PhoneBridge is now **Nexus**. Alongside notifications, calls and GPS, a paired Android phone can
now check on your Linux computer: system status and a read-only process view.

**This is an alpha for testers.** Read-only by design. Linux + Android only.

## Highlights
- **Nexus Remote**: CPU, memory, disk, load and uptime; sortable, paged process list; per-process details.
- **Per-device credentials** with instant revoke and rotate, **server-enforced permissions**
  (default deny), and a **metadata-only audit log**.
- Hardened systemd user service; installer for Arch and Ubuntu; Arch `PKGBUILD`.

## What Remote cannot do
Run commands, open a shell, read/write files, signal or stop processes, use root, or see
command lines, environment variables or other users' names. No such operation exists.

## Upgrading from PhoneBridge
Re-run `phone-bridge/scripts/install.sh` and install the new APK. Existing shared-token phones keep
working for notifications/calls/GPS. Then: `phone-bridge remote pair my-phone --grant remote.read`.

**Breaking:** the placeholder token `change-me` is rejected; the dashboard's `/api/locate` is POST-only.

## Known limitations
- WebSocket is not TLS; use it only inside Tailscale.
- Credential is copied by hand (no QR yet).
- No instrumented Android UI tests yet.
- User scripts and any mutating action are deferred.

## Verification
124 Python tests, 26 Android unit tests, Lint 0 errors, Ubuntu 24.04 and Arch container installs.
See `docs/security/NEXUS_SECURITY_TEST_REPORT.md`.
