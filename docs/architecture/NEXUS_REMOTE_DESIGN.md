# Nexus Remote 0.1 — Technical Design

**Status:** Approved for implementation (Day 5). Implemented Days 6–24 on branch `nexus-remote-0.1`.
**Inputs:** `NEXUS_BASELINE.md`, `PHONEBRIDGE_MESSAGE_FLOW.md`, `CURRENT_TRUST_BOUNDARIES.md`,
`NEXUS_REMOTE_THREAT_MODEL.md`, `REMOTE_PERMISSION_MODEL.md`, `NEXUS_DAY4_SECURITY_REVIEW.md`.

## 1. Goal and non-goals

A paired Android device can, over the existing PhoneBridge WebSocket, ask the Linux
agent for a **bounded, read-only** set of diagnostics: `system.status`,
`process.list`, `process.inspect`.

Non-goals for 0.1 (explicitly out of scope): remote shell, PTY, arbitrary file access,
root/sudo, process signalling/killing, user scripts, Windows/macOS, multi-user Linux hosts,
exposure of any `remote.*` capability over HTTP/the map dashboard.

## 2. Decisions that close the Day 4 review blockers

| # | Day 4 open decision | Decision for 0.1 |
|---|---|---|
| 1 | Device credential + pairing ceremony | Linux operator runs `phone-bridge remote pair <name>`. The agent creates a device principal `dev_<16 hex>` and a 256-bit random secret, stores **only SHA-256(secret)** in `nexus.db` (mode 0600), and prints the credential `<device_id>.<secret>` **once**. The user pastes it into the Android app, which encrypts it with an Android Keystore AES-GCM key. Hello frames carry `device_id` + `device_secret`; verification is a constant-time hash compare. |
| 2 | Broker execution context / sandbox | The agent runs as the logged-in user (systemd **user** unit, `NoNewPrivileges`). All 0.1 operations are in-process reads of `/proc` and `statvfs`; **no read operation spawns a process**, so there is no child process to sandbox and no egress path. Sandboxing is a prerequisite for any future execute operation. |
| 3 | Legacy shared token coexistence | Legacy token sessions keep working for notifications/calls/GPS but are **never** granted `remote.*`; a `remote.*` frame on a legacy session is rejected (`not_authorized`). Optional `legacy_token_expires` (ISO date) makes the legacy path time-limited. If a client presents device credentials and they fail, the server rejects — it never falls back to the token (downgrade resistance). |
| 4 | Approval UI availability | Approval happens **on Linux** (`phone-bridge remote approve <id>`), with a desktop notification as the prompt. If nobody approves before expiry the request is rejected (fail-closed). The phone only displays "waiting for approval". |
| 5 | Scripts in v1 | **Deferred.** `remote.scripts` cannot be granted in 0.1 (CLI refuses). No script store exists. |
| 6 | R1 classification | All reads are `remote.read`. Read operations must not spawn processes. |
| 7 | Registry governance / anti-wrapper rule | The registry is a fixed Python table in `remote_registry.py`. No operation may accept an executable, command line, path, script body or unbounded argument vector. Every parameter has a closed type and range. Adding an operation requires a code change + review + a test. |
| 8 | Script listing/export | Not applicable (deferred with scripts). |
| — | Pre-approvals | **Forbidden** in 0.1. Every approval is single-use and bound to one request. |
| — | Dashboard exposure | `remote.*` is reachable only via the authenticated WebSocket. The HTTP dashboard has no remote routes; a test asserts this. |

## 3. Components

```
Android                                   Linux agent (phone-bridge / nexus)
-------                                   ----------------------------------
RemoteActivity ──> RemoteController        server.py  BridgeServer
   │                  │                       │ handshake: legacy token | device credential
   │                  ▼                       │ remote.* frames ─────────────┐
   │          RemoteRequestTracker            ▼                              ▼
   │        (request_id correlation)     remote_protocol.py         remote_dispatcher.py
   │                  │                  (strict schemas)    replay cache · rate limit · concurrency
   ▼                  ▼                                       permission check · approval · timeout
ConnectionManager (existing socket) ◀──────────────────────▶ output cap · audit
   remote frames sent directly, never queued                      │
                                                     remote_registry.py (fixed allow-list)
                                                     remote_ops.py (/proc readers)
                                                     remote_store.py (nexus.db: devices,
                                                       grants, approvals, audit, migrations)
```

Location tracking (`storage.py`, `locations.db`) is untouched; remote state lives in a
separate database `nexus.db` so a migration bug cannot damage GPS history.

## 4. Wire protocol (v1)

All remote frames carry `"v": 1`. Unknown `v`, unknown fields, oversize frames (>16 KiB)
and missing identifiers are rejected. Full schema: `docs/protocol/REMOTE_PROTOCOL.md`;
shared fixtures in `protocol/fixtures/remote/` are tested by both Python and Kotlin.

| Type | Direction | Purpose |
|---|---|---|
| `remote.capabilities` | L→A | After `hello_ack`: whether remote is available, device id, granted permissions, usable operations |
| `remote.request` | A→L | `request_id`, `operation`, `params`, `ts` |
| `remote.event` | L→A | Lifecycle: `accepted`, `awaiting_approval`, `running` |
| `remote.approval_required` | L→A | Approval id + expiry (informational) |
| `remote.approval_result` | L→A | `approved` / `denied` / `expired` (informational) |
| `remote.response` | L→A | Terminal: `completed` / `failed` / `rejected` / `expired` with bounded `result` or `error` |

## 5. Request pipeline (server)

1. Session must be device-authenticated → else `rejected/not_authorized`.
2. Strict schema validation → `rejected/invalid_request`.
3. `ts` within ±300 s → else `expired`; `request_id` not seen in the last 600 s → else `rejected/duplicate_request`.
4. Per-device rate limit (20 req / 10 s) → `rejected/rate_limited`.
5. Registry lookup → `rejected/unknown_operation` / `operation_disabled` (fail closed).
6. Fresh device lookup: revoked or rotated → `rejected/device_revoked` and the socket is closed.
7. Fresh permission lookup (default deny) → `rejected/permission_denied`.
8. Parameter validation against the operation schema → `rejected/invalid_params`.
9. Concurrency (4 per device, 8 global) → `rejected/busy`.
10. If the operation requires approval: create an approval bound to
    (request_id, device_id, operation, SHA-256 of canonical params, expiry 60 s) and wait.
11. Execute in a worker thread with the operation timeout → `failed/timeout`.
12. Enforce the operation output cap; lists truncate with `truncated: true`; anything still
    over cap → `failed/output_too_large`.
13. Write audit metadata (never params values, results, notifications or coordinates).

Each request runs as its own task, so a slow or failing request cannot block heartbeats or
legacy notification/GPS routing on the same connection. On disconnect, in-flight tasks are
cancelled.

## 6. Operations (0.1)

| ID | Permission | Risk | Approval | Timeout | Output cap | Params |
|---|---|---|---|---|---|---|
| `system.status` | `remote.read` | R1 | no | 3 s | 16 KiB | none |
| `process.list` | `remote.read` | R1 | no | 5 s | 64 KiB | `limit` 1–200, `offset` 0–100000, `sort_by` ∈ {memory, cpu_time, pid, name}, `order` ∈ {asc, desc}, `own_only` bool |
| `process.inspect` | `remote.read` | R1 | no | 3 s | 8 KiB | `pid` 1–4194304 (required), `start_ticks` int (optional, PID-reuse guard) |

**Closed default field set** (closes Day 4 G14): pid, ppid, name (`comm`, ≤64 printable
chars), state, `own` (bool: same UID as agent), threads, nice, RSS/VMS, memory %, CPU time,
average CPU %, start time, runtime. **Never returned:** cmdline, environment, cwd, open
files, usernames, UIDs of other users, mount sources, hostname (the configured
`device_name` is used instead). Disks are reported by label (`root`, `home`), not path.

## 7. Identity lifecycle

- `remote pair <name> [--grant remote.read]` — new principal; no permission unless granted.
- `remote grant|ungrant <id> <perm>` — exact strings only; `remote.scripts` refused in 0.1.
- `remote rotate <id>` — new secret; old secret invalid immediately; live sessions closed.
- `remote revoke <id>` / `remote revoke-all` — principal disabled; live sessions closed
  within 5 s by the server's revocation sweep and on the next request at the latest;
  pending approvals for that device are cancelled. Re-pairing creates a new principal with
  no inherited grants.
- Sessions are capped at `remote_session_max_age` (default 12 h), then closed so the phone
  re-authenticates.

## 8. Android

- Remote screen (`RemoteActivity`) reached from the main screen; legacy screen unchanged.
- Remote frames go through the existing socket. They are **never queued** while offline and
  **never retried automatically** with the same request id; polling issues new read requests
  with bounded backoff only while the screen is visible.
- `RemoteRequestTracker` keys state by locally generated `request_id`; unknown, duplicate or
  late responses are ignored; on disconnect all non-terminal requests become `failed/disconnected`.
- No mutating controls exist in the 0.1 UI.

## 9. Compatibility

- Old Android builds: ignore `remote.capabilities` (unknown frame) and keep working.
- New Android against an old server: no `remote.capabilities` arrives → Remote screen shows
  "not supported by this server".
- Existing configs keep working. `token = "change-me"` (the shipped placeholder) is now
  rejected at startup with a clear message — this is an intentional, security-motivated break.

## 10. Testing strategy

See `docs/NEXUS_REGRESSION_MATRIX.md`. Python: `unittest` (no new runtime deps). Android:
JVM unit tests for protocol models, fixtures and the request tracker.
