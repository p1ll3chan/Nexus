# Trust-Boundary Review — Independent Verification

**Review date:** 2026-11-12
**Document reviewed:** `docs/security/CURRENT_TRUST_BOUNDARIES.md`
**Method:** Static source review against the PhoneBridge checkout. No source changes, no network probes, no dependency installation, and no secrets were printed.

## 1. Executive Verdict

The report is **substantially accurate** on the core facts: there is a single shared-token authentication factor validated only at the WebSocket handshake; the WebSocket listener defaults to an all-interface bind; Tailscale is used for discovery/presence only and does not gate access; the map listener defaults to loopback but is configurable and has no authentication; and the SSE stream sends `Access-Control-Allow-Origin: *`.

The review found **no material factual errors** in the report. It found several **incomplete claims** and **additional attack paths** that raise the practical severity of a few findings, but no claim that is flatly wrong. The report's main shortcoming is that it treats several conditions as theoretical where the source makes them concrete, and it omits the predictable default-token path, the local (same-machine) map exposure, and a cross-boundary XSS interaction.

**Verdict:** The report is accurate enough to be used as the basis for Day 4 threat modeling, provided the additions and severity adjustments below are incorporated.

## 2. Specific Questions — Answers

### Q1. Can a non-Tailnet client connect if network access permits?

**Yes (confirmed).** `Config.host` defaults to `0.0.0.0` (`phone-bridge/config.py:152-156`) and `BridgeServer.start()` binds exactly that host/port via `websockets.serve()` (`server.py:406-413`). No Tailnet membership check, no `origins` argument, and no peer-IP validation is applied. `TailscaleDiscovery` (`discovery.py:66-138`) is only invoked by the optional watchdog for presence logging (`server.py:386-397`) and by the `status` CLI (`main.py:319-333`); it is not consulted during handshake.

Precondition for actual connection by a non-Tailnet client: network reachability to the bound interface/port, which the repository cannot by itself establish and which was not tested. The **application** performs no Tailnet-only enforcement.

### Q2. Does the shared token provide any device identity?

**No (confirmed).** The `hello` frame carries a client-asserted `device` string and the shared `token` (`MessageProtocol.kt:24-30`; Python schema `protocol.py:210-233`). Authentication is possession of the token (`authentication.py:37-49`). The `device` name is used to key the session (`server.py:220-224`) and optionally filtered by `Config.is_device_allowed()` (`config.py:234-238`), but it is not bound to any credential. The default `enabled_devices` is empty and permits all authenticated names (`config.py:200`). Any token holder can claim any device name, and two clients may share a name.

### Q3. Can an unauthenticated client access dashboard APIs?

**Yes (confirmed).** `MapWebServer._handle_client()` serves `/api/status`, `/api/locations`, `/api/export.geojson`, `/api/export.gpx`, `/api/locate`, and `/api/events` with no authentication (`web.py:676-719`). The default bind is loopback (`config.py:204-206`), which reduces remote exposure but does not restrict other local accounts/processes on the same host, and the bind is configurable via config/env/CLI.

### Q4. Does wildcard SSE CORS create an exploitable cross-origin scenario?

**Conditionally, and the report hedges this correctly but under-specifies the preconditions.** `/api/events` sends `Access-Control-Allow-Origin: *` (`web.py:776-784`), and `_handle_client()` performs no `Origin` validation (`web.py:645-675`). A cross-origin page could in principle open an `EventSource` to the listener and read location/session events. Exploitability depends on:
- The map listener being reachable from the victim's browser (loopback bind makes this a same-machine or Private-Network-Access scenario; a LAN bind is more exposed).
- Browser mixed-content policy (an HTTPS origin generally cannot open an `http://` SSE stream to a non-local host; `http://127.0.0.1` and `http://localhost` are special-cased as potentially trustworthy but still subject to Private Network Access in Chromium).
- Private Network Access (Chromium) / equivalent protections, which require the server to opt in via `Access-Control-Allow-Private-Network` for public→private requests.

Net effect: this is a **potential** cross-origin read on loopback (partially mitigated by browser PNA) and a more practical read when the map is bound to a LAN/all-interface address. It is not a standalone "arbitrary site steals your location" without those reachability/browser conditions. The remediation (drop or scope the wildcard) remains correct.

### Q5. Are authentication checks consistently applied to every relevant WebSocket message?

**No, and this is by design but under-documented.** Authentication happens once in `_handshake()` (`server.py:185-224`). After that, `_read_loop()` (`server.py:226-252`) processes `notification`, `call`, `location`, `locate`, `ping`, and `pong` without re-checking credentials or any message-level authentication. Within an authenticated session this is conventional, but it means:
- There is no per-operation authorization; any authenticated frame is processed.
- A client-originated `locate` triggers `request_location()` to **all** connected sessions (`server.py:247-252`), a mild cross-device amplification the report does not mention.
- There is no message authentication code or per-frame integrity beyond transport; if the transport is unencrypted (default `ws://`), an active MITM can inject frames after observing the handshake.

### Q6. What happens when tokens are missing, invalid, or reused?

- **Missing (server):** `load_config()` auto-generates a token when the effective value is empty (`config.py:380-383`). It is generated in-memory and is **not persisted by `serve`**; on restart a new value is generated, and the operator is not shown it in the normal `serve` output. Practical effect: a phone configured with a prior token can silently fail to authenticate after a server restart.
- **Missing (Android):** `BridgeSettings.isConfigured()` requires a non-blank token (`Prefs.kt:18-19`); otherwise `ConnectionManager` stays in `Retrying` with "server address and token are required" (`ConnectionManager.kt:97-101`).
- **Invalid:** `Authenticator.verify()` raises `AuthError`, the server sends `error(auth_failed)` (`server.py:209-214`), and the attempt is rate-limited per source address (`authentication.py:37-66`).
- **Reused:** Multiple clients with the same token may connect; there is no detection, no session collision policy, and no per-device credential (`server.py:79-80, 96-97`).

### Q7. Are sensitive notification and GPS records protected appropriately?

**Notifications are not persisted; GPS is persisted with weak at-rest posture.** Notification/call content is not written to SQLite (`server.py:254-330`; only `_handle_location` persists). GPS records are written by `LocationStore` (`storage.py:155-208`), whose constructor creates parent directories and opens the database without setting restrictive file or directory permissions (`storage.py:93-107`) — the same weakness the report correctly identifies for the config file. GPS history (exact coordinates, battery, timestamps) is therefore potentially readable by other local accounts depending on umask and parent directory modes. Both are logged at INFO with sensitive content (coordinates, caller identity, message text) in `server.py:262-267, 303-306, 342-349`.

## 3. Verified Findings

All of the following report claims were verified against source and are **correct**:

1. Single shared-token factor, compared with `hmac.compare_digest()` (`authentication.py:37-49`).
2. Token generation via `secrets.token_urlsafe(32)` (`config.py:400-403`), with auto-generation on empty value (`config.py:380-383`).
3. Handshake ordering: first frame must be `hello`; parse → device allow-list → token verify → session creation (`server.py:185-224`). The device check precedes token verification (`server.py:204-214`).
4. 10-second handshake timeout (`server.py:35, 185-202`).
5. Failed-auth rate limit default 10/60s per source address (`authentication.py:29-35, 54-66`).
6. Device-name allow-list is optional, case-insensitive, client-spoofable (`config.py:234-238`).
7. `to_public_dict()` masks a non-empty token (`config.py:222-226`), used by `status` (`main.py:303-306`).
8. WebSocket default `0.0.0.0:8765` (`config.py:152-156`; `config.example.toml:4-5`); no `origins` argument to `serve()` (`server.py:406-413`).
9. Map default `127.0.0.1:8766` (`config.py:204-206`); no authentication on API routes (`web.py:676-719`); SSE `Access-Control-Allow-Origin: *` (`web.py:776-784`); no `Origin` check (`web.py:645-675`).
10. `ws://` on both ends; `BridgeSettings.webSocketUrl()` strips `wss://` and returns `ws://` (`Prefs.kt:21-30`); no TLS context in `serve()` (`server.py:406-413`); cleartext allowed by Android config (`network_security_config.xml:1-8`).
11. Android token stored in `MODE_PRIVATE` `SharedPreferences` (`Prefs.kt:43-59`), not Keystore-backed encryption.
12. `BridgeService` is non-exported (`AndroidManifest.xml:39-42`).
13. Tailscale is discovery-only (`discovery.py:1-6, 66-138`; `server.py:386-397`).
14. CLI token overrides and setup output exist (`main.py:70-75, 135-142, 371-379`; `scripts/install.sh:14-21`).

## 4. Incorrect or Incomplete Claims

No claim in the report is flatly wrong. The following are **incomplete** or under-weighted:

| Report claim | Assessment |
|---|---|
| "A generated value is not automatically shared with Android unless the operator provisions it" (lines 73) | **Correct but incomplete.** For `serve`, the auto-generated token is in-memory only and not surfaced; the operator is left with a running server whose token is unknown and which changes on restart. This is a concrete usability/authentication failure mode, not just a provisioning note. |
| Token comparison "reduces timing leakage" (lines 74) | **Correct but overstated in one edge.** `hmac.compare_digest` is constant-time only for equal-length inputs; `self.token.encode()` vs `candidate.encode()` of different lengths falls back to an early length check. With a fixed-length generated token (43 chars) this is minor, but a user-supplied `--token` of arbitrary length introduces a length-timing signal. Informational. |
| Rate limiting "not protection from distributed attempts" (lines 76) | **Correct.** Additional nuance: the bucket is keyed by source address only, `success` does not reset it, and bookkeeping keys are never expired/cleaned (`authentication.py:54-66`). Over a long run the `_attempts` dict can grow unboundedly with distinct sources. |
| Map "default avoids direct remote map access through the listener" (lines 79) | **Correct but incomplete.** Loopback binding does not prevent other local accounts/processes on the same host from reading location history or invoking `/api/locate`. On a multi-user Linux host this is an unauthenticated local data/control surface, not merely a remote-access question. |
| "Android app sandbox ... limits ordinary cross-app access" (lines 80) | **Correct.** Not covered: `BridgeSettings`/`Prefs` hold the plaintext token in memory and there is no key-based or encrypted-at-rest token; this is acknowledged for storage but the in-memory aspect and lack of a rotation/reset flow are only implied. |
| Threat table "Any holder of the shared token ... can inject notifications/calls/locations" (lines 141) | **Correct but missing a higher-impact consequence:** a token holder can also choose a malicious device name that is later interpolated into dashboard HTML (`web.py:443-466, 496-504`), enabling stored-style script execution in the operator's browser when they view the (loopback) map. This cross-boundary chain is not in the report. |
| "No token values ... were printed" (lines 189) | **Confirmed for this review** and the prior report. Note the repository still ships `token = "change-me"` in `config.example.toml:8`, a known placeholder that is a **valid, non-empty token** if used verbatim (see Additional Attack Paths). |

## 5. Additional Attack Paths and Missing Threat Actors

### A. Predictable default token via the example config (confirmed, precondition: operator copies it verbatim)

`config.example.toml:8` ships `token = "change-me"`. `load_config()` only generates a token when the effective value is empty (`config.py:380-383`); `"change-me"` is non-empty and is therefore accepted unchanged. The installer substitutes a random token (`scripts/install.sh:16-18`), but a manual user who copies the example config without editing or re-generating the token ends up with a **known, globally guessable shared secret**. Impact: anyone who can reach the listener and guesses/reads the documented default authenticates. This is distinct from the "placeholder only" framing; it is a live accepted value. Remediation: have the config loader reject or force-regenerate a known-weak placeholder, or ship the example without a usable token and require generation.

### B. Local multi-user exposure of GPS history and locate control (confirmed, precondition: shared Linux host)

Even with the loopback default, `/api/locations`, `/api/status`, `/api/export.*`, `/api/locate`, and `/api/events` are unauthenticated (`web.py:676-719`). On a multi-user machine, any local process can read precise location history and trigger locate requests. The report frames this mostly as a remote-exposure concern; the same-host vector is not called out.

### C. Authenticated device name → dashboard XSS → map data/control (potential, preconditions: token holder + operator opens the map)

A token holder can set an arbitrary `device` name (no credential binding), which is stored with GPS rows (`storage.py:155-186`) and rendered via `innerHTML`/Leaflet popup templates in the dashboard (`web.py:443-466, 496-504`). Because the map is served from the same origin as `/api/locate`, injected script can, subject to browser protections, read same-origin location data or invoke `/api/locate`. This was flagged in the Day 1 review but is absent from the trust-boundary threat model. It links the two otherwise-separate boundaries.

### D. Unauthenticated name-membership oracle (confirmed)

Because `device_not_allowed` (from `is_device_allowed`) is emitted before token verification and `auth_failed` is emitted after (`server.py:204-214`), an unauthenticated peer can distinguish "name is in the allow-list" from "name is not allowed" without knowing the token. The report notes this; it is correctly characterized as a limited membership oracle.

### E. Unbounded rate-limit bookkeeping (potential, low)

`Authenticator._attempts` is a `defaultdict(deque)` that is never pruned except by per-key timestamps on access (`authentication.py:54-66`). Many distinct source addresses over time can grow memory without bound. Low severity; relevant only if the listener is broadly reachable.

### F. Missing threat actors

- **Local same-machine process/user** (covered implicitly but not as a distinct actor for the map and DB files).
- **Operator-typo/misconfiguration actor**: the predictable `change-me` token path (A) is effectively a misconfiguration-by-default actor.
- **Active MITM on the unencrypted `ws://` path who acts after handshake observation** — the report lists a passive/active observer but frames it around token capture; message injection after the token is observed is the same actor with a stronger consequence (frame forgery without token replay).

## 6. Severity-Ranked Security Gaps

Severity assumes default configuration and separates confirmed behavior from exploitation preconditions.

| # | Finding | Severity | Confirmed vs potential | Preconditions for impact |
|---|---|---|---|---|
| 1 | Predictable shared token if `config.example.toml` used verbatim (`change-me`) | High | Confirmed (code accepts it) | Operator copies example config without regenerating; listener reachable. |
| 2 | WebSocket all-interface bind + no TLS, no Tailnet enforcement | High | Confirmed (defaults) | Listener reachable off-Tailnet; attacker observes/captures token and frames. |
| 3 | Map APIs unauthenticated, incl. `/api/locate` and SSE | Medium | Confirmed | Map bound non-loopback (remote) or shared-host local access. |
| 4 | Wildcard SSE CORS | Medium (conditional) | Confirmed | Browser can reach listener; browser PNA/mixed-content not blocking; cross-origin page opened. |
| 5 | Device name is unauthenticated and reaches HTML sinks | Medium | Confirmed | Token holder sets malicious name; operator views map. |
| 6 | Token/DB files created without restrictive permissions | Medium | Confirmed (no mode set) | Local multi-user host; permissive umask/parent dirs. |
| 7 | Sensitive data logged at INFO (coordinates, caller, message text) | Medium (privacy) | Confirmed | Logs retained beyond intended retention; host log access. |
| 8 | Token in CLI args / installer output / shell history | Low-Medium | Confirmed | Shared host; process listing or history access; command captured. |
| 9 | Device-name membership oracle before auth | Low | Confirmed | Attacker can reach listener and enumerate names. |
| 10 | No per-device identity / token reuse undetected / no revocation | Medium (future impact) | Confirmed (limitation) | Multi-device or remote-control use expands impact. |
| 11 | Unbounded rate-limit bookkeeping | Low | Potential | Many distinct source addresses. |
| 12 | Ephemeral auto-generated token on `serve` (unknown to operator, changes on restart) | Low (operational) | Confirmed | Operator relies on auto-gen without persisting/reading it. |

## 7. Recommended Remediation Order

The report's five-priority plan is directionally sound. This review recommends the following order, with two insertions:

1. **Close the predictable-token path (new, first):** reject or force-regenerate the `change-me` placeholder in `config.py`; ship the example without a usable token. This is the cheapest, highest-ratio fix.
2. **Network boundary:** decide Tailnet-only vs arbitrary-network; constrain bind or add real `wss://`; remove the silent `wss`→`ws` downgrade (`Prefs.kt:21-30`).
3. **Map/dashboard:** keep loopback default, require opt-in for non-loopback, add authentication if remote, scope/remove wildcard CORS, make `/api/locate` POST-only, and fix the device-name HTML sinks (`web.py:443-466, 496-504`) since it is a same-origin escalation vector.
4. **Token provisioning/storage:** enforce `0600`/`0700` modes for config **and** DB (`config.py:406-443`; `storage.py:93-107`), stop printing tokens by default, move Android credential storage to Keystore-backed encryption, and add per-device identity + rotation/revocation.
5. **Authorization model:** replace client-asserted device name as an authorization key; scope operations; uniform pre-auth rejection to close the membership oracle; bound rate-limit bookkeeping.
6. **Regression tests and deployment docs:** add the boundary tests the report lists, plus a test that the example-config token is never accepted as-is and a test asserting default binds.

## 8. Recommended Day 4 Threat-Model Priorities

1. **Adversarial reachability matrix:** enumerate which listener/bind combinations are reachable by whom (Tailnet peer, LAN host, local account, remote internet) and map each to the specific data/control surface exposed. This directly resolves the "assumption vs reality" gap the report repeatedly flags.
2. **Cross-boundary composition:** model the token-holder + device-name + map-viewing chain (C), since it combines two boundaries currently documented in isolation.
3. **Secret lifecycle STRIDE:** threat-model token provisioning, storage (both platforms), rotation, and revocation as a single lifecycle, including the `change-me` and ephemeral-generate cases.
4. **Message-level model:** decide whether authenticated-session-only processing is acceptable, or whether future remote operations require per-message authorization/MAC and per-device identity.
5. **Privacy/at-rest model:** GPS/notification retention, INFO logging of sensitive fields, and DB/config file permissions as a coherent data-protection requirement.

## 9. Review Scope

This review read `docs/security/CURRENT_TRUST_BOUNDARIES.md` and the relevant Android/Python source (`authentication.py`, `server.py`, `config.py`, `web.py`, `discovery.py`, `main.py`, `client.py`, `storage.py`, `Prefs.kt`, `MessageProtocol.kt`, `ConnectionManager.kt`, `AndroidManifest.xml`, `network_security_config.xml`, `config.example.toml`, `scripts/install.sh`, `systemd/phone-bridge.service`). No secrets were printed, no source or Git history was changed, no dependencies were installed, and no network probes or runtime tests were performed.

---

**Conclusion:** The trust-boundary report is accurate and does not require factual correction, but it should be supplemented with the predictable default-token path, the local same-machine map/DB exposure, and the device-name→dashboard cross-boundary chain before it is used as the Day 4 threat-model input. The single highest-value fix is preventing the `change-me` placeholder from ever being accepted as a live token.
