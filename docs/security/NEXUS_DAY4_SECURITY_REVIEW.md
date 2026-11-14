# Nexus Day 4 — Independent Security Architecture Review

**Review date:** 2026-11-12
**Documents reviewed:**
- `NEXUS_REMOTE_THREAT_MODEL.md` (threat model)
- `REMOTE_PERMISSION_MODEL.md` (permission model)
- `CURRENT_TRUST_BOUNDARIES.md` (current-state trust boundaries)
- `CURRENT_TRUST_BOUNDARIES_REVIEW.md` (current-state verification)

**Method:** Documentation review only. No source code, original documents, dependencies, or network state were modified, installed, or probed. No secrets were printed. Findings are tagged **[Confirmed omission]**, **[Confirmed ambiguity]**, **[Recommendation]**, or **[Assumption to validate]** to distinguish verified document gaps from reviewer suggestions.

---

## 1. Executive Verdict

**Ready with conditions.**

The proposed design is directionally sound: deny-by-default authorization, a per-device principal distinct from network membership and the shared token, strict GPS/notification separation, explicit prohibition of arbitrary shell and R4 operations, approval bound to exact request digests, and honest acknowledgment of same-UID residual risk. The core permission decision matrix (`REMOTE_PERMISSION_MODEL.md` §5) holds up under combination analysis — no pure permission-pairing produces an execution approval bypass.

However, the review found **two confirmed current-state attack paths absent from the threat model** (browser/device-name XSS; the predictable `change-me` default token during migration), **one internal inconsistency** in the permission model (R1 read-via-job permission string), and **several unclassified or underspecified operations** (script export/listing, operation-registry governance, network egress, dashboard-exposure prohibition, pre-approval scope). There are also **five unresolved foundational decisions** that would cause two developers to build incompatible or insecure systems if implementation started today.

Conditions for "ready": complete the P0 document revisions in §6 and resolve the blockers in §7 before any implementation work. The model should not be implemented as written without those changes.

---

## 2. Review Area Findings

### 2.1 Threat-model completeness

**Coverage confirmed as adequate:**

- All assets named in the task appear in `NEXUS_REMOTE_THREAT_MODEL.md` §3 "Assets and Security Objectives" (device credentials, Linux account/session, scripts, process metadata, command output, notifications, GPS records), plus two correctly added assets (authorization/approval state, audit records).
- All nine threats named in the task appear in §6 "Threat Actors and Threat Analysis" (stolen phone, leaked shared token, compromised Tailnet device, replay, malformed payloads, command injection, privilege escalation, log leakage, malicious saved script), plus three valuable additions (approval fatigue, malicious Linux account, Tailnet misconfiguration).
- Local attackers (same-UID compromise, §6 row "Malicious or compromised Linux account/session"), compromised endpoints (stolen phone, compromised Tailnet device, compromised Linux account), and network exposure (§6 row "Tailnet ACL or route misconfiguration") are represented.
- Trust boundaries (§4) are well-chosen, especially boundaries 2/3 separating Tailnet membership from application identity, and boundary 5 treating a remote request as data rather than consent.

**Confirmed omissions:**

1. **[Confirmed omission] Browser attacker and the device-name XSS chain are absent from the threat model.** §6 contains no browser-origin attacker row and no mention of the dashboard. Yet `CURRENT_TRUST_BOUNDARIES.md` ("Threat Actors and Trust Assumptions", row "Browser-origin attacker when map is reachable") documents the browser-origin actor, and `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.C documents a concrete chain: an authenticated token holder sets a malicious `device` name that is rendered via `innerHTML`/Leaflet popups (`web.py:443-466, 496-504`), executing script in the operator's browser on the map origin — the same origin as unauthenticated `/api/locate` and location APIs. A malicious *paired device* is exactly the actor the new threat model exists to consider, and the threat model's §6 "Malicious saved script" row proves the model intends to cover token-holder/credential-holder adversarial behavior — but the browser-mediated consequence is missing. If any remote-control surface is ever added to the dashboard origin, this becomes a full authorization bypass channel.
2. **[Confirmed omission] The predictable `change-me` default token is not represented as a live threat.** §6 "Leaked shared token / copied credential" lists leak vectors (CLI args, history, config, storage, logs) but omits the shipped default. `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.A and §6 (row 1, severity High, confirmed) establish that `config.example.toml:8` ships `token = "change-me"`, which `load_config()` accepts as a valid non-empty token (`config.py:380-383` only regenerates on *empty*). During any migration period where the shared token still authenticates sessions, this is the single most likely credential-compromise path, and the threat model's §9.7 (credential migration open decision) does not sequence its remediation as a prerequisite.
3. **[Confirmed omission] No distinct other-local-user (non-root, different UID) actor.** §6's local-attacker row is framed as same-UID compromise. `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.B documents a different confirmed actor: other local accounts reading GPS history and triggering locate via the unauthenticated loopback map, and (by extension) reading weakly-permissioned config/DB/script-store files. The threat model's mitigations mention "restrictive file modes" (log-leakage row, malicious-account row) but never name the actor or enumerate the files in scope (config, policy store, script store, audit log, GPS DB).
4. **[Confirmed omission] No explicit rule that `remote.*` must never be exposed via the dashboard/HTTP surface.** The threat model diagram (§4) routes `remote.request` only through TS→BROKER, but nothing *prohibits* a future developer from adding a dashboard "run" button backed by an HTTP endpoint — and the current map HTTP server is unauthenticated (`CURRENT_TRUST_BOUNDARIES.md` "Dashboard HTTP and CORS"). This guidance existed in `docs/architecture/PHONEBRIDGE_MESSAGE_FLOW_REVIEW.md` §7 item 6 ("Do not expose these operations through the unauthenticated dashboard HTTP routes") but was not carried into either new document.
5. **[Confirmed ambiguity] Capability negotiation is required but not downgrade-resistant.** §8.2 requires "negotiated protocol/capabilities," and `REMOTE_PERMISSION_MODEL.md` §1 correctly states a shared token "does not imply any `remote.*` permission" — so policy-level coexistence is handled. But nothing requires the negotiation itself to be authenticated/bound to the per-device credential, and nothing states that legacy shared-token sessions can never be *granted* `remote.*` even by an operator action. A downgrade variant (peer claims no remote capability to stay on the weaker legacy path, or an implementation accepts a legacy-authenticated frame carrying `remote.*`) remains implementable within the written requirements.

**Recommendations (not confirmed omissions):**

- **[Recommendation]** Add a DoS row beyond malformed payloads: request flooding the broker/approval queue and unbounded replay-cache growth. §6's malformed-payload row covers parser resource exhaustion; approval-flood is partially covered by "rate-limited prompts" in the approval-fatigue row; an explicit request-rate/replay-cache bound would close it.
- **[Recommendation]** Add a dedicated "malicious or trojanized paired application" actor row. Its consequences are partially spread across the stolen-phone and malicious-script rows, but a legitimately paired yet adversarial client is a distinct actor once per-device credentials exist (the credential is valid; the intent is not).
- **[Assumption to validate]** §5 "Proposed assumptions to validate" assumes a single-operator Linux endpoint. If a second paired device or second Linux user is ever supported, boundary 4 (Linux privilege) and the single-approver approval model need rework. State this as a hard v1 constraint.

### 2.2 Permission-model correctness

**Strengths confirmed:**

- The three permission strings are genuinely distinct in kind: `remote.read` (data), `remote.execute` (registered operations), `remote.scripts` (store management). `REMOTE_PERMISSION_MODEL.md` §2 explicitly scopes each with "May include" / "Does not include" lists.
- GPS and notification independence is real and consistently enforced: §3 "Explicit GPS and Notification Separation" in its entirety; §5 matrix rows 6–7 ("Neither..."); threat model §4 boundary 6, §8.7, §9.7. No `remote.*` string grants location or notification data, and audit records are barred from containing them by default (§8). This requirement from the task is fully met.
- No-permission-implies-another is stated three ways (§1, §5 closing note, threat model §7 first bullet) and the conjunctive script-run rule (§5 row 5) is correct.
- R4 denial is unambiguous (§4 R4 row; §5 final row "No combination is sufficient").

**Confirmed ambiguities / inconsistencies:**

1. **[Confirmed ambiguity] R1 permission string is internally inconsistent.** §4 "Operation Risk Levels and Approval Rules", R1 row: "Required permission: `remote.read` for metadata; `remote.execute` if a registered process/job is launched to produce it." But §5 "Permission Combination and Decision Matrix", row 2: "Read process metadata / sensitive diagnostics | `remote.read`" with no conditional. Two developers can implement "process listing" differently — one reads `/proc` directly (grants `remote.read`), another spawns `ps` (demands `remote.execute`) — for the identical user-visible action. Authorization should key on action semantics, not implementation detail. Related: §4's R2 example ("specific user service status/restart") means a *read-only* service status query via `systemctl` would be R2/`remote.execute` while a process listing is R1/`remote.read`, an inconsistency users and implementers will trip over.
2. **[Confirmed omission] Script listing and export are unclassified.** §2 `remote.scripts` "May include" listing and "Create, edit, import, export, or delete script content." But §4's R3 row names only "create/edit/import/delete a script," and §5's matrix has a row only for "Create/edit/import/delete saved script." Neither listing (identifier/metadata disclosure) nor **export (full script-content disclosure)** has a risk tier or an approval rule. Scripts routinely embed secrets (tokens, paths, hostnames), so an ungated export under a standing `remote.scripts` grant is a data-disclosure channel exceeding the section's own metadata framing. This is also the concrete answer to "can permission combinations accidentally grant excessive access": not via the matrix itself, but via this unclassified operation.
3. **[Confirmed ambiguity] Pre-approval scope is contradictory in effect.** §2 `remote.execute` permits "a future reviewed design [to add] a narrow, user-created pre-approval for a specific operation ID and exact parameter bounds," and §6 defines pre-approval records. But the threat model §7 "Operation Risk and Approval Principles" never mentions pre-approvals and its second bullet ("Requesting an operation is not approval") plus per-request approval language implies they do not exist. As written, one team may ship v1 with pre-approvals and another without. The v1 scope must be fixed: forbid, or fully specify (risk ceiling, expiry, single-operation ID, argument bounds, revocation).
4. **[Recommendation]** §2 `remote.read`'s "optionally executable basename/resource usage" and §4 R1's "bounded diagnostic output that may contain paths" leave the default field set to implementer discretion. §9.2 already flags this; it should be resolved to a closed default allow-list with opt-in extras, not "optional" per-field.

### 2.3 Remote execution safety

**Confirmed strengths:**

- Arbitrary shell is explicitly prohibited in four places: `REMOTE_PERMISSION_MODEL.md` §1 ("must not support... arbitrary shell"), §2 `remote.execute` "Does not include: Arbitrary shell, PTY, client-selected executable paths, command-string concatenation..."; §7.2 ("never interpolate untrusted input into a shell command"); and `NEXUS_REMOTE_THREAT_MODEL.md` §6 command-injection row. R4 (§4) denies shell categorically.
- Allow-listed operation IDs with typed argument schemas (§7.1–7.2), unprivileged UID / no sudo / no capabilities (§7.3), fixed cwd/environment/symlink handling (§7.4), timeout/CPU/memory/process/output/concurrency limits and cancellation (§7.5–7.6), digest-bound immutable script execution (§7.7), and destructive-action approval (R3 per-request; R4 denied) are all specified.
- Approval-to-execution binding to canonical arguments and content digest (threat model §7 third bullet; permission model §4 "Approval mechanics") correctly prevents TOCTOU between approval and dispatch for content.

**Confirmed gaps:**

1. **[Confirmed omission] No prohibition on generic/wrapper operations in the registry.** §7.1 says "Dispatch by a server-defined operation ID, not a client-provided shell string or executable path." An operation registry containing e.g. `custom.run {argv: [...]}` satisfies that sentence literally while reintroducing arbitrary execution. §7.2's "strict per-operation schemas" constrains arguments but does not forbid a generic pass-through operation. There is also no registry governance requirement (who reviews and admits operation IDs). This is the most direct route by which the written model could be implemented "correctly" yet fail completely.
2. **[Confirmed omission] Network egress for child processes is missing from the normative constraints.** §7 "Execution and Output Constraints" lists timeout, CPU/memory/process limits, output bytes, concurrency, cancellation, cleanup — no network restriction. The threat model §6 malicious-script row gestures at "sandbox, resource/network limits," but the normative execution section omits it, and no sandbox mechanism is named (namespaces, seccomp, systemd slice/scope). A malicious script or compromised operation can exfiltrate user data over the network even with every listed constraint enforced.
3. **[Confirmed ambiguity] Approval/request flooding bounds are unspecified.** "Expires quickly" (§4 Approval mechanics), "short-lived sessions" (threat model §6 stolen-phone row), "rate-limited prompts" (§6 approval-fatigue row), and replay-cache size/TTL carry no numbers or defaults. Implementers will pick arbitrary values; the review cannot verify these controls are meaningful as written.
4. **[Recommendation]** Output truncation semantics ("truncate safely", §7.6) should specify encoding handling (binary rejection or safe replacement) and whether truncation is fail-visible to the requester (the audit "output-truncated flag" in §8 suggests yes; make it a response field requirement too).

**Combination-bypass analysis (task question):** No low-risk permission combined with another listed capability bypasses a higher-risk approval *within the written matrix* — `remote.read`+`remote.scripts` yields metadata plus store management only; `remote.scripts`+`remote.execute` still requires per-run approval; script content change invalidates approval. The bypass channels are all *around* the matrix: (a) ungated script export (§2.2 finding 2); (b) a loosely implemented pre-approval (§2.2 finding 3); (c) exposure via the unauthenticated dashboard origin (§2.1 finding 4); (d) a generic wrapper operation (finding 1 above). Each is addressed in §6 below.

### 2.4 Identity and authentication

**Confirmed strengths:**

- The four layers are cleanly distinguished: network membership (threat model §4 boundary 2), application authentication (boundary 3 / §8.1), device identity (permission model §6: principal is "a stable authenticated device identity, not the current `hello.device` string or a shared token"), and authorization (§1/§5). The "Must not be assumed" list (threat model §5) explicitly rejects Tailnet-as-safety and shared-token-as-identity.
- Revocation semantics are specified (permission model §6: immediate invalidation, session termination, approval clearing, in-flight job policy; re-pairing creates a new principal with no inheritance; fail-closed on unknown/revoked/stale state).

**Confirmed gaps:**

1. **[Confirmed omission] Credential mechanism is unresolved — an implementation blocker, acknowledged but load-bearing.** Threat model §9.1 leaves the device credential and pairing ceremony open; permission model §9.7 leaves provisioning/rotation/revocation/migration open. Algorithm, storage (Android Keystore is only a mitigation mention in threat model §6), verification on Linux (key registry? signatures per request or per handshake?), and the pairing ceremony are all undefined. Every other control in the model presumes this layer exists.
2. **[Confirmed ambiguity] Rotation procedure is named but not specified.** "Rotation/revocation" appears as a mitigation (§6 leaked-token row) and open decision (§9.7), but dual-credential grace periods, forced re-pair vs. in-place rotation, and what happens to in-flight requests during rotation are undefined. Threat model §7 acknowledges the in-flight-job question but does not answer it.
3. **[Recommendation]** Compromised-device *detection* is out of model scope (revocation is the recovery mechanism). Consider at minimum a documented "revoke all devices" panic action and a visible indicator of the currently-connected principal on Linux, so an operator can act on suspicion.
4. **[Recommendation]** Client-side "App re-authentication for sensitive actions" (threat model §6 stolen-phone row) is unverifiable by the server; the model correctly relies on Linux-side approval as the real control for elevated operations, but this asymmetry should be stated so implementers do not treat app-lock as a security boundary for R1+.

### 2.5 Implementation readiness

Decisions that must be resolved before implementation (each would produce divergent behavior across developers):

| # | Unresolved decision | Where acknowledged | Why it blocks |
|---|---|---|---|
| 1 | Device credential mechanism, pairing ceremony, rotation | Threat model §9.1; permission model §9.7 | Foundation of every authorization check |
| 2 | Broker execution context (dedicated UID vs user service) and concrete sandbox mechanism | Threat model §9.3; permission model §7 has no mechanism | Determines the entire privilege boundary and whether egress/isolation claims are enforceable |
| 3 | Transport policy (Tailnet-only hard requirement vs WSS) | Threat model §9.2 | Determines whether request confidentiality relies on an assumption or a guarantee; current client silently downgrades `wss`→`ws` (`Prefs.kt:21-30`, per `CURRENT_TRUST_BOUNDARIES.md` "Android storage and wire transfer") |
| 4 | Approval UI availability and fail-closed semantics | Threat model §9.5 (recommendation only); permission model §9.5 | "Recommendation: yes" is not a requirement; headless hosts would get different behavior per implementer |
| 5 | Script execution in v1 scope | Threat model §9.4; permission model §9.1 (both recommend deferral) | Recommendation must be ratified or the entire `remote.scripts`+run surface enters v1 unreviewed |
| 6 | R1 permission-string resolution and default process-metadata field set | This review §2.2; permission model §9.2 | Two implementers grant different permissions for the same action |
| 7 | Operation registry governance and anti-generic-wrapper rule | This review §2.3 (absent from model) | Without it, the allow-list can legally contain an arbitrary-execution operation |
| 8 | Script listing/export classification and approval rule | This review §2.2 (absent from model) | Determines whether `remote.scripts` grants content disclosure |
| 9 | Pre-approval v1 scope | This review §2.2; permission model §2/§6 vs threat model §7 | Contradictory signals between the two documents |
| 10 | Quantitative bounds: approval TTL, session lifetime, output caps, request/prompt rate limits, replay-cache size/TTL | This review §2.3; threat model §9.6 (output only) | Controls exist in name but not in enforceable form |

Additional ambiguities (resolvable during design, listed for completeness): audit storage location/permissions/retention mechanism (permission model §8 names properties, not mechanisms); multi-device-per-user policy (unstated); whether the existing dashboard locate flow is folded into a future `location.read` (threat model §9.7 gestures, does not decide); binary/encoding handling in output truncation.

---

## 3. Confirmed Design Strengths

1. **Deny-by-default lifecycle ordering** (threat model §1 "Design rule", §8): identity → freshness → authorization → risk → frozen input → least-privilege execution → bounded result → metadata-only audit. The order is correct; no stage can be skipped by a later one.
2. **GPS/notification separation** (permission model §3; threat model §4 boundary 6, §9.7): genuinely independent, reinforced in the decision matrix, response constraints (§8.7), and audit prohibitions (§8). Meets the task requirement without exception.
3. **Arbitrary shell / R4 denial** (permission model §1, §2, §4 R4, §5; threat model §6): explicit, repeated, and categorical.
4. **Approval binding to exact digests and arguments** (threat model §7; permission model §4 "Approval mechanics", §7.7): correctly prevents approval reuse after content or argument change, and forbids approval originating from the request itself.
5. **Conjunctive script-run rule** (`remote.scripts` **and** `remote.execute`, permission model §2/§5): the correct structure for a two-capability dangerous action.
6. **Honest residual-risk treatment** of same-UID compromise (threat model §6 last-rows, §9): declaring it outside the broker's protection boundary is more secure than pretending an in-process check defends against it.
7. **Current-state honesty** (threat model §1, permission model §7 closing note): both documents state plainly that the prototype implements none of this, preventing the docs from being read as a description of existing behavior.
8. **Layer separation** of Tailnet membership vs application identity (threat model §4 boundaries 2–3, §5 "Must not be assumed"): the central lesson of the Day 3 audits is carried forward correctly.

---

## 4. Missing or Ambiguous Requirements (Consolidated)

| # | Requirement gap | Type | Source |
|---|---|---|---|
| G1 | Browser-attactor/device-name XSS threat absent from threat model | Confirmed omission | This review §2.1-1; `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.C |
| G2 | `change-me` predictable default token absent as a migration-period threat; fix not sequenced as prerequisite | Confirmed omission | This review §2.1-2; `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.A, §6-1 |
| G3 | Other-local-user actor and file-permission attack surface (config/policy/script/audit/GPS DB) unenumerated | Confirmed omission | This review §2.1-3; `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.B |
| G4 | No prohibition on exposing `remote.*` via dashboard/HTTP origin | Confirmed omission | This review §2.1-4; `PHONEBRIDGE_MESSAGE_FLOW_REVIEW.md` §7-6 |
| G5 | Downgrade-resistant, credential-bound capability negotiation; legacy sessions never grantable `remote.*` | Confirmed ambiguity | This review §2.1-5 |
| G6 | R1 permission-string inconsistency (read vs read-via-job) between §4 and §5 of the permission model | Confirmed ambiguity | This review §2.2-1 |
| G7 | Script listing/export unclassified; export = ungated content disclosure under standing grant | Confirmed omission | This review §2.2-2 |
| G8 | Pre-approval scope contradictory between permission model (§2, §6) and threat model (§7) | Confirmed ambiguity | This review §2.2-3 |
| G9 | No anti-generic-wrapper rule or operation-registry governance | Confirmed omission | This review §2.3-1 |
| G10 | Network egress limits and concrete sandbox mechanism absent from normative execution constraints | Confirmed omission | This review §2.3-2 |
| G11 | Quantitative bounds unspecified (approval TTL, session lifetime, output caps, rates, replay cache) | Confirmed ambiguity | This review §2.3-3, §2.5-10 |
| G12 | Credential mechanism/pairing/rotation unresolved | Confirmed omission (acknowledged open decision) | Threat model §9.1; permission model §9.7 |
| G13 | Approval UI availability treated as recommendation, not requirement | Confirmed ambiguity | Threat model §9.5; permission model §9.5 |
| G14 | Default process-metadata field set left "optional" | Recommendation to close | Permission model §2, §9.2 |
| G15 | Request/approval flood DoS and replay-cache growth not explicitly bounded | Recommendation | This review §2.1 rec. 1 |

---

## 5. Attack Scenarios That Remain Possible

Assuming the model is implemented as written, minus the gaps above. Preconditions stated explicitly.

| # | Scenario | Precondition | Mitigated by |
|---|---|---|---|
| A1 | Stolen, unlocked phone reads R0 status until revocation | Physical possession + unlock | Accepted residual (low sensitivity); revocation UX; threat model §6 row 1 |
| A2 | Malicious paired device reads script contents via export, including embedded secrets | `remote.scripts` granted; export left ungated (G7) | Only by closing G7 |
| A3 | Registered generic wrapper operation reintroduces arbitrary execution | Registry ungoverned (G9) | Only by closing G9 |
| A4 | Device-name XSS executes in operator's browser on map origin; reads GPS, triggers locate; escalates to anything later added to that origin | Current dashboard unfixed + any token/credential holder (G1) | Fix dashboard rendering + G4; `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §5.C |
| A5 | `change-me` default token authenticates a legacy session during migration | Operator copies example config verbatim; listener reachable (G2) | Reject placeholder in `load_config`; `CURRENT_TRUST_BOUNDARIES_REVIEW.md` §7-1 |
| A6 | Same-UID local malware alters scripts/policy/audit, bypasses approvals | Linux account already compromised | Declared out-of-boundary residual (threat model §6); OS permissions + integrity checks reduce, not remove |
| A7 | Approval fatigue: operator approves harmful R2/R3 request under prompt pressure | Human factor | §4 approval mechanics (arguments shown, rate-limited prompts); residual remains |
| A8 | Request flood exhausts broker/approval queue or replay cache | Listener reachable; bounds unspecified (G11/G15) | Close G11/G15 |
| A9 | Tailnet ACL misconfiguration exposes cleartext `ws://` on LAN | Deployment error (threat model §6 last row) | Bind/firewall/startup diagnostics; deployment risk remains |
| A10 | Pre-approval implemented broadly, letting a device repeat an R2 operation without per-request approval | G8 left unresolved | Forbid pre-approvals in v1 or fully specify |
| A11 | Malicious script exfiltrates user data over network despite CPU/memory/output limits | G10 (no egress restriction) | Close G10 |

---

## 6. Prioritized Required Changes

**P0 — must land in the documents before any implementation:**

1. Add browser-attacker + device-name-XSS actor/chain to the threat model (§6), and add an explicit requirement: `remote.*` operations are reachable **only** via the authenticated per-device channel; never via the map/HTTP/dashboard surface; legacy shared-token sessions can never be granted or exercise `remote.*` (closes G1, G4, half of G5).
2. Add the `change-me` predictable-default-token as an explicit migration-period threat, and sequence its remediation (reject placeholder in `load_config`; ship example without a usable token) plus the current High findings (bind policy, dashboard auth, XSS fix) as **prerequisites** to remote-control work (closes G2).
3. Resolve the R1 permission-string inconsistency: classify by user-visible action. Recommendation: all reads are `remote.read` regardless of implementation, and read operations must not spawn processes; job-based diagnostics are R2 `remote.execute` by action, not by implementation (closes G6).
4. Add an anti-generic-wrapper rule ("no registered operation may accept an executable, command line, script body, or unbounded argument vector; every operation must have a reviewed, narrow schema") and registry governance (security review + documented effect per operation ID) (closes G9).
5. Classify script listing (recommend R1-equivalent, per-request approval or explicit grant-time opt-in) and export (recommend R3-equivalent: per-request approval, since it discloses content) in §4 and add both rows to the §5 decision matrix (closes G7).
6. Forbid pre-approvals in v1 (align threat model §7 and permission model §2/§6); revisit only via a reviewed amendment (closes G8).
7. Add network-egress restriction for child processes and name the sandbox decision as a blocker to resolve (closes G10 partially).
8. Require authenticated, downgrade-resistant capability negotiation bound to the device credential (closes remainder of G5).

**P1 — resolve during design, before `remote.execute`/`remote.scripts` implementation:**

9. Decide the credential mechanism, pairing ceremony, Android Keystore storage, and rotation procedure (closes G12).
10. Decide broker execution context (dedicated UID vs user systemd user-service) and the concrete sandbox mechanism (systemd sandboxing directives, namespaces, or seccomp) (completes G10).
11. Decide transport policy (Tailnet-only hard requirement vs WSS) and remove the silent `wss`→`ws` downgrade as a prerequisite.
12. Ratify fail-closed approval-UI availability as a requirement, not a recommendation (closes G13).
13. Ratify script-execution deferral for v1 (closes the §9.4/§9.1 recommendation).
14. Specify quantitative defaults: approval TTL, session lifetime, output byte caps, request/prompt rate limits, replay-cache size/TTL (closes G11, G15).

**P2 — improve during implementation:**

15. Close the default process-metadata field set (G14).
16. Specify audit storage mechanism, permissions, and retention period.
17. State single-operator / single-approver as a hard v1 constraint, or design multi-approver semantics.
18. Add the "revoke all devices" panic action and connected-principal visibility on Linux.

---

## 7. Implementation Blockers

The following must be answered before writing code; as written, two competent developers would produce different security behavior:

1. **Device credential mechanism + pairing ceremony** (threat model §9.1; permission model §9.7) — everything else presumes it.
2. **Broker execution context + sandbox mechanism** (threat model §9.3; permission model §7) — determines whether privilege and egress claims are enforceable at all.
3. **Transport policy** (threat model §9.2) — assumption vs guarantee for request confidentiality; interacts with the existing silent `wss`→`ws` downgrade.
4. **Approval UI availability / fail-closed semantics** (threat model §9.5; permission model §9.5) — currently a recommendation, not a requirement.
5. **Script execution v1 scope** (threat model §9.4; permission model §9.1) — recommendation must be ratified.
6. **R1 classification rule** (permission model §4 vs §5) — contradictory as written.
7. **Operation registry governance + anti-generic-wrapper rule** — absent; without it the allow-list can legally contain arbitrary execution.
8. **Script listing/export approval rules** — absent; determines whether `remote.scripts` grants content disclosure.
9. **Pre-approval v1 scope** — contradictory signals between the two documents.
10. **Quantitative control bounds** — controls named but unenforceable as specified.

---

## 8. Review Scope and Constraints Confirmation

This review examined the four named documents and cross-referenced `docs/architecture/PHONEBRIDGE_MESSAGE_FLOW_REVIEW.md` and the PhoneBridge source where a claim needed grounding. It did not modify source code, the two original design documents, Git history, or dependencies; ran no network probes or runtime tests; and printed no secrets. Verified omissions are grounded in the cited sections of the reviewed documents; recommendations and assumptions are labeled as such.

---

**Verdict restated: Ready with conditions.** The permission matrix and lifecycle are sound; the identity/authorization layering is correct; GPS separation is complete. The conditions are the P0 document revisions (§6 items 1–8) and resolution of the ten blockers (§7), after which the design is fit to enter implementation for R0/R1 reads and a narrow, reviewed R2 operation set — with script execution deferred.
