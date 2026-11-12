# Nexus Remote — Day 1 Baseline Audit

> **Date:** 2026-11-08<br>
> **Scope:** Baseline audit of the existing PhoneBridge prototype before evolving it into Nexus Remote.<br>
> **Constraint:** No application source code was modified during this audit.

---

## 1. Project Directory Tree

```
Phone_Tracker/
├── .gitignore
├── Plan.txt
├── android/
│   ├── app/
│   │   ├── build.gradle.kts
│   │   ├── proguard-rules.pro
│   │   └── src/main/
│   │       ├── AndroidManifest.xml
│   │       ├── java/com/phonebridge/app/
│   │       │   ├── App.kt
│   │       │   ├── MainActivity.kt
│   │       │   ├── bridge/
│   │       │   │   ├── BootReceiver.kt
│   │       │   │   ├── BridgeRuntime.kt
│   │       │   │   ├── BridgeService.kt
│   │       │   │   ├── CallMonitor.kt
│   │       │   │   ├── ConnectionManager.kt
│   │       │   │   ├── LocationMonitor.kt
│   │       │   │   ├── MessageProtocol.kt
│   │       │   │   └── NotificationBridgeListener.kt
│   │       │   └── data/
│   │       │       └── Prefs.kt
│   │       └── res/
│   │           ├── drawable/
│   │           ├── layout/activity_main.xml
│   │           ├── mipmap-anydpi-v26/
│   │           ├── values/
│   │           └── xml/network_security_config.xml
│   ├── build.gradle.kts
│   ├── gradle.properties
│   ├── gradlew
│   ├── gradlew.bat
│   ├── gradle/wrapper/
│   ├── local.properties          (ignored)
│   └── settings.gradle.kts
└── phone-bridge/
    ├── __init__.py
    ├── authentication.py
    ├── client.py
    ├── config.example.toml
    ├── config.py
    ├── discovery.py
    ├── main.py
    ├── notifications.py
    ├── protocol.py
    ├── pyproject.toml
    ├── requirements.txt
    ├── scripts/install.sh
    ├── server.py
    ├── storage.py
    ├── systemd/phone-bridge.service
    ├── tests/
    │   ├── __init__.py
    │   ├── test_config_auth.py
    │   ├── test_protocol.py
    │   └── test_tracker_and_notifications.py
    └── web.py
```

> **Note:** No `README.md` or `AGENTS.md` exists in the repository. `Plan.txt` is the only high-level project description file.

---

## 2. Technology Stack

| Layer | Technology | Version / Notes |
|---|---|---|
| Android | Kotlin + Android SDK | `compileSdk = 34`, `minSdk = 26`, `targetSdk = 34` |
| Android build | Gradle + Android Gradle Plugin | Kotlin DSL (`build.gradle.kts`) |
| Android UI | XML layouts + Material 3 + ViewBinding | Material Switch, TextInputLayout |
| Android networking | OkHttp WebSocket client | `com.squareup.okhttp3:okhttp:4.12.0` |
| Android lifecycle | Kotlin coroutines + `lifecycle-runtime-ktx` | Foreground service / `repeatOnLifecycle` |
| Python runtime | CPython | `>=3.11` |
| Python networking | `websockets` | `>=13.0`, async WebSocket server & client |
| Python packaging | `setuptools` via `pyproject.toml` | Console script: `phone-bridge` |
| Optional desktop notifications | `dbus-python` | Optional; falls back to `notify-send` or console |
| Database | SQLite via `sqlite3` | WAL mode, custom `storage.py` |
| Dashboard | Vanilla HTML/JS + Leaflet + OpenStreetMap/CARTO tiles | Inline in `web.py` |
| Network overlay | Tailscale | Assumed; no Tailscale SDK integration; `discovery.py` queries `tailscale status --json` |
| OS target | Arch Linux / Ubuntu (per roadmap) | Systemd user unit provided |

---

## 3. Existing Architecture

### 3.1 Android Side

```
MainActivity
    ├── BridgeSettings (host, port, token, toggles, location interval)
    ├── Prefs (SharedPreferences persistence)
    └── BridgeService (foreground service)
            ├── ConnectionManager (OkHttp WebSocket, auth, reconnect)
            ├── CallMonitor (TelephonyCallback / PhoneStateListener)
            ├── LocationMonitor (GPS/Network/Fused + battery)
            └── BridgeRuntime (SharedFlow event bus + MutableStateFlow stats)
                    ↑
NotificationBridgeListener (system NotificationListenerService)
```

**Key Android components:**

- **`MainActivity.kt`** — Configuration UI, runtime permission requests, runtime observation.
- **`BridgeService.kt`** — Foreground service (`dataSync|location`) that owns the WebSocket connection.
- **`ConnectionManager.kt`** — OkHttp WebSocket client; authenticates with a shared secret; reconnects with exponential backoff (5 s → 60 s); buffers up to 200 pending events.
- **`MessageProtocol.kt`** — JSON wire protocol models: `Hello`, `Notification`, `Call`, `Location`, `Locate`, `Ping`, `Pong`, plus inbound frame parsing.
- **`BridgeRuntime.kt`** — Process-wide event bus (`OutboundEvent`) and state/stats flows.
- **`NotificationBridgeListener.kt`** — `NotificationListenerService`; filters to social/communication/email apps when `socialOnlyNotifications` is enabled.
- **`CallMonitor.kt`** — Emits `RINGING`/`OFFHOOK`/`IDLE` call events.
- **`LocationMonitor.kt`** — Subscribes to `LocationManager` providers; reads battery; responds to immediate-locate requests.
- **`Prefs.kt`** — `BridgeSettings` dataclass + `SharedPreferences` helper.
- **`BootReceiver.kt`** — Starts `BridgeService` on boot if configured.

### 3.2 Python / Laptop Side

```
main.py (CLI)
    └── BridgeServer (asyncio + websockets)
            ├── Authenticator (constant-time token + rate limit)
            ├── NotificationService (D-Bus / notify-send / console)
            ├── TailscaleDiscovery
            ├── LocationStore (SQLite)
            └── MapWebServer (asyncio HTTP + SSE)
```

**Key Python components:**

- **`main.py`** — CLI entry point. Commands: `serve`, `map`, `where`, `location`, `locate`, `send`, `call`, `simulate`, `ping`, `status`, `token`, `init-config`.
- **`server.py`** — `BridgeServer`: handshake, ping/pong heartbeat, dispatches `notification`/`call`/`location`/`locate`; broadcasts events to Map UI listeners.
- **`protocol.py`** — Protocol dataclasses and validation (`Hello`, `Notification`, `Call`, `Location`, `Locate`, `Ping`, `Pong`).
- **`config.py`** — Layered config (defaults → TOML/JSON → env → CLI). Generates random tokens; masks token in public output.
- **`storage.py`** — `LocationStore`: SQLite persistence of GPS points, device history, GeoJSON/GPX export, retention pruning.
- **`web.py`** — `MapWebServer`: HTTP/1.1 + SSE endpoints; single-page Leaflet dashboard.
- **`notifications.py`** — Desktop notification backends + social/communication allowlist.
- **`authentication.py`** — `Authenticator` with constant-time HMAC compare and per-source rate limiting (10 attempts / 60 s).
- **`discovery.py`** — Tailscale CLI status parser.
- **`client.py`** — Test/reference WebSocket client + `simulate()`.

### 3.3 Wire Protocol

All frames are JSON objects with a `type` field. Current message types:

| Type | Direction | Payload |
|---|---|---|
| `hello` | phone → laptop | `{device, token, version, platform}` |
| `hello_ack` | laptop → phone | `{server, version, timestamp}` |
| `notification` | phone → laptop | `{id, app, package, title, text, ongoing, low_priority, timestamp}` |
| `call` | phone → laptop | `{id, state, name, number, timestamp}` |
| `location` | phone → laptop | `{id, latitude, longitude, accuracy, altitude, speed, bearing, provider, battery, charging, timestamp}` |
| `locate` | laptop → phone | `{id, timestamp}` (request immediate GPS fix) |
| `ping` | laptop → phone | `{timestamp}` |
| `pong` | phone → laptop | `{timestamp}` |
| `ack` | laptop → phone | `{ref, ok, detail}` |
| `error` | laptop → phone | `{code, message}` |

---

## 4. Feature Inventory

| Feature | Implementation location | Current status | Evidence |
|---|---|---|---|
| Notifications (Android → Linux) | `NotificationBridgeListener.kt`, `server.py::_handle_notification`, `notifications.py` | ✅ Implemented | Listener parses `StatusBarNotification`, forwards to WebSocket, server renders via D-Bus/notify-send/console |
| Social/communication notification filter | `NotificationBridgeListener.kt`, `notifications.py::is_app_allowed` | ✅ Implemented | `only_allowed_apps` defaults to true; package + keyword + category allowlist |
| Call events | `CallMonitor.kt`, `server.py::_handle_call` | ✅ Implemented | Telephony state changes → `RINGING`/`OFFHOOK`/`IDLE` → desktop notification |
| GPS sharing | `LocationMonitor.kt`, `server.py::_handle_location`, `storage.py` | ✅ Implemented | GPS/Network/Fused providers → SQLite |
| Battery telemetry | `LocationMonitor.kt::readBattery` | ✅ Implemented | Broadcast receiver + `BatteryManager` |
| Locate request | `server.py::request_location` → `ConnectionManager` → `LocationMonitor::sendImmediateFix` | ✅ Implemented | `locate` frame triggers `getCurrentLocation` on Android |
| SQLite persistence | `storage.py::LocationStore` | ✅ Implemented | `locations` table with indexes, retention pruning |
| CLI | `main.py` | ✅ Implemented | 12 subcommands + config override flags |
| Dashboard / map | `web.py` + inline Leaflet HTML | ✅ Implemented | `/`, `/api/status`, `/api/locations`, `/api/events` (SSE), `/api/export.geojson`, `/api/export.gpx`, `/api/locate` |
| Tailscale connectivity | `discovery.py`, `BridgeSettings.webSocketUrl()` | ⚠️ Assumed / optional | Uses user-supplied Tailscale IP/hostname; `discovery.py` only logs presence; no Tailscale SDK or MagicDNS automation |
| Authentication | `authentication.py`, `Hello` handshake | ✅ Implemented | Shared secret, constant-time compare, rate limiting |
| Heartbeat / keepalive | `server.py::_heartbeat`, `ConnectionManager` | ✅ Implemented | Server pings every 25 s; OkHttp client pings every 20 s |
| Reconnection | `ConnectionManager.kt::scheduleReconnect` | ✅ Implemented | Exponential backoff 5 s–60 s |
| Boot auto-start | `BootReceiver.kt` | ✅ Implemented | Respects `startOnBoot` setting |

---

## 5. Build and Test Commands

### Python

```bash
cd phone-bridge
python3 -m unittest discover -s tests -t . -v
```

### Android

```bash
cd android
./gradlew :app:assembleDebug
```

### CLI smoke test

```bash
cd phone-bridge
python3 main.py serve --token "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
# In another terminal:
python3 main.py simulate --device pixel-9
python3 main.py where --history 5
```

---

## 6. Test Results

### Python Unit Tests

Command:

```bash
cd phone-bridge
python3 -m unittest discover -s tests -t . -v
```

Output:

```text
test_generate_token_is_random (tests.test_config_auth.AuthTests.test_generate_token_is_random) ... ok
test_rate_limiting (tests.test_config_auth.AuthTests.test_rate_limiting) ... ok
test_valid_and_invalid_token (tests.test_config_auth.AuthTests.test_valid_and_invalid_token) ... ok
test_env_and_overrides_precedence (tests.test_config_auth.ConfigTests.test_env_and_overrides_precedence) ... ok
test_invalid_backend_rejected (tests.test_config_auth.ConfigTests.test_invalid_backend_rejected) ... ok
test_invalid_port_rejected (tests.test_config_auth.ConfigTests.test_invalid_port_rejected) ... ok
test_token_is_generated_when_missing (tests.test_config_auth.ConfigTests.test_token_is_generated_when_missing) ... ok
test_toml_file_round_trip (tests.test_config_auth.ConfigTests.test_toml_file_round_trip) ... ok
test_web_url_and_device_allow_list (tests.test_config_auth.ConfigTests.test_web_url_and_device_allow_list) ... ok
test_call_accepts_legacy_event_field (tests.test_protocol.ProtocolTests.test_call_accepts_legacy_event_field) ... ok
test_call_state_mapping (tests.test_protocol.ProtocolTests.test_call_state_mapping) ... ok
test_fields_are_truncated (tests.test_protocol.ProtocolTests.test_fields_are_truncated) ... ok
test_invalid_json_rejected (tests.test_protocol.ProtocolTests.test_invalid_json_rejected) ... ok
test_locate_round_trip (tests.test_protocol.ProtocolTests.test_locate_round_trip) ... ok
test_location_rejects_out_of_range_coordinates (tests.test_protocol.ProtocolTests.test_location_rejects_out_of_range_coordinates) ... ok
test_location_round_trip_and_validation (tests.test_protocol.ProtocolTests.test_location_round_trip_and_validation) ... ok
test_missing_type_rejected (tests.test_protocol.ProtocolTests.test_missing_type_rejected) ... ok
test_notification_round_trip (tests.test_protocol.ProtocolTests.test_notification_round_trip) ... ok
test_unknown_type_rejected (tests.test_protocol.ProtocolTests.test_unknown_type_rejected) ... ok
test_record_query_and_export (tests.test_tracker_and_notifications.LocationStoreTests.test_record_query_and_export) ... ok
test_only_allowed_apps_can_be_disabled (tests.test_tracker_and_notifications.NotificationAllowlistTests.test_only_allowed_apps_can_be_disabled) ... ok
test_social_and_communication_apps_allowed (tests.test_tracker_and_notifications.NotificationAllowlistTests.test_social_and_communication_apps_allowed) ... ok
test_unwanted_apps_rejected_by_default (tests.test_tracker_and_notifications.NotificationAllowlistTests.test_unwanted_apps_rejected_by_default) ... ok
test_end_to_end_location_and_filtered_notifications (tests.test_tracker_and_notifications.ServerAndWebIntegrationTests.test_end_to_end_location_and_filtered_notifications) ... ok

----------------------------------------------------------------------
Ran 24 tests in 0.208s

OK
```

### Android Build

Command:

```bash
cd android
./gradlew :app:assembleDebug --console=plain
```

Output (truncated):

```text
BUILD SUCCESSFUL in 2s
36 actionable tasks: 36 up-to-date
```

The build completed with only deprecation warnings:

- `PhoneStateListener` is deprecated on older API paths (handled with `TelephonyCallback` on API 31+).
- `Notification.priority` and `PRIORITY_LOW` are deprecated (still functional).

### Lint / Static Checks

No dedicated lint or static-analysis configuration was found (no `ruff`, `mypy`, `flake8`, `ktlint`, or `detekt` configs). Tests were run directly.

---

## 7. Security Observations

| Topic | Observation | Risk | Recommendation |
|---|---|---|---|
| Shared secret | Token is a URL-safe random string generated via `secrets.token_urlsafe(32)`; compared with `hmac.compare_digest`; masked in public config output. | Low | Rotate regularly; consider per-device tokens or public-key auth for multi-device support. |
| Rate limiting | Per-source IP rate limiting on failed auth attempts (10 / 60 s). | Low | Good baseline; ensure IPv6 source addresses are handled consistently. |
| Network binding | Default WebSocket binds to `0.0.0.0:8765`; map UI binds to `127.0.0.1:8766`. | Medium | `0.0.0.0` exposes the WebSocket on all interfaces. Tailscale is expected to provide perimeter security, but an allow-list or loopback-only mode should be offered. |
| Cleartext traffic | `network_security_config.xml` permits cleartext traffic (`cleartextTrafficPermitted="true"`). | Medium | Acceptable inside a Tailnet; should be removed or made conditional if WebSocket moves to `wss://`. |
| Token storage | Android stores token in `SharedPreferences`; Python stores token in `~/.config/phone-bridge/config.toml`. | Low-Medium | SharedPreferences is not encrypted by default; consider `EncryptedSharedPreferences`. Protect config file permissions (`0600`). |
| Hardcoded defaults | `token = "change-me"` in `config.example.toml` is a placeholder only. | Low | Installer replaces it; ensure users never commit real `config.toml`. |
| Exposed credentials | Scan found no committed API keys, passwords, private keys, or real tokens. | Low | None found. |
| Database hygiene | No `.db`, `.db-wal`, `.db-shm`, or location-history files found in the repo. | Low | Add `*.db*` to `.gitignore` if not already present. |
| Input validation | Protocol validates JSON shape, string lengths, coordinate ranges, numeric fields. | Low | Good; continue fuzzing new message types. |
| Desktop notification injection | Any authenticated client can trigger desktop notifications. | Low | Current threat model assumes authenticated phone only; future multi-role support may need authorization levels. |

### Security Scan Commands Used

```bash
find . -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.token' -o -name 'config.toml' -o -name '*.keystore' -o -name '*.jks' \) -not -path './.git/*'
rg -n -i 'token|secret|password|api_key|apikey|access_key' --type py --type kt --type xml --type toml
rg -n -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' --type py --type kt --type xml --type toml
```

---

## 8. Git Status

```bash
git status
```

Output:

```text
On branch main
nothing to commit, working tree clean
```

Branch information:

```bash
git branch -a && git log --oneline -5
```

Output:

```text
* main
7db204d Add PhoneBridge Android app, Python server, SQLite GPS tracker, Map UI, and social notification filter
```

Ignored files (as reported by `git status --ignored=matching`):

- `android/.gradle/`
- `android/.kotlin/`
- `android/app/build/`
- `android/dist/`
- `android/local.properties`
- `phone-bridge/__pycache__/`
- `phone-bridge/tests/__pycache__/`

> **Note:** `android/dist/PhoneBridge-debug.apk` exists locally but is git-ignored. It should not be committed.

---

## 9. Technical Uncertainties

1. **Rebrand strategy** — The codebase still uses the `com.phonebridge.app` package, `PhoneBridge` strings, and `phone-bridge` CLI name. Nexus Remote needs a decision on whether to fork/rename or migrate incrementally.
2. **Protocol extensibility** — New features (remote terminal, file transfer, desktop control) will require new message types. The current enum-based `parse_event` switch is clean but may need a plugin-style router for many message types.
3. **Multi-device support** — Currently one phone ↔ one laptop is assumed. Supporting multiple Android devices and multiple laptops will require namespacing sessions, per-device tokens, and routing.
4. **Tailscale integration depth** — Tailscale is assumed but not enforced. The project does not verify that peers are on the same tailnet before accepting connections.
5. **Map UI external dependencies** — The dashboard loads Leaflet and map tiles from CDNs (`unpkg.com`, `cartocdn.com`, `openstreetmap.org`). Offline/air-gapped use will fail without vendoring or bundling these assets.
6. **Android foreground service restrictions** — `FOREGROUND_SERVICE_LOCATION` and `ACCESS_BACKGROUND_LOCATION` may require additional Play Store / OEM-specific approval. A background-location audit is recommended.
7. **No existing CI/CD** — No GitHub Actions, pre-commit hooks, or lint checks are present.
8. **Missing project docs** — No `README.md`, `AGENTS.md`, or contribution guidelines exist.
9. **Test coverage** — Only 24 tests exist, focused on protocol, config, storage, and one integration test. No Android instrumentation tests.
10. **Desktop control / terminal / file transfer** — These long-term features have no scaffolding yet and will require new modules on both Android and Python sides.

---

## 10. Recommended Next Steps

### Immediate (Day 2)

1. Create a project README and AGENTS.md with build/run instructions and conventions.
2. Add `*.db*`, `android/.kotlin/`, and `android/.idea/` to `.gitignore` defensively.
3. Freeze current PhoneBridge functionality as the v0.1 baseline and tag it.
4. Set up a minimal CI pipeline (GitHub Actions) that runs Python tests and Android debug builds on PRs.
5. Introduce static analysis/linting: `ruff`/`mypy` for Python; `ktlint` or Android Lint for Kotlin.

### Short-term (Week 1–2)

6. Design the Nexus Remote wire-protocol extension:
   - Add a namespacing or capability-negotiation field to `hello`/`hello_ack`.
   - Define message types for remote terminal (`shell_start`, `shell_data`, `shell_stop`), file transfer (`file_offer`, `file_chunk`, `file_done`), and system monitoring (`sys_info`, `sys_metric`).
7. Refactor `server.py` into a pluggable message router so new handlers can be registered without editing the dispatch switch.
8. Add per-session routing and multi-device support in `BridgeServer`.
9. Bundle/vend Leaflet and map tiles (or document CDN dependency) for offline-capable map UI.
10. Harden default binding: offer `--host 127.0.0.1` option and document the `0.0.0.0` default.

### Medium-term (Month 1)

11. Implement remote terminal module:
    - Python: `asyncio` subprocess PTY wrapper.
    - Android: secure in-app terminal view + command whitelist.
12. Implement file transfer module:
    - Chunked binary messages over WebSocket or a secondary encrypted channel.
    - Quota, path validation, and virus-scan hooks.
13. Implement desktop control module:
    - Python: input injection via `evdev` / `uinput` (Linux-specific).
    - Android: touchpad/keyboard UI.
14. Add end-to-end encryption above Tailscale (e.g., libsodium `crypto_box`) for defense-in-depth.
15. Add Android instrumentation tests and expand Python test coverage.

### Blockers for Day 2

- None. The codebase builds, tests pass, and no critical security leaks were found.
- The only prerequisite is a decision on the rebrand scope (rename package/CLI or keep PhoneBridge compatibility).

---

## A. Files Inspected

- `Plan.txt`
- `.gitignore`
- `android/app/build.gradle.kts`
- `android/build.gradle.kts`
- `android/settings.gradle.kts`
- `android/app/src/main/AndroidManifest.xml`
- `android/app/src/main/java/com/phonebridge/app/MainActivity.kt`
- `android/app/src/main/java/com/phonebridge/app/App.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/BootReceiver.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/BridgeRuntime.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/BridgeService.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/CallMonitor.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/ConnectionManager.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/LocationMonitor.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/MessageProtocol.kt`
- `android/app/src/main/java/com/phonebridge/app/bridge/NotificationBridgeListener.kt`
- `android/app/src/main/java/com/phonebridge/app/data/Prefs.kt`
- `android/app/src/main/res/layout/activity_main.xml`
- `android/app/src/main/res/values/strings.xml`
- `android/app/src/main/res/xml/network_security_config.xml`
- `phone-bridge/pyproject.toml`
- `phone-bridge/requirements.txt`
- `phone-bridge/config.example.toml`
- `phone-bridge/authentication.py`
- `phone-bridge/client.py`
- `phone-bridge/config.py`
- `phone-bridge/discovery.py`
- `phone-bridge/main.py`
- `phone-bridge/notifications.py`
- `phone-bridge/protocol.py`
- `phone-bridge/server.py`
- `phone-bridge/storage.py`
- `phone-bridge/web.py`
- `phone-bridge/scripts/install.sh`
- `phone-bridge/systemd/phone-bridge.service`
- `phone-bridge/tests/test_protocol.py`
- `phone-bridge/tests/test_config_auth.py`
- `phone-bridge/tests/test_tracker_and_notifications.py`

## B. Files Created or Modified

- **Created:** `docs/NEXUS_BASELINE.md`
- **Modified:** None (application source code was not changed).

## C. Commands Executed

```bash
# Repository inspection
git rev-parse --show-toplevel
git status
git branch -a
git log --oneline -10
find . -maxdepth 3 -type f \( -name 'AGENTS.md' -o -name 'README*' -o -name '*.md' -o -name '*.txt' -o -name '*.toml' -o -name 'pyproject.toml' -o -name 'build.gradle*' -o -name 'settings.gradle*' \) | sort

# Tests and build
cd phone-bridge && python3 -m unittest discover -s tests -t . -v
cd android && ./gradlew :app:assembleDebug --console=plain

# Security / hygiene scans
find . -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.token' -o -name 'config.toml' -o -name '*.keystore' -o -name '*.jks' \) -not -path './.git/*'
rg -n -i 'token|secret|password|api_key|apikey|access_key' --type py --type kt --type xml --type toml
rg -n -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' --type py --type kt --type xml --type toml
```

---

*End of Day 1 baseline audit.*
