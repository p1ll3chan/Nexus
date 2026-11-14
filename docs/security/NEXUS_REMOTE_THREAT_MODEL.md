# Nexus Remote — Android-to-Linux Threat Model

**Status:** Proposed design model; not implemented.<br>
**Scope:** An Android device controlling a paired Linux user's session over a network intended to use Tailscale. This document describes security goals and requirements for future remote-control features. It is not a statement that the current PhoneBridge prototype implements them.

## 1. Executive Summary

Nexus Remote would allow an explicitly paired Android device to request selected read and control operations on a Linux user session. The Android device and Linux host are separate security principals; a Tailnet provides network reachability and an encrypted overlay path when traffic actually traverses it, but is not a substitute for application authentication, authorization, consent, replay protection, or auditability.

The current repository implements notifications, call events, location sharing, and a locate request over a WebSocket authenticated by a shared token. It does **not** implement remote command execution, saved-script execution, a per-device identity, or the proposed `remote.*` permissions. See `CURRENT_TRUST_BOUNDARIES.md` and `CURRENT_TRUST_BOUNDARIES_REVIEW.md` for the current implementation and its gaps. The current shared-token protocol and network defaults are not sufficient authorization for privileged remote operations.

**Design rule:** deny by default. A request is allowed only when the transport is available, the client is cryptographically paired, the exact permission is granted to that client, the requested operation is in policy, the risk-level approval requirements are satisfied, and the request is fresh and within resource limits. Failure at any stage denies the operation.

## 2. Scope, Goals, and Non-Goals

### In scope

- A paired Android device requesting narrowly defined operations from a Linux user's session.
- Reading approved system/process metadata, executing approved operations, and managing/running explicitly authorized saved scripts.
- Authentication, authorization, consent, replay handling, process and file boundaries, output handling, auditing, and revocation.
- Separation between Remote-control permissions and GPS/location permissions.

### Out of scope for this model

- Root/administrator control, `sudo`, arbitrary privileged daemons, or control of other Linux users' sessions.
- Arbitrary interactive shell/PTY, arbitrary filesystem browsing, arbitrary file transfer, keyboard/mouse injection, or persistent unattended control.
- Treating Tailnet membership or a client-supplied device name as the paired-device identity.
- Any promise that the current PhoneBridge code already enforces these controls.

Adding any out-of-scope capability requires a separate threat-model revision and explicit product approval.

## 3. Assets and Security Objectives

| Asset | Examples / sensitivity | Security objective |
|---|---|---|
| Device credentials | Per-device private key, pairing credential, refresh/revocation material | Protect confidentiality; bind a credential to one paired device; support revocation and rotation. |
| Linux account and user session | User UID, session bus, processes, user-owned files and services | Preserve least privilege; never cross into root or another UID; prevent an operation from expanding its own authority. |
| Saved scripts | Script content, metadata, version/hash, execution history | Protect integrity and ownership; show exactly what will run; prevent modification between approval and execution. |
| Process metadata | PIDs, names, executable paths, owner, command-line arguments, environment | Minimize collection; treat as sensitive because it reveals user activity and may contain secrets. |
| Command output | stdout, stderr, exit status, generated data | Limit collection, size, and retention; prevent disclosure to the wrong device or logs. |
| Notifications | App name, title/body, sender/content | Keep outside Remote-control access by default; avoid command output or audit logs reproducing content. |
| GPS records | Current fixes, history, timestamps, battery/location telemetry | Keep under a separate location permission and consent path; do not expose through `remote.read`, `remote.execute`, or `remote.scripts`. |
| Authorization and approval state | Device-to-permission grants, approvals, revocations | Integrity, explicit scope, visible history, and fail-closed behavior when unavailable or inconsistent. |
| Audit records | Who requested what, when, approval, result class | Accountability without storing tokens, notification bodies, GPS coordinates, or unbounded command output. |

Security objectives: authenticated device identity; least privilege; explicit, revocable consent; confidentiality in transit and at rest; integrity and freshness of requests; bounded execution; safe failure; and auditable decisions.

## 4. System and Trust-Boundary Diagram

```mermaid
flowchart LR
    PERSON[Linux user / approver]
    PHONE[Android app\npaired device credential]
    POLICY[Linux pairing + permission policy\nremote.read / execute / scripts]
    BROKER[Linux user-session broker\nno root; validates request + approval]
    READ[Read-only providers\nallowlisted status/process metadata]
    EXEC[Constrained operation runner\nargv, limits, sandbox]
    SCRIPTS[Private script store\nversion/hash + review state]
    AUDIT[Protected audit log\nmetadata, not payloads]
    GPS[Android LocationMonitor\nseparate location permission]
    LOCSTORE[Linux LocationStore / GPS records\nseparate authorization domain]
    TS[Tailscale overlay\nreachability + network encryption only when routed]

    PHONE -->|authenticated remote.request| TS
    TS --> BROKER
    PERSON -->|local pairing, grants, approval, revoke| POLICY
    POLICY --> BROKER
    BROKER --> READ
    BROKER --> EXEC
    SCRIPTS -->|only after script + execute policy and approval| EXEC
    READ -->|bounded remote.response| BROKER
    EXEC -->|bounded output/status| BROKER
    BROKER -->|remote.response| TS
    TS --> PHONE
    BROKER --> AUDIT
    GPS -->|separate location message| TS
    TS --> LOCSTORE
    GPS -. not authorized by remote.* .-> BROKER
```

### Trust boundaries

1. **Android device boundary:** a stolen/unlocked or compromised phone can act with the device's granted permissions until revoked. A user-visible lock screen alone is not sufficient application authorization.
2. **Network/Tailnet boundary:** Tailnet ACLs limit reachability and the overlay encrypts traffic that uses it. Network membership alone does not establish which paired application/device sent a request.
3. **Application identity boundary:** the Linux broker must authenticate a specific paired device with a unique credential. A shared bearer token or self-asserted `device` string is not adequate for the proposed control features.
4. **Linux privilege boundary:** the broker and child operations run as the intended unprivileged Linux user. They must not inherit root, broad capabilities, another user's session, or unrestricted access to secrets.
5. **Approval boundary:** a remote request is data, not user consent. Linux-side approval policy must be separate from Android's request and separate from Tailscale authorization.
6. **GPS boundary:** location sharing and on-demand location access are separate from Remote control. A grant to inspect processes or run an operation grants no access to current or historical GPS.
7. **Audit boundary:** audit records are a separate sensitive asset. Logging must not turn output, notification bodies, tokens, or coordinates into a long-lived secondary data store.

## 5. Assumptions and Explicit Constraints

### Proposed assumptions to validate

- The Linux endpoint is operated by one local user and the broker acts only as that user's unprivileged UID.
- Pairing is initiated and approved locally on Linux, with a second confirmation on Android. A copied token, device name, or Tailnet identity alone is insufficient pairing.
- The user may deny or revoke access at any time; if the approval UI, policy database, identity store, or audit system is unavailable, new control operations fail closed.
- Tailscale is the intended network path, but the application must still authenticate the paired device and enforce its own authorization policy.
- Notification contents and GPS records are not inputs to command construction and are not implicitly accessible to remote operations.

### Must not be assumed

- A phone is safe merely because it is on the Tailnet.
- A shared token proves a unique phone identity.
- A previously approved request remains safe after script content or operation arguments change.
- A script's filename, extension, UI label, or source device makes it trustworthy.
- A command is safe because it is run as a normal user; that user may still own sensitive data and session services.
- Approval of one permission implies approval of another.

## 6. Threat Actors and Threat Analysis

| Threat actor / event | Attack path | Potential impact | Required mitigations |
|---|---|---|---|
| **Stolen or unlocked phone** | Attacker opens the paired app or invokes its remote API with the victim's stored credential. | Reads process metadata, runs approved operations, accesses scripts within the victim's grants. | App re-authentication for sensitive actions; local Linux approval for elevated risks; device revocation; short-lived sessions; encrypted credential storage; visible disconnect/revoke control. |
| **Leaked shared token / copied credential** | Token is obtained from CLI args/history, config, Android app storage, logs, backup, or an untrusted host. | Any holder can impersonate a client; shared token does not reveal which device leaked. | Unique per-device key/credential; protected provisioning; rotation/revocation; no secrets in args/logs; do not treat Tailnet access as identity. |
| **Compromised Tailnet device** | Malicious or compromised peer reaches the Linux port, guesses identity, or exploits a leaked shared token. | Attempts authentication; if credential is shared/leaked, invokes all grants associated with it. | Tailnet ACLs plus app authentication; per-device identity; allow-list at both network and application levels; rate limits; firewall/interface binding. |
| **Replay of a valid request** | Captured or duplicated `remote.request` is resent after a timeout, reconnect, or approval. | Repeats a command or script; causes duplicate state changes or data disclosure. | Fresh nonce/request ID; session/device binding; expiry; replay cache; idempotency keys; single-use approvals bound to exact request digest; duplicate response handling. |
| **Malformed or oversized payload** | Invalid JSON, deeply nested/large fields, invalid Unicode, unexpected enum/action, huge argument list or response. | Parser crash, resource exhaustion, policy bypass, path traversal or unsafe operation dispatch. | Strict schema and type validation; byte/field/count limits; timeouts; bounded queues; explicit enum allow-list; fuzz/property tests; recoverable parser errors that do not bypass policy. |
| **Command injection** | Attacker embeds shell metacharacters or crafted arguments in request fields; implementation concatenates a command string. | Arbitrary code execution as the Linux user, data theft or destructive changes. | Do not pass untrusted strings to a shell. Use operation IDs and typed argument schemas; `execve`/argument arrays; strict validation; fixed working directory/environment; deny shell operators and executable paths from the client. |
| **Privilege escalation** | A runner inherits privileged credentials, invokes `sudo`, follows writable paths/symlinks, or controls a privileged helper. | Root access, other-user access, persistent host compromise. | Dedicated unprivileged UID or tightly scoped user service; no `sudo`/setuid/capabilities; filesystem and process sandboxing; safe path handling; no privileged helper in initial scope. |
| **Log leakage** | Tokens, command output, script bodies, process command lines, notification content, or GPS are written to journald/files and retained. | Credential theft, exposure of user activity/location, secondary compromise. | Structured metadata-only audit logs; redaction; bounded retention and restrictive file modes; output not logged by default; separate opt-in diagnostics. |
| **Malicious saved script** | A token holder creates/edits/imports a script and later runs it under the paired user's authority; content changes after approval. | Arbitrary user-level code execution, data exfiltration, persistence, destructive actions. | `remote.scripts` does not imply execution; `remote.execute` also required; local review/approval per content hash; immutable version at execution; run approval bound to digest; sandbox, resource/network limits; no arbitrary script execution in initial release. |
| **Malicious or compromised Linux account/session** | Local malware modifies broker policy, scripts, binaries, or logs, or acts as the same UID. | Can bypass local approvals and alter results; can impersonate the broker. | Treat same-UID compromise as outside the broker's protection boundary; protect pairing/config/scripts with OS permissions; integrity checks; clearly state residual risk. |
| **Approval fatigue / social engineering** | Repeated prompts disguise dangerous requests as diagnostics or flood the user until they approve. | User approves a harmful action without informed consent. | Human-readable operation and exact arguments; risk labels; rate-limited prompts; deny by default; no “approve all future commands” switch; local display of script digest/content. |
| **Tailnet ACL or route misconfiguration** | Host is reachable via LAN/public interface even though operators believe it is Tailnet-only. | Expands attack surface; cleartext `ws://` or unauthenticated local services exposed. | Bind to intended interface; host firewall; startup diagnostics; deployment verification; treat Tailnet as defense in depth, not the only app control. |

## 7. Operation Risk and Approval Principles

Risk levels and concrete permissions are defined in `REMOTE_PERMISSION_MODEL.md`. This threat model requires the following approval properties:

- Permission grants are **per paired device**, explicit, default-deny, scoped, visible, and revocable. Granting `remote.execute` must never implicitly grant `remote.read`, `remote.scripts`, GPS, notification access, or another permission.
- Requesting an operation is not approval. The broker checks identity, permission, operation policy, freshness, input limits, and required local approval independently.
- Approval is bound to the exact operation ID, canonical arguments, target account, and (for scripts) immutable content digest. Any change requires a new approval.
- No remote request may approve itself, modify the approval store, grant permissions, disable auditing, or bypass local policy.
- If no local approver is available, approval-required operations are denied; there is no silent headless fallback.
- Revoking a device immediately prevents new work and cancels or terminates in-flight work where safe. The exact treatment of an already-running process must be documented and tested.

## 8. Secure Request Lifecycle Requirements

Proposed lifecycle for every future `remote.request`:

1. **Authenticate principal:** verify a unique paired-device credential, not only a client-provided name or shared token.
2. **Validate session and freshness:** enforce negotiated protocol/capabilities, request ID uniqueness, nonce/expiry, maximum frame size, and replay policy.
3. **Authorize exact capability:** match the exact permission string, device grant, operation ID, and requested resource; unknown permissions and operations deny.
4. **Classify risk:** apply the operation's documented risk level and approval rule before dispatch.
5. **Freeze approved input:** canonicalize and bind approval to immutable arguments or script hash.
6. **Execute with least privilege:** use a narrow executor, unprivileged account, no shell interpolation, fixed environment/cwd, bounded CPU/memory/time/output, and explicit cancellation behavior.
7. **Return a bounded result:** correlate `remote.response` with request ID and device/session; sanitize errors; do not return secret-bearing environment, full command lines, notification text, or GPS unless a distinct permission allows it.
8. **Audit metadata:** record principal, operation, permission, risk, approval/denial, request ID, timing, exit class, and script digest where applicable. Do not log credentials or raw sensitive payloads by default.

## 9. Residual Risks and Open Decisions

1. **Identity and pairing:** select the device credential and Linux-side pairing ceremony; define how lost/stolen phones are revoked.
2. **Transport guarantee:** decide whether Tailnet-only is a hard requirement or whether remote WSS access is supported. In either case, do not rely on a client-entered hostname to prove route security.
3. **Linux execution boundary:** choose a dedicated broker account versus a user service, and enumerate what parts of the user's session it may access.
4. **Script policy:** decide whether saved scripts are in scope at all for the first release. Initial recommendation: no remote script execution until sandboxing, review, digest binding, and consent UX exist.
5. **Offline/headless approvals:** specify whether operations fail closed when no Linux UI session is active. Initial recommendation: yes.
6. **Output and retention:** choose output size, content filtering, device-side storage, and deletion/retention policy.
7. **GPS separation:** define any future `location.read`/on-demand location grant separately from `remote.*`; current location sharing settings and Android OS permission remain independent.

---

**Security baseline:** Tailscale is a network reachability/encryption layer, not the paired-device identity or operation authorization layer. Remote control must be designed as a separate, explicitly approved capability set with GPS access kept outside it.
