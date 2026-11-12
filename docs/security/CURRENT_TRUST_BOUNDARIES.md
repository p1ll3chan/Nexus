# Current Trust Boundaries — PhoneBridge / Nexus Remote

**Review date:** 2026-11-12<br>
**Scope:** Authentication, token handling, network bind/exposure, Tailscale assumptions, dashboard Origin/CORS behavior, and client identity. Source review only; no code was changed and no secrets or token values are reproduced here.

## Executive Summary

PhoneBridge currently has **one application authentication factor** for WebSocket clients: a shared bearer token sent in the first JSON `hello` frame. Python compares it with `hmac.compare_digest()` and can additionally restrict the client-supplied device name when an allow-list is configured. Tailscale may provide an encrypted/private network path, but **the application does not require Tailscale, validate Tailnet membership, or bind specifically to a Tailscale interface**.

The WebSocket listener defaults to `0.0.0.0:8765`; Android always constructs a `ws://` URL and the Android network security policy permits cleartext. Whether the listener is reachable from a LAN or the public internet depends on host firewall/router/network state and was not tested. The map listener defaults to `127.0.0.1:8766`, which is a useful default boundary, but it can be configured to listen elsewhere; its APIs have no authentication, it does not check request `Origin`, and the SSE response allows every origin.

The shared token is generated with a cryptographically secure random source and compared in constant time. Its operational handling is weaker: CLI and installer paths can print it, CLI flags carry it in process arguments, config creation enforces no restrictive file permissions, and Android stores it in ordinary private `SharedPreferences` rather than an Android Keystore-backed encrypted store.

**Bottom line:** Tailscale reachability and application authentication are distinct layers. Tailscale can encrypt traffic that actually traverses the Tailnet; it does not authenticate the PhoneBridge `hello` on behalf of this application, and PhoneBridge does not ensure its traffic traverses Tailscale. The shared token is application authentication, but it authenticates a token holder as a group; it does not establish a distinct, non-spoofable device identity or operation-level authorization.

## Data-Flow and Trust-Boundary Diagram

```mermaid
flowchart LR
    subgraph Android[Android device trust domain]
        UI[MainActivity / user settings]
        PREFS[Private SharedPreferences\n(host, token, device name)]
        CM[ConnectionManager / OkHttp]
        UI --> PREFS
        PREFS --> CM
    end

    subgraph Transport[Network path — deployment-dependent]
        TS[Tailscale overlay\noptional; encrypts only if traffic uses it]
        OTHER[LAN / other interface\npossible because server binds 0.0.0.0]
    end

    subgraph Linux[Linux host trust domain]
        WS[WebSocket listener\n0.0.0.0:8765 by default]
        HELLO[First JSON frame: hello\ndevice + shared token + version + platform]
        AUTH[Authenticator\nconstant-time shared-token compare\nper-source failed-attempt rate limit]
        ALLOW[Optional device-name allow-list\nempty means any authenticated name]
        SESSION[ClientSession\nname asserted by client + remote address]
        WS --> HELLO --> ALLOW --> AUTH --> SESSION
    end

    CM -->|ws://; token in hello| TS
    CM -. alternative path, firewall-dependent .-> OTHER
    TS --> WS
    OTHER --> WS

    subgraph Browser[Browser / map trust boundary]
        BROWSER[Browser dashboard]
    end
    subgraph MapListener[Linux map HTTP listener]
        MAP[HTTP map/API\n127.0.0.1:8766 by default\nconfigurable; no authentication]
        SSE[SSE events\nAccess-Control-Allow-Origin: *]
        MAP --> SSE
    end
    BROWSER -->|HTTP; no credential| MAP
    SSE -->|event stream| BROWSER

    DISC[TailscaleDiscovery\noptional status/presence query only]
    DISC -. does not gate handshake or bind address .-> WS
```

**Diagram notes:**

- Tailscale's network encryption is conditional on the actual route. The source configuration does not force that route or reject non-Tailnet peers.
- The `enabled_devices` setting is an optional string-name filter, not a separate identity credential. Authentication uses the shared token.
- The browser-to-map path is separate from the WebSocket token handshake; the map does not reuse it.
- Whether a listener is externally reachable is a deployment/network question; this review did not inspect firewall rules, router forwarding, or live Tailscale state.

## Confirmed Controls

| Control | Evidence | Security effect / limit |
|---|---|---|
| Random token generation | `phone-bridge/config.py:400-403` uses `secrets.token_urlsafe(32)`; `load_config()` generates one when the configured token is empty (`:380-383`). | Strong unpredictable bearer secret when securely provisioned. A generated value is not automatically shared with Android unless the operator provisions it. |
| Constant-time token comparison | `phone-bridge/authentication.py:37-49` uses `hmac.compare_digest()` and rejects empty configured/candidate values. | Reduces timing leakage during comparison; does not encrypt the token in transit or distinguish holders. |
| Handshake before session creation | `phone-bridge/server.py:185-224` requires the first frame to be `hello`, parses it, checks the optional device allow-list, verifies the token, and only then constructs/returns a `ClientSession`. | Unauthenticated connections do not become application sessions. The server still accepts TCP/WebSocket connections before auth; the handshake has a 10-second timeout (`server.py:35, 185-202`). |
| Failed-auth rate limiting | `Authenticator` defaults to 10 failed attempts per source in a 60-second window (`authentication.py:29-35, 54-66`); the server supplies the remote address (`server.py:139, 210`). | Slows repeated guesses per observed source address. It is not a global limit, device identity check, or protection from distributed attempts. |
| Optional device-name restriction | `Config.is_device_allowed()` compares names case-insensitively (`config.py:234-238`); server checks it during handshake (`server.py:204-207`). | Can reject names outside a configured list, but names are client-supplied and can be spoofed by any holder of the shared token. Empty list allows all token holders. |
| Token redaction in public config output | `Config.to_public_dict()` replaces nonempty `token` with a mask (`config.py:222-226`); `phone-bridge status` prints this public dict (`main.py:303-306`). | Protects the normal `status` output path; does not protect config files, process arguments, explicit token-generation/setup output, or Android storage. |
| Default map loopback bind | `Config.map_host` defaults to `127.0.0.1` (`config.py:204-206`), and `MapWebServer.start()` binds the configured host (`web.py:631-637`). | Default avoids direct remote map access through the listener, provided it remains at the default. It is configurable from file/env/CLI. |
| Android app sandbox and backup setting | `Prefs` accesses `SharedPreferences` with `Context.MODE_PRIVATE` (`Prefs.kt:43-59`) and the manifest sets `android:allowBackup="false"` (`AndroidManifest.xml:18-27`). | Limits ordinary cross-app access and disables normal Android app backup. Storage is not application-encrypted; rooted/compromised device access remains a risk. |
| Internal Android bridge service | `BridgeService` is `android:exported="false"` (`AndroidManifest.xml:39-42`). | Other apps cannot directly bind/start it through the exported component interface. This does not secure the network token or listener. |

## Authentication and Token Lifecycle

### Python CLI and configuration paths

- `phone-bridge serve --token ...` is accepted by the CLI (`phone-bridge/main.py:70-75`) and passed as a configuration override (`:35-52`). The token is therefore available in the process argument list while the command is running; if entered literally in an interactive shell, the command may also be saved in shell history. Visibility depends on host/process controls and shell configuration.
- `phone-bridge init-config --token ...` accepts a token on the command line (`main.py:135-142`). Without one, it generates a new token, writes configuration, and prints the token value to stdout (`main.py:374-379`). The standalone `token` command also intentionally prints a generated token (`:371-373`). This report does not reproduce any token output.
- The installer generates a token, substitutes it into the config template, and prints the resulting secret (`phone-bridge/scripts/install.sh:14-21`). The generated secret is also embedded in the `sed` substitution argument. No restrictive `umask` or post-write `chmod` is set in the inspected installer.
- `write_default_config()` creates parent directories and writes the token-bearing file but does not set explicit file or directory permissions (`phone-bridge/config.py:406-443`). Effective permissions depend on ambient umask and pre-existing parent directory modes.
- Config resolution order is defaults → file → environment → CLI overrides (`config.py:1-8, 338-383`). The token may consequently be provisioned through a TOML/JSON file, `PHONE_BRIDGE_TOKEN`, `serve --token`, or `init-config --token`.
- **Confirmed limitation:** Python `Authenticator` holds one configured token for all clients (`server.py:79-80`). There are no per-device tokens, token IDs, scoped credentials, revocation list, or application-driven token rotation in the inspected authentication path.

### Android storage and wire transfer

- The Android UI reads and saves the token in `Prefs` (`MainActivity.kt:80-103, 113-125`; `Prefs.kt:57-89`). It is stored in private app `SharedPreferences`, not an Android Keystore-backed encrypted preference store.
- `MessageProtocol.hello()` serializes the token in the JSON frame (`MessageProtocol.kt:24-30`); `ConnectionManager.SocketListener.onOpen()` sends that frame (`ConnectionManager.kt:136-139`). It is not sent as an HTTP authorization header and there is no challenge-response proof that avoids transmitting the bearer credential.
- `BridgeSettings.webSocketUrl()` strips a supplied `wss://` prefix and always returns `ws://` (`Prefs.kt:21-30`). The manifest references a network security config that globally allows cleartext (`AndroidManifest.xml:18-27`; `network_security_config.xml:1-8`).
- The Python URI is also `ws://` (`config.py:213-216`), and `BridgeServer.start()` calls `websockets.serve()` without a TLS context (`server.py:406-413`). There is no application TLS configuration in this path.

### Handshake identity is not device identity

The client `hello` includes `device`, `token`, `version`, and `platform` (`MessageProtocol.kt:24-30`; Python schema `protocol.py:210-233`). The server checks `device` before token validation (`server.py:204-214`), then uses that same client-asserted string in `ClientSession` (`:220-224`). The default `enabled_devices` is empty and means allow all authenticated names (`config.py:200, 234-238`).

Consequences:

- The shared secret authenticates possession of the token, not a particular phone.
- Device names are labels, not proof of identity. A token holder can claim another name or connect concurrently using a duplicate name.
- The server's session map is keyed by WebSocket connection, not a verified device key (`server.py:96-97`). No certificate/public-key binding or per-device secret is present in the inspected flow.
- Because device-name allow-list rejection occurs before authentication (`server.py:204-214`) and uses a distinct error code, an unauthenticated peer may be able to probe whether a guessed name is allowed. This is a limited name-membership oracle; it does not reveal the token.

## Network Exposure, Tailscale, and Origin Behavior

### WebSocket listener

- `Config.host` defaults to `0.0.0.0`, port `8765` (`config.py:152-156`); the example config uses the same host/port. `BridgeServer.start()` binds exactly the configured host and port (`server.py:406-413`). This is an all-IPv4-interface bind, not a Tailnet-only bind.
- `Config.uri` and the Android app construct `ws://` endpoints (`config.py:213-216`; `Prefs.kt:21-30`). In the absence of a network-layer encrypted route, both handshake token and subsequent notification/call/location data are cleartext on that path.
- The server does not pass an `origins` allow-list to `websockets.serve()` (`server.py:406-413`). No explicit WebSocket `Origin` check is present in the inspected Python application. Origin validation would be a browser-origin policy, not a replacement for bearer authentication.
- The WebSocket server applies an application message-size ceiling (`server.py:406-412`) and authenticates the first message, but source-level code does not validate that the connecting IP belongs to a Tailnet address range.

### What Tailscale does—and does not do here

- `TailscaleDiscovery` runs the local `tailscale status --json` command and parses peer names/IPs (`phone-bridge/discovery.py:66-99, 101-126`). The optional server watchdog uses it to log whether a configured phone name appears online (`server.py:386-397`). The CLI status command can display peer information (`main.py:319-333`).
- `discovery.py` is not consulted by the handshake to approve a peer; `server.py` does not compare a connecting remote address to the discovered peer's Tailscale IP; the default listener does not bind only to a Tailnet interface.
- If the configured phone endpoint is reached through Tailscale, Tailscale supplies its own encrypted network path and Tailnet access controls. If the same `0.0.0.0` port is reached over another interface, those Tailnet properties do not protect that path. The repository cannot establish the actual route or firewall policy of a running host.
- Application shared-token authentication remains valuable inside a Tailnet: it prevents an arbitrary Tailnet peer from joining unless it also has the token. Conversely, Tailscale reachability alone is not a substitute for the application's `Authenticator` check.

### Dashboard HTTP and CORS

- Map defaults are `127.0.0.1:8766` (`config.py:204-206`), but `map_host` is configurable from config/env/CLI (`config.py:241-272, 327-367`; `main.py:82-90`). `MapWebServer.start()` binds the configured host (`web.py:631-637`). The URL display rewriting `0.0.0.0` to loopback (`web.py:626-629`) does not change the actual bind.
- The map is plain HTTP. There is no authentication on `/api/status`, `/api/locations`, exports, `/api/locate`, or `/api/events` (`web.py:676-719`). A remote bind exposes location/session data and a locate trigger to any client able to reach it.
- There is no inspection or validation of the request `Origin` header in `_handle_client()` (`web.py:645-675`). The SSE response explicitly includes `Access-Control-Allow-Origin: *` (`web.py:776-784`); other routes do not add Origin-specific CORS policy. CORS is not authentication: wildcard SSE allows cross-origin browser code to read that stream where browser/network policy permits access.
- `/api/locate` accepts GET as well as POST (`web.py:710-715`). This matters if remote browser access is ever enabled; it is a state-changing unauthenticated route.

## Threat Actors and Trust Assumptions

| Actor / condition | Capability and boundary | Relevant consequence |
|---|---|---|
| Passive or active observer on a non-Tailnet network path | Can observe/modify cleartext `ws://` if the listener is reachable over that path. | Can capture the bearer token and read/inject PhoneBridge frames. Actual reachability was not tested. |
| Unauthenticated host/LAN/Tailnet peer | Can attempt WebSocket handshakes against a reachable listener. | Must pass shared-token validation to create a session; may probe allow-listed device names because that check precedes token validation. Rate limiting is per source address. |
| Any holder of the shared token | Can authenticate from a reachable source and choose a client-supplied device name, subject to optional name allow-list. | Can inject notifications/calls/locations or impersonate an allowed device name; all token holders share the same privilege. |
| Compromised Android app/device or rooted device | Can access the app's configured endpoint and token; a malicious app process can emit authenticated frames. | Device sandboxing/`MODE_PRIVATE` does not protect against same-UID compromise or root. |
| Local Linux user with read access to config/process output | May read a weakly permissioned config, observe process arguments, or capture installer/CLI output. | May recover the shared token and then impersonate a client. Actual local file modes were not inspected as part of this source-focused review. |
| Browser-origin attacker when map is reachable to the victim's browser | Can attempt cross-origin requests to the map listener; SSE allows `*`. | May read SSE events if browser networking policy allows and the listener is reachable; remote map bind exposes APIs directly without authentication. |
| Public-internet attacker | Can reach only if deployment networking/firewall/router exposes the listener. | Source defaults alone do not prove public exposure; all-interface bind and missing TLS make such exposure dangerous if it exists. |

## Prioritized Remediation Plan

This is an ordered plan, not a claim that the repository alone can fix external firewall/Tailnet configuration.

### Priority 1 — Define and enforce the network boundary

1. Founder decision: is the supported deployment Tailnet-only, or must it work on arbitrary LAN/public networks?
2. For Tailnet-only operation, bind to an explicitly selected Tailnet address/interface where practical and document a host firewall rule. Do not assume `phone_name` discovery enforces this.
3. If non-Tailnet transport is supported, implement end-to-end `wss://` support on both ends and remove silent `wss`→`ws` downgrade. Until then, clearly require a trusted encrypted network path.
4. Add an integration/deployment check that verifies actual listen addresses and rejects unintended all-interface exposure in secure mode.

### Priority 2 — Protect the dashboard independently

1. Preserve loopback-only default. Require explicit opt-in for a non-loopback bind.
2. If remote access is required, add authentication/authorization to all APIs and exports, not just the page; protect `/api/locate`; reject unintended Origins and remove wildcard SSE CORS (or scope it to a deliberate origin).
3. Make the state-changing locate action POST-only and add CSRF protection if browser credentials are used. Do not treat Origin/CORS as user identity.

### Priority 3 — Improve token provisioning and storage

1. Enforce restrictive config directory/file modes (`0700`/`0600`) in both config writer and installer; verify permissions instead of relying on umask.
2. Avoid `--token` for long-lived secrets where process listings/shell history are concerns; do not print the generated installer token by default. Keep explicit one-time generation workflows available without exposing values in routine logs.
3. Store Android credentials using an Android Keystore-backed encrypted mechanism, with a documented reset/rotation flow.
4. Add per-device credentials or another device-bound identity and a token rotation/revocation mechanism. Keep the current shared token during a backwards-compatible migration period if existing functionality requires it.

### Priority 4 — Make client authorization identify a principal

1. Replace client-asserted display name as the authorization key with a stable authenticated device identity (for example, separately provisioned device credential/public key).
2. Scope authorization by device and operation. A global shared token should not silently authorize future remote-control operations.
3. Avoid revealing device allow-list membership before successful authentication, or use a uniform pre-auth rejection response.
4. Bound/expire rate-limit bookkeeping keys and add tests for both distributed source attempts and ordinary reconnects.

### Priority 5 — Add boundary regression tests and deployment documentation

- WebSocket: valid/invalid token, first-frame/hello timeout, rate limit, configured device policy, no-Origin assumption, bind address, and TLS behavior.
- Dashboard: default loopback bind, non-loopback opt-in, unauthenticated APIs, wildcard CORS behavior, and locate side effect.
- Secret handling: CLI/setup output policy, config permissions, token redaction in status, Android storage behavior.
- Deployment: explain that Tailscale encrypts only traffic actually routed through the Tailnet and that the application shared token is still required.

## Review Notes

- Confirmed by source: default bind values, token flow, token comparison, handshake order, optional name allow-list behavior, Tailscale discovery role, map routes, CORS header, and absence of explicit Origin configuration in the inspected server setup.
- Not confirmed: real firewall rules, port forwarding, host file modes, whether an installed Tailscale service is active, actual route selection, process visibility policy, shell history configuration, or live remote reachability.
- No token values, environment values, or local credential files were printed or included in this report. No code was changed and no tests/network probes were run.

---

**Conclusion:** A Tailnet can provide an encrypted, access-controlled network path, while the PhoneBridge shared token authenticates the application session. Neither boundary currently enforces the other. The main priorities are to explicitly constrain network binding/transport, protect the configurable map listener, and strengthen token provisioning and per-device identity.
