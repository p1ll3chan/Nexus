# Remote Permission Model

**Status:** Proposed policy vocabulary; not implemented.<br>
**Applies to:** Android-initiated operations against a paired Linux user session.<br>
**Companion:** `NEXUS_REMOTE_THREAT_MODEL.md`.

## 1. Policy Requirements

- Permissions default to **denied** and are granted by a local Linux user to a specific authenticated device principal.
- Permission strings are exact, case-sensitive identifiers. Unknown strings, wildcard grants, aliases, and implicit inheritance are denied.
- A permission grant is not the same as approval of a specific operation. Operation risk determines whether per-request local approval is also required.
- Grants are independently revocable and have an explicit lifetime/scope. Re-pairing or replacing a device credential must not silently inherit grants.
- Tailscale membership, a shared network token, a `device` display name, Android location permission, or notification access does not imply any `remote.*` permission.
- The initial policy must not support root, `sudo`, arbitrary shell, arbitrary filesystem access, or silent unattended script execution.

## 2. Permission Strings

### `remote.read`

Allows read-only access to the explicitly enumerated remote diagnostics exposed by the Linux broker.

**May include when specifically enabled:**

- A bounded system status snapshot: OS/app version, uptime, CPU/memory/disk utilization and service health.
- Process metadata approved by the local policy: process name, PID, owner, and optionally executable basename/resource usage.

**Does not include:**

- Arbitrary file reads, directory traversal, file contents, secrets, environment variables, open-file contents, shell access, or command execution.
- Full process command-line arguments, working directories, environment, or other high-sensitivity fields unless separately reviewed and individually approved.
- Notifications, notification history, call details, GPS/current location, or GPS history.
- Reading saved-script contents; that is governed by `remote.scripts`.

### `remote.execute`

Allows invocation of the Linux broker's **registered operation IDs** with validated typed arguments. It does not mean “run an arbitrary command string.”

**May include when specifically enabled:**

- Named, allow-listed operations with a documented effect and bounded inputs/output.
- Read-only diagnostic operations that are nevertheless implemented as a process/job; these still count as execution for authorization.

**Does not include:**

- Arbitrary shell, PTY, client-selected executable paths, command-string concatenation, `sudo`, root, setuid helpers, other-user sessions, arbitrary filesystem operations, or permission/policy changes.
- Running a saved script unless `remote.scripts` is also granted and the script-specific approval requirements below are met.
- GPS or notification access.

Every `remote.execute` operation requires local approval per request in the initial policy, unless a future reviewed design adds a narrow, user-created pre-approval for a specific operation ID and exact parameter bounds. No blanket “approve all commands” grant is allowed.

### `remote.scripts`

Allows management of a broker-owned saved-script collection only. It is separate from permission to execute those scripts.

**May include when specifically enabled:**

- List script identifiers and safe metadata (name, owner, version, digest, last reviewed time).
- Create, edit, import, export, or delete script content only within a dedicated private script store and only through validated broker operations.

**Does not include:**

- Running a script. Running requires **both** `remote.scripts` and `remote.execute`.
- Arbitrary reads/writes outside the script store, symlink traversal, changing the broker's policy, or accessing GPS/notification data.

Create/edit/import/delete operations require explicit local approval. Every run requires a new local approval bound to the exact immutable script digest, target user, arguments, and requested effect. Any content or argument change invalidates the approval. The initial release recommendation is to keep script execution disabled until sandboxing and a review UX are implemented.

## 3. Explicit GPS and Notification Separation

`remote.read`, `remote.execute`, and `remote.scripts` grant **no access** to GPS or notification data.

- Existing GPS sharing remains controlled by its own Android OS location permission and PhoneBridge location setting. Those controls authorize the existing phone-to-Linux location-sharing feature; they do not grant a remote process-read or execution right.
- If on-demand GPS retrieval or GPS history access is exposed as a future remote operation, it must have a separate location-specific permission and consent path (for example, a separately designed `location.read` capability). It must not be implemented as a side effect of any `remote.*` grant.
- Notification listener access and notification forwarding settings remain separate. Remote-control permission does not grant access to notification content or notification history.
- Remote audit records must not contain GPS coordinates, notification bodies, or caller details by default.

## 4. Operation Risk Levels and Approval Rules

Risk is assigned to the **operation and its data**, not merely to the message type or requested permission. A single permission may cover multiple risk tiers, each with different approval rules.

| Level | Definition and examples | Required permission | Approval rule |
|---|---|---|---|
| **R0 — Routine, minimized read** | Fixed low-sensitivity health/status fields (app version, uptime, bounded CPU/memory/disk utilization). No process identity, command output, or user file content. | `remote.read` | Local user must explicitly grant `remote.read` to the paired device. After that grant, no per-query prompt is needed for the exact R0 allow-list. User can revoke at any time. |
| **R1 — Sensitive read** | Process metadata; details that reveal which applications or user tasks are active; bounded diagnostic output that may contain paths or identifiers. | `remote.read` for metadata; `remote.execute` if a registered process/job is launched to produce it. | Require a local per-request approval for process listings and sensitive diagnostics. Show requested fields and target session. Do not expose command lines or environment by default. |
| **R2 — Constrained execution** | A named, reviewed operation with typed arguments and limited side effects, such as requesting a specific user service status/restart if that operation is explicitly supported. | `remote.execute` | Require local approval on every request in the initial policy. Show operation ID, target, canonical arguments, risk, and whether it changes state. The operation must be allow-listed and bounded. |
| **R3 — User-data or script mutation** | Write/replace/delete user data; create/edit/import/delete a script; run a saved script; modify persistent user-session configuration. | `remote.execute`; script operations also require `remote.scripts`. | Require explicit local per-request approval. For script run, display/review the exact content digest and arguments; run only the approved immutable version. No approval may be reused after any input/content change. |
| **R4 — Privileged, broad, or difficult-to-reverse** | Root/`sudo`, account/security changes, arbitrary shell/PTY, unrestricted file access, system-wide service/network/firewall changes, disabling controls, or bulk destructive operations. | **Not granted by any of these strings.** | Deny in the initial policy. A future proposal requires a separate threat model, explicit founder approval, local interactive re-authentication/physical presence, and narrowly scoped controls; a remote `remote.execute` grant alone is never sufficient. |

### Approval mechanics

- Pairing and permission grant require a local Linux-side confirmation that identifies the Android device and the requested permission strings.
- Per-operation approval is generated on Linux, bound to a unique request ID and canonical request digest, expires quickly, and is single-use.
- The approval UI must show the operation in human-readable form, target Linux account/session, risk tier, arguments, working directory if relevant, and expected side effects. Script approval includes a digest and readable content review.
- Denial, timeout, missing UI/approver, policy-store error, or unknown risk classification means **deny**; no headless “best effort” execution.
- Approval cannot be supplied solely by the Android request, an old token, a Tailnet ACL, a script comment/label, or an earlier approval for different content.

## 5. Permission Combination and Decision Matrix

| Requested action | Minimum permission set | Extra rule |
|---|---|---|
| Read allow-listed R0 system status | `remote.read` | Permission grant required; no prompt per request after grant. |
| Read process metadata / sensitive diagnostics | `remote.read` | Local approval per request; omit command-line/env fields by default. |
| Invoke registered operation | `remote.execute` | Local approval per request; operation ID and arguments must validate. |
| Create/edit/import/delete saved script | `remote.scripts` | Local approval per change; confined script store and version/digest recorded. |
| Run a saved script | `remote.scripts` **and** `remote.execute` | Local approval for each run bound to exact digest and args; risk R3. |
| Read or request current GPS / history | **Neither `remote.read`, `remote.execute`, nor `remote.scripts`** | Separate location-specific permission/consent design and existing OS/location controls. |
| Read notifications/calls | **Neither `remote.read`, `remote.execute`, nor `remote.scripts`** | Separate notification/call consent and policy; no implicit Remote grant. |
| Root/admin/arbitrary shell | **No combination is sufficient** | Deny under this model. |

Permissions are conjunctive where indicated. One permission never implies another, and granting all three still does not grant GPS, notifications, root, or arbitrary filesystem access.

## 6. Identity, Grant, and Revocation Semantics

The permission principal must be a stable authenticated device identity, not the current `hello.device` string or a shared token. Each grant should record:

- Unique device/principal identifier and credential key ID (never the raw credential).
- Exact permission strings, scope, local Linux account/session, grant time, expiry, and granting user.
- Any operation pre-approvals as a distinct record with operation ID, argument constraints, risk ceiling, and expiry.
- Revocation state and audit metadata.

Revoking a device invalidates future requests immediately, terminates its session, clears outstanding approvals, and cancels/terminates in-flight jobs according to a documented safe policy. Re-pairing creates a new principal and does not inherit previous permissions. Unknown devices, duplicate/stale request IDs, revoked keys, invalid permission strings, and unavailable policy state fail closed.

## 7. Execution and Output Constraints

For any future `remote.execute` implementation:

1. Dispatch by a server-defined operation ID, not a client-provided shell string or executable path.
2. Use argument arrays and strict per-operation schemas; never interpolate untrusted input into a shell command.
3. Run under the intended unprivileged Linux UID with no `sudo`, root, ambient capabilities, or access to another user's session.
4. Fix and validate working directory, environment, file paths, and symlink behavior. No arbitrary path access.
5. Enforce execution timeout, CPU/memory/process limits, output byte limits, concurrent job limits, cancellation, and cleanup.
6. Treat stdout/stderr as sensitive output: return only to the requesting still-authorized device, truncate safely, and avoid logging content by default.
7. Persist scripts in a private broker-owned directory; do not execute writable content after approval unless the hash is unchanged. Review/import/execute each version as separate operations.
8. Do not connect execution output to notification content or GPS storage by default.

These are requirements for a future implementation; the current PhoneBridge code has no remote executor or script store.

## 8. Audit Requirements

Record enough metadata to investigate but avoid creating a second sensitive-data store. Suggested fields:

- Timestamp, request ID, paired device principal, Linux user/session, permission checked, operation ID, risk tier.
- Authorization decision, approval actor/decision, request freshness result, script digest if applicable.
- Start/end time, exit/result class, timeout/cancellation, output-truncated flag.

Do **not** log raw credentials, notification bodies, GPS coordinates, full command output, environment variables, or script content by default. Audit storage must have restrictive permissions, bounded retention, and a documented deletion path.

## 9. Open Product/Security Decisions

1. Is the first release limited to R0/R1 reads and a small set of R2 operations? Recommendation: yes; postpone saved-script execution.
2. Which exact process metadata fields may be read, and are executable paths/arguments excluded?
3. Which Linux session does the broker control: a dedicated user service or the interactive user's session? What files/services are explicitly in bounds?
4. Which R2 actions are allowed, and which state changes require approval on every invocation?
5. Is the host always expected to have a local approval UI? Recommendation: operations requiring approval fail closed when no local approver is available.
6. What separate permission and consent flow governs on-demand GPS? It must not be `remote.read`.
7. How are per-device credentials provisioned, rotated, revoked, and migrated away from the existing shared token?

---

**Summary:** `remote.read`, `remote.execute`, and `remote.scripts` are separate proposed capabilities. Saved-script execution requires both script and execution permission plus per-run approval. GPS and notification access remain entirely separate. R4 privileged or arbitrary-shell operations are denied by this model.
