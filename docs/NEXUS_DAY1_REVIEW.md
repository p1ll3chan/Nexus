# Nexus Remote — Day 1 Independent Audit Review

**Review date:** 2026-11-08<br>
**Repository:** `/home/p1ll3chan/Documents/Projects/Personal_Projects/Phone_Tracker`<br>
**Branch / commit inspected:** `main` / `7db204d`<br>
**Review scope:** Source-level verification of `docs/NEXUS_BASELINE.md`, repository hygiene, application security, and test evidence. No application code was changed and no dependencies were installed.

## 1. Executive Assessment

The baseline captures the main Android-to-Python flow and the successful Python test / Android build results are reproducible in this environment. It is a useful orientation document, but it is **not yet a sufficiently complete security or release baseline**. Several material risks were not identified or were understated: WebSocket traffic is always cleartext at the application layer and the server listens on all interfaces by default; the optional map server has unauthenticated data/control endpoints and a permissive SSE CORS header; untrusted device names reach HTML sinks; token/database file modes are not hardened; sensitive event contents are logged; and GPS retention relies on the phone-supplied event timestamp.

The review found no tracked private-key/cloud-token pattern, no database/config/keystore files in the scanned repository tree, and no shell-based command construction in the inspected subprocess calls. These are limited source/repository checks, not proof that Git history, every secret format, or a deployed host is clean.

**Verdict:** The repository is sufficiently understood to begin Day 2 work, but it is **not ready to expose as a general network service or to add remote shell/file/control capabilities**. Day 2 should begin with a bounded security-hardening task and regression tests around the transport/exposure model, dashboard, local secret/data permissions, and privacy retention/logging. Keep the current PhoneBridge behavior covered while doing so.

## 2. Baseline Report Verification

### Accurate or substantially accurate

- The high-level components are real: Kotlin Android app, Python `websockets` server/client, JSON protocol, SQLite `LocationStore`, CLI, and inline Leaflet dashboard.
- The important data path is broadly correct: Android notification/call/location monitors publish events; `ConnectionManager` sends them over WebSocket; `BridgeServer` authenticates the initial `hello`, dispatches events, writes GPS history, and optionally serves the map/API.
- The baseline names the core source files and most current feature areas. Its distinction between Tailscale as an expected overlay and the application not enforcing Tailnet membership is correct.
- The listed Python command independently passed all **24 tests** in this review.
- The Android `:app:assembleDebug` task independently reported **BUILD SUCCESSFUL**. All 36 actions were `UP-TO-DATE`; this proves Gradle accepted the task graph and existing outputs, but does not establish a clean rebuild from deleted build outputs.
- The current `.gitignore` already covers Python caches/virtual environments, `config.toml`, `*.token`, `*.db`, `*.db-wal`, `*.db-shm`, Android Gradle/Kotlin/build caches, `local.properties`, `.idea`, and `android/dist` (see `.gitignore:1-31`).

### Inaccurate, stale, or requiring qualification

1. **Current Git status is not clean.** Before creating this review, `git status --short` reported `?? docs/`. `docs/NEXUS_BASELINE.md` itself is untracked; it was not part of commit `7db204d`. The baseline's recorded clean status may describe the earlier audit point, but is not the repository's current state. No files were staged.
2. **The suggested `.gitignore` additions are redundant.** Baseline lines 375-376 recommend adding `*.db*`, `android/.kotlin/`, and `android/.idea/`; the current file already has explicit database/WAL/SHM patterns plus `.kotlin/` and `.idea/` entries. An extra broad `*.db*` pattern is optional, not an uncovered gap.
3. **The “one phone ↔ one laptop” uncertainty is too strong.** `BridgeServer.sessions` is a map keyed by each WebSocket connection, location history is device-keyed, and the dashboard exposes a device selector. Basic multi-client/multi-device handling already exists (`server.py:96-97, 108-109, 127-135`; `storage.py:210-277`; `web.py:751-774`). There is still one shared token, no per-device credentials/authorization, and no documented lifecycle or collision policy for duplicate device names.
4. **“Clean/secure token handling” is incomplete.** Token generation and constant-time comparison are good (`config.py:400-403`; `authentication.py:37-49`), but config creation does not enforce restrictive file/directory permissions, the installer prints the generated token, and the default network does not use TLS (see findings below).
5. **The input-validation statement is overbroad.** Coordinates and string lengths are checked, but timestamp parsing for several message types directly calls `float(...)` and is not normalized to `ProtocolError` (`protocol.py:247-260, 285-293, 381-403`).
6. **The report's security scan is not a complete secret audit.** Its listed patterns target source text and filename extensions; they do not establish that Git history, all binary files, ignored files, or arbitrary credential formats are clean. The review's own limited tracked-file pattern scan found no matches for common private-key/AWS/GitHub/OpenAI token forms; it was not a history scan.
7. **The report describes a 30-day retention policy without explaining its clock source.** Cleanup compares the phone-provided event `timestamp`, not local `received_at` (`storage.py:189-191`). Future or badly skewed timestamps can therefore evade the stated retention period.
8. **Tailscale and map behavior need sharper distinction.** Tailscale discovery only runs a CLI status query for optional presence/watchdog information; it does not bind the WebSocket to a Tailnet interface, validate the peer's Tailnet identity, or protect the map endpoints (`discovery.py:3-5, 75-99`; `server.py:386-397`).

### Missing files / implementation details

- The baseline directory tree omits tracked `android/app/src/main/res/drawable/ic_bridge.xml`, `ic_launcher_foreground.xml`, the launcher XMLs, `colors.xml`, and `themes.xml`; it is an illustrative rather than exhaustive tree.
- It does not list the Gradle wrapper properties / exact wrapper version, or the Linux installer and user systemd unit details as first-class architecture/security items. Those are important for reproducibility and deployment.
- Android `MainActivity.sendTestNotification()` emits a test notification and call, and attempts an immediate location; if unavailable it publishes a hardcoded sample coordinate (San Francisco) (`MainActivity.kt:155-185`). The baseline mentions CLI simulation, but not this in-app test behavior. It should be clearly presented as test-only so users do not mistake the sample fix for a real GPS fix.
- Android declares background location, phone-state/call-log, notification, boot, and foreground-service permissions; its components have different export/binding settings. The baseline only briefly discusses background-location restrictions.
- The dashboard uses external Leaflet/CARTO/OpenStreetMap assets/tiles; the baseline notes availability but not that map tile requests disclose approximate viewed areas to those external services.

## 3. Architecture Review

### Verified event / control path

```text
Android MainActivity / user settings
  ├─ Prefs: SharedPreferences configuration
  └─ BridgeService (foreground)
       ├─ ConnectionManager (OkHttp WebSocket, hello token, reconnect queue)
       ├─ NotificationBridgeListener (user-enabled NotificationListenerService)
       ├─ CallMonitor (TelephonyCallback / legacy PhoneStateListener)
       └─ LocationMonitor (LocationManager updates / immediate locate / battery)
                 ↓ JSON frames
Python BridgeServer
  ├─ hello/device allow-list/shared-token check
  ├─ NotificationService (D-Bus, notify-send, console, or none)
  ├─ LocationStore (SQLite GPS history and exports)
  ├─ TailscaleDiscovery (optional CLI status/presence query)
  └─ MapWebServer (HTTP API, exports, SSE and Leaflet page)
```

The main component boundaries and event integration points in the baseline are good. Two qualifications matter for design: (a) `BridgeServer` already accepts multiple authenticated sessions, although authorization is global-token-based; (b) the map server is a separate plain HTTP listener, not an authenticated part of the WebSocket protocol. The current architecture has no remote command execution, file-transfer, or desktop-input subsystem.

## 4. Security Findings

Severity describes the source-level risk in the default/configurable deployment. Conditional risks are explicitly qualified. No exploit was run against a deployed host.

### S1 — Cleartext WebSocket and all-interface default

- **Severity:** Medium (High if the phone connects over an untrusted network rather than an encrypted Tailnet).
- **Evidence:** `config.py:154-155` defaults to `host="0.0.0.0"`, port `8765`; `config.example.toml:4-5` repeats this; `BridgeServer.start()` binds those values at `server.py:406-413`. `BridgeSettings.webSocketUrl()` strips `wss://` and always returns `ws://` at `Prefs.kt:21-29`. `network_security_config.xml:3` explicitly allows cleartext. The token is sent in the first JSON `hello` (`ConnectionManager.kt:136-139`; `protocol.py:218-233`).
- **Impact:** Where traffic is reachable outside Tailscale, a network observer can read notifications, call details, GPS, and the bearer token; a token observer can impersonate a client. `0.0.0.0` also exposes the listener on every available interface unless the host firewall constrains it. The code assumes Tailnet encryption but does not enforce Tailnet-only routing or validate a peer identity.
- **Recommended remediation:** Decide and enforce the supported deployment boundary. Bind the server to a specifically selected Tailnet address/interface (or require an explicit opt-in to all-interface binding) and document/firewall the port. If non-Tailnet use is supported, implement and test real `wss://` support end-to-end; do not silently downgrade an entered `wss://` URL to `ws://`. Keep the shared-token check even when using Tailscale.

### S2 — Unauthenticated map data/control API; permissive event-stream CORS

- **Severity:** Medium; default loopback binding substantially limits direct remote exposure.
- **Evidence:** Map defaults to `127.0.0.1:8766` (`config.py:205-206`), but `map_host` is configurable and `MapWebServer.start()` binds it directly (`web.py:631-637`). Routes for `/api/status`, `/api/locations`, GPX/GeoJSON export, `/api/locate`, and `/api/events` are served without authentication (`web.py:683-719`). `/api/events` sets `Access-Control-Allow-Origin: *` (`web.py:776-784`) while broadcasting location/session events. `/api/locate` accepts both GET and POST (`web.py:710-715`), including a state-changing GET.
- **Impact:** If the map is bound to a LAN/Tailnet/all-interface address, any reachable client can read precise location history and trigger immediate location requests. The wildcard CORS response permits cross-origin JavaScript/EventSource to read the event stream when the browser can reach the listener; localhost/private-network browser protections vary by browser and do not substitute for authorization.
- **Recommended remediation:** Keep the map loopback-only by default. If remote access is required, add explicit authentication/authorization for all APIs and exports, restrict CORS to a deliberate origin set (or omit it), and make location requests POST-only with CSRF protection. Add a test proving unauthenticated requests cannot read or trigger sensitive operations in the intended deployment mode.

### S3 — Untrusted device name is interpolated into dashboard HTML

- **Severity:** Medium.
- **Evidence:** `Hello.from_dict()` accepts the client-supplied `device` string after only trimming/truncating (`protocol.py:81-93, 218-224`); the server uses it as the session identity (`server.py:220-224`) and stores it with GPS records (`storage.py:155-186`). The dashboard inserts `pt.device` into Leaflet popup HTML via template strings (`web.py:443-466`) and history-list `innerHTML` (`web.py:496-504`).
- **Impact:** A client that can authenticate (or a compromised phone sharing the common token) can use a crafted device name to execute script in a browser viewing the map. Depending on browser protections and the map binding, that script can read same-origin location data or invoke the locate API.
- **Recommended remediation:** Render untrusted names using DOM text nodes / `textContent`, and construct popup elements without HTML interpolation. Apply output encoding at every HTML sink; add a regression test using a markup/script-shaped device name. A restrictive CSP is useful defense-in-depth, not a replacement for safe rendering.

### S4 — No enforced restrictive permissions for Linux token/config/database files; installer prints token

- **Severity:** Medium (local multi-user exposure; actual mode depends on process umask and parent directory permissions).
- **Evidence:** `write_default_config()` creates parent directories and writes token-bearing TOML without chmod or restrictive creation mode (`config.py:406-443`). `scripts/install.sh:14-19` prints the generated shared secret and writes the config using `sed`; it does not set a restrictive umask. `LocationStore` creates parent directories and opens SQLite without setting directory/database permissions (`storage.py:93-107`).
- **Impact:** Under a common `umask 022`, a newly created config/database may be group/world-readable if parent directories are traversable; another local account could recover the shared token and/or precise GPS history. Installer output may also be retained in terminal logs. This is not evidence that the current host's actual files have insecure modes; no local runtime config/database was found in the scanned repository tree.
- **Recommended remediation:** Create config/data directories with private modes and enforce config/database file mode `0600` (directories typically `0700`) independent of ambient umask. Avoid printing the installer-generated secret by default, or make the one-time display explicit and avoid placing the token in command arguments/process listings. Verify modes in tests and installer checks.

### S5 — Sensitive notification/call/location data written to INFO logs; retention clock is client-controlled

- **Severity:** Medium for privacy; retention weakness depends on device clock/input.
- **Evidence:** `BridgeServer._handle_notification()` logs full title and text at INFO (`server.py:262-267`); `_handle_call()` logs caller name/number (`server.py:303-306`); `_handle_location()` logs exact latitude/longitude (`server.py:341-349`). The configured 30-day cleanup uses the event's `timestamp` rather than `received_at` (`storage.py:155-161, 189-191`; example setting `config.example.toml:60-62`).
- **Impact:** Systemd/journal or redirected logs can retain private message/call/location data beyond SQLite's configured retention. Future or substantially skewed phone timestamps can keep history from being pruned as expected. SQLite deletion is logical row deletion, not guaranteed secure erasure from the database/WAL.
- **Recommended remediation:** Redact event bodies and exact coordinates from routine logs (offer opt-in diagnostic logging with clear warning). Base retention on local `received_at` or validate/normalize event time, and document database/WAL backup and deletion behavior. Add tests for future/stale timestamps and retention.

### S6 — Some malformed timestamps escape protocol validation and close the session

- **Severity:** Low; requires an authenticated peer to send the malformed frame.
- **Evidence:** `Notification.from_dict`, `Call.from_dict`, `Locate.from_dict`, `Ping.from_dict`, and `Pong.from_dict` use direct `float(data.get("timestamp") or _now())` conversions (`protocol.py:247-260, 285-293, 381-403`). `BridgeServer._read_loop()` only treats `ProtocolError` as a recoverable protocol error (`server.py:226-235`); conversion `ValueError` propagates to the handler's broad exception handler, ending that session (`server.py:162-165`).
- **Impact:** A malformed or nonnumeric timestamp from an authenticated client disconnects that client and produces an unhandled-error log. The WebSocket message-size limit and handshake authentication limit exposure; this does not establish unauthenticated remote code execution.
- **Recommended remediation:** Validate every timestamp as finite, numeric, and within a sensible range; normalize all parser errors to `ProtocolError`. Add malformed/NaN/infinite timestamp tests and ensure a bad frame is handled consistently.

### S7 — Android declares sensitive/background permissions; effective behavior not device-validated

- **Severity:** Low / Informational; no confirmed unauthorized access found.
- **Evidence:** `AndroidManifest.xml:7-16` declares foreground-service, notification, boot, phone state, call log, coarse/fine location, and background location permissions. `MainActivity.requestLocationPermissions()` requests only coarse/fine (`MainActivity.kt:136-142`); no request for `ACCESS_BACKGROUND_LOCATION` is present. `READ_CALL_LOG` is requested when the call toggle is enabled (`MainActivity.kt:128-134`). `BridgeService` is non-exported (`AndroidManifest.xml:39-42`); the exported notification listener is protected by `BIND_NOTIFICATION_LISTENER_SERVICE` (`44-52`); `MainActivity` is exported as the launcher (`29-37`). `BootReceiver` is exported and has boot/package-replaced filters (`54-62`), but only starts if the user enabled start-on-boot and settings are complete (`BootReceiver.kt:9-19`). Backup is disabled (`AndroidManifest.xml:18-27`).
- **Impact:** Background-location startup/continuous behavior can vary by Android release and while-in-use/background permission rules; it was not tested on a device. The manifest's exported receiver is a residual local trigger surface, but the inspected receiver has a narrow action check and opt-in guard; no high-impact exploit was demonstrated. Notification access is deliberately user-granted and can expose notification contents by design.
- **Recommended remediation:** Test install, permission granting, background/boot behavior, and revocation on supported Android API levels. Remove permissions that are unnecessary for supported call/location behavior; otherwise explain and request them with appropriate user flow. Keep the internal service non-exported and listener binding permission.

### Command execution review — no command injection observed in inspected calls

`NotifySendNotifier.send()` builds a list of arguments and calls `asyncio.create_subprocess_exec()` without a shell (`notifications.py:152-165`). `TailscaleDiscovery.status()` likewise invokes the configured executable and fixed `status --json` arguments using `create_subprocess_exec()` (`discovery.py:75-94`). This avoids shell interpolation in these paths. The configured executable path is intentionally user-controlled configuration and should remain trusted. No `os.system`, `shell=True`, or `eval` path was found by source search.

## 5. Repository Hygiene Findings

| Check | Independent result |
|---|---|
| Branch / remote / commit | `main`, `origin` points to `https://github.com/p1ll3chan/Nexus.git`, current commit `7db204d`. No branch/remote/history changes were made. |
| Working tree | `git status --short` showed `?? docs/` before this review file was created. The baseline report was already untracked. No source changes were made. |
| `.gitignore` | Good coverage for listed Python, Android, config, DB/WAL/SHM, local.properties, and APK output paths. Baseline recommendations to add `.kotlin`, `.idea`, and database patterns duplicate current rules. Consider `*.env`, private-key suffixes, and generic release artifacts only if project workflows introduce them; no such artifact is currently tracked. |
| Generated outputs | `android/app/build/`, `android/.gradle/`, `android/.kotlin/`, Python `__pycache__/`, and `android/dist/PhoneBridge-debug.apk` exist locally or were observed by repository enumeration and are ignored. `git check-ignore -v` confirmed the APK/build/local.properties/config/cache examples are covered. |
| Sensitive runtime files | A repository-tree filename scan (excluding generated build/cache directories) found no `config.toml`, SQLite DB/WAL/SHM, token file, or keystore file. This says nothing about files outside this checkout. |
| Secret scan | A limited scan of tracked text for common PEM private-key, AWS access-key, GitHub token, and `sk-` token patterns found no matches. `config.example.toml` has the documented placeholder `change-me`; tests contain fake credentials/IPs. This was not a Git-history or binary forensic scan. |
| Hosts / ports | Intentional defaults are `0.0.0.0:8765` for WebSocket and `127.0.0.1:8766` for map; tests use loopback and a test port. No personal public IP was identified in inspected source/config. The report's “hardcoded IP” pattern also matches dummy test fixtures and should distinguish those from deployment addresses. |
| Python dependencies | `pyproject.toml` and `requirements.txt` both specify `websockets>=13.0`; there is no lockfile or upper bound. `setuptools>=68` is a build requirement and D-Bus is optional. Exact environment resolution is therefore not reproducible from the repository alone. |
| Android dependencies | Gradle wrapper pins Gradle 8.9; Android Gradle Plugin 8.7.3, Kotlin 2.0.21, and direct Android libraries have fixed versions. No Gradle dependency lock/verification metadata was found; transitive resolution is not fully locked. |

The installer (`phone-bridge/scripts/install.sh`) creates a virtual environment, installs from the checkout, creates a user service/config, and enables the service. The user systemd unit runs as the user (not root), but has no additional systemd filesystem/network sandboxing. No dependency was installed during this review.

## 6. Test Evidence Assessment

### Independently reproduced

1. `cd phone-bridge && python3 -m unittest discover -s tests -t . -v` — **24 tests passed**, `Ran 24 tests in 0.196s`, `OK`.
2. `cd android && ./gradlew :app:assembleDebug --console=plain` — **BUILD SUCCESSFUL**, `36 actionable tasks: 36 up-to-date`.

No package installation was performed. The Android run reused existing Gradle outputs; I did not delete build outputs or claim a clean rebuild. No Android device/emulator tests, Android Lint run, Python CLI smoke test, Tailscale-network test, or external penetration test was performed.

### Coverage quality

- Python coverage is useful for configuration/auth primitives, protocol round trips/basic validation, notification allowlisting, SQLite query/export, and one loopback integration path that exercises handshake, notifications, location, map status/locations, and HTML retrieval.
- It is **not sufficient to protect the full user-visible behavior or the security boundaries**. Missing examples include malformed timestamps and fuzzing; invalid/expired/large handshakes over actual sockets; rate-limit address behavior; web auth/CORS/CSRF/XSS tests; map export/locate/SSE behavior; file permissions/retention; subprocess failure/timeouts; phone retry/buffering; and Android permission, lifecycle, notification, call, GPS, and boot behavior.
- Android `assembleDebug` is a compile/package task, not a test suite and not Android Lint. There are no Android instrumentation/unit tests in the tracked tree.
- The 24 passing Python tests are reproducible and credible. The Android success is also reproducible as a Gradle success, but only with existing tasks up-to-date in this checkout; report it as such rather than as a clean compilation proof.

## 7. Missing or Incorrect Findings in Kimi's Baseline

1. **Transport exposure:** It mentions cleartext but understates that the Android URL builder always returns `ws://`, even when an entered host starts with `wss://`; the WebSocket server defaults to all interfaces.
2. **Map API privacy:** It does not call out that the dashboard API/export/locate/SSE endpoints have no authentication, and that SSE explicitly allows every origin. Loopback default is a meaningful mitigation but is not an access-control implementation.
3. **Dashboard injection:** It misses HTML injection from an authenticated client's device name through `innerHTML`/Leaflet popup templates.
4. **Local secret and DB permissions:** It recommends config permission hardening but does not establish current modes or note that the installer/config writer/database creation apply no explicit restrictive mode. The installer also prints the generated token.
5. **Sensitive logs and retention:** It misses exact message text, caller identity/number, and precise coordinates in INFO logs, and misses that DB pruning uses phone event time rather than receipt time.
6. **Android permissions/exports:** The baseline only says background-location restrictions deserve follow-up; it does not inventory the declared permissions, runtime request behavior, notification listener binding protection, internal service export setting, or exported boot receiver.
7. **Validation nuance:** Protocol validation is not uniformly applied to timestamps. The baseline's broad “numeric fields” statement should be narrowed and a malformed timestamp case covered.
8. **Existing hygiene:** It recommends `.gitignore` rules already present. The `docs/` directory is currently untracked, so the baseline's historical clean-tree snapshot is not current.
9. **Multi-device nuance:** Basic concurrent-session/device-keyed support exists already; per-device auth and lifecycle semantics remain incomplete.
10. **Audit confidence:** A source scan and ignored-file listing cannot establish absence of credentials in repository history, untracked binary content, external config directories, system logs, or deployed systems. The document should label its scan scope rather than present “no credentials” as an absolute.

## 8. Prioritized Remediation List

Priority is based on user-data impact and prerequisites, not on the size of each feature. Risks below assume PhoneBridge behavior is to be preserved.

| Priority | Workstream | Security impact | Engineering priority / dependencies | Break risk |
|---:|---|---|---|---|
| 1 | **Kimi #5 — security hardening** | Highest: protects bearer token, GPS, notification/call contents, dashboard access, and local data at rest. | Start with a written supported-network/threat model, then fix/test map output access and HTML sinks, make bind/transport behavior explicit, harden file modes/logging/retention. Security tests should be added before invasive changes. | Medium: transport/bind changes can break phone connectivity; split into compatible, test-backed changes and document migration. |
| 2 | **Kimi #2 — CI** | Indirect but high ongoing value: catches regressions before release. | Add after agreeing supported Python/JDK/Android SDK setup; run existing Python suite and Android assemble on PR. Does not require rebrand or protocol work. | Low, provided workflow does not install or publish artifacts unexpectedly. |
| 3 | **Kimi #1 — project hygiene/docs** | Low direct security; improves safe operation and onboarding. | Add accurate README/AGENTS and deployment/security guidance. Most proposed `.gitignore` rules already exist. Tag only after deciding what exact source+documentation state constitutes v0.1; the current baseline report is untracked. | Low for docs; low-to-medium for tag/version changes because users may rely on existing names and tags are release references. |
| 4 | **Kimi #6 — quality tooling** | Low-to-medium preventative impact; useful for protocol/validation defects. | Add standard Python/Android lint checks after CI conventions are chosen; avoid broad autoformatting in the same change. `mypy` may require typing/config work; Detekt adds configuration and dependency burden. | Low-to-medium; initially may surface many pre-existing findings. |
| 5 | **Kimi #3 — rebrand strategy/scaffolding** | Low immediate security impact. | Founder/product decision first. Package/application ID changes affect installed Android identity/data migration; CLI and config names affect scripts and service units. Decide compatibility policy before rename implementation. | Medium-to-high if renaming is done as a single sweep. |
| 6 | **Kimi #4 — protocol extensions / remote operations** | Potentially very high attack impact, especially shell, file, and desktop-control features. | Defer implementation until threat model, identity/authorization, consent, capability negotiation, version compatibility, resource limits, and recovery model are agreed. The current protocol and single shared token are not sufficient design evidence for privileged remote operations. | High; can expand privileges and break interoperability. Do not build remote execution as the first Day 2 feature. |

### Evaluation notes on the six proposals

- **Project hygiene:** README and AGENTS are worthwhile, but `.gitignore` additions named in Kimi's plan are already covered. Do not tag before deciding whether the audit report is part of the baseline snapshot and before choosing version compatibility.
- **CI:** A high-value, low-risk follow-up once immediate security regressions have tests. It should not publish APKs or secrets by default.
- **Rebrand:** Needs a founder decision, not a source rename first. Preserve the existing `com.phonebridge.app`, `phone-bridge`, config paths, and systemd service until migration/compatibility is specified.
- **Protocol:** Capability negotiation could be useful eventually, but adding unauthenticated high-privilege operations is a major attack-surface expansion. No protocol extension should authorize shell/file/control actions implicitly through the current shared token.
- **Security hardening:** Kimi identified valid topics (bind, config mode, Android token encryption) but missed the map/XSS/log/retention findings. Changing the WebSocket default to loopback alone would break a phone connecting remotely; explicitly bind to a chosen Tailnet address or define a secure relay/TLS approach instead.
- **Quality tools:** Ruff and Android Lint are reasonable starter checks. Mypy and Detekt should be evaluated against current code and chosen Python/Kotlin versions; do not let enabling tools trigger unrelated large rewrites.

## 9. Recommended Day 2 Task

**Start with a security baseline and regression-test pass, not protocol expansion or rebranding.** First agree that the initial deployment is private/personal and Tailnet-only (or specify an alternative). Then address, in this order:

1. Prevent XSS from device names in map history/popups and add a regression test.
2. Keep map access local by default; if remote map access is required, add an explicit authenticated mode, remove wildcard SSE CORS, and make locate a protected POST operation.
3. Define the WebSocket binding/routing contract. Ensure the UI cannot silently downgrade `wss://`; constrain exposure to the selected trusted interface/network and document any Tailscale/firewall prerequisite.
4. Enforce private permissions for generated config and SQLite data; remove routine secret output from installation paths.
5. Redact private notification/call/location fields from normal logs and make location retention depend on local receipt time.
6. Add focused tests for all of the above, then establish CI to run the existing Python tests and Android build.

This sequence protects the existing notification/call/location features while changing only one security boundary at a time. It does not imply that the current all-interface listener is publicly reachable; actual firewall, Tailnet, and host exposure were not tested here.

## 10. Questions Requiring Founder Decisions

1. Is the supported initial deployment strictly **personal devices on a private Tailscale network**, or must Nexus work across arbitrary LANs/the public internet?
2. Should the map UI remain localhost-only, or is remote browser access a required feature? If remote, what authentication/identity provider should it use?
3. Should PhoneBridge's Android application ID, CLI name, config path, and systemd unit remain backward-compatible during the Nexus rebrand? Is retaining existing installs/data required?
4. What location-retention promise should users receive, and should notification/call contents ever be written to persistent logs?
5. Are remote terminal, file transfer, or desktop control actually approved product goals? If yes, what actions require explicit user consent, what privilege ceiling is acceptable, and is arbitrary shell execution out of scope?
6. Is the untracked `docs/NEXUS_BASELINE.md` intended to be included in the first baseline commit/tag?

## Review Commands and Scope Notes

Commands executed in this review:

```bash
git status --short
git branch --show-current
git log --oneline -5
git remote -v
git ls-files
git check-ignore -v android/dist/PhoneBridge-debug.apk \
  android/app/build/outputs/apk/debug/app-debug.apk android/local.properties \
  phone-bridge/config.toml phone-bridge/__pycache__/main.cpython-314.pyc
cd phone-bridge && python3 -m unittest discover -s tests -t . -v
cd android && ./gradlew :app:assembleDebug --console=plain
```

Additional read-only source searches examined subprocess/logging calls, protocol/parser behavior, HTML insertion/CORS, Android permissions/components, and tracked-file common secret patterns. No dependencies were installed and no tracked application/configuration files were modified; test/build activity may refresh ignored caches. No network scan, Git-history secret scan, Android device test, Tailscale deployment test, clean Gradle rebuild, or live penetration test was performed.

---

**Concise verdict:** Yes, the repository is understood well enough to proceed to Day 2. Before adding remote-operation features or exposing services beyond a trusted Tailnet, address the dashboard XSS/unauthenticated data surface, make WebSocket transport and interface binding explicit, harden local secret/data permissions, and reduce sensitive logging/fix retention semantics.
