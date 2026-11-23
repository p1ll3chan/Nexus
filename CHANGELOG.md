# Changelog

## 0.3.0-alpha1 — Nexus Remote 0.1 (unreleased, release candidate)

PhoneBridge is now **Nexus**. Agent `0.3.0a1`, Android `0.3.0-alpha1` (versionCode 3).

### Added
- **Nexus Remote (read-only):** `system.status`, `process.list`, `process.inspect` from the
  phone, over the existing WebSocket. Fixed allow-list, strict schemas, timeouts, output caps.
- **Per-device identity:** `phone-bridge remote pair|devices|grant|ungrant|rotate|revoke|revoke-all`.
  Secrets hashed on Linux, Keystore-encrypted on Android.
- **Server-side permissions** (`remote.read`; `remote.execute` reserved; `remote.scripts` deferred), default deny.
- **Linux-side approval engine** (single-use, bound to request + params hash, fail-closed expiry)
  with `remote approvals|approve|deny`. No operation in 0.1 requires it.
- **Audit log** of metadata only: `remote audit`; retention `audit_retention_days`.
- Android **Remote screen**: identity/permission header, live status with stale/error states,
  sortable paged process list, process details; Lint-clean, accessible.
- `nexus` command alias; Arch `PKGBUILD`; `uninstall.sh`; hardened systemd user unit.
- Shared protocol fixtures tested by Python and Kotlin; 100 new Python tests, 26 Android unit tests.
- Docs: design, protocol, threat/permission model, credential migration, scripts policy,
  security test report, regression report, packaging, tester guide.

### Changed
- Installer: Arch/Ubuntu detection, Python ≥ 3.11 check, binds to Tailscale IP, sets
  `device_name` from hostname, config 0600, never root, safe re-run for updates.
- Agent warns when listening on all interfaces.
- Apache-2.0 `LICENSE` and `NOTICE` added.

### Security
- **Breaking:** placeholder token `change-me` is rejected at startup.
- **Breaking:** dashboard `/api/locate` is POST-only.
- Dashboard escapes device names (stored XSS fix).
- Config files written 0600; tests isolated from the real home directory.

### Deferred
- User scripts (`remote.scripts`), any mutating operation, QR pairing, TLS, instrumented Android UI tests.

## 0.2.0 — PhoneBridge
- Notifications, calls, GPS + battery to Linux; SQLite history; map dashboard; CLI.
