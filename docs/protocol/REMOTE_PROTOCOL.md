# Nexus Remote Wire Protocol — v1

Transport: the existing PhoneBridge WebSocket (JSON text frames). Remote frames have
`type` starting with `remote.` and always carry `"v": 1`. Implementations:
`phone-bridge/remote_protocol.py` and `android/.../remote/RemoteProtocol.kt`.
Fixtures shared by both test suites: `protocol/fixtures/remote/*.json`.

## Handshake changes

`hello` (phone → Linux) gains an optional per-device credential:

```json
{"type":"hello","device":"pixel-9","version":1,"platform":"android",
 "device_id":"dev_0123456789abcdef","device_secret":"<43+ url-safe chars>"}
```

- With `device_id`, `token` is not required and is ignored. A failed device credential
  **never** falls back to the token.
- Without `device_id`, the legacy `token` path is used (if `legacy_token_auth` allows it).

`hello_ack` adds `"auth": "device" | "token"` and, for device auth, `"device_id"`.
Immediately after `hello_ack` the server sends `remote.capabilities` (old phone builds
ignore it).

## Frames

### `remote.capabilities` (Linux → phone)

| Field | Type | Notes |
|---|---|---|
| `available` | bool | `false` for legacy-token sessions |
| `server` | string | configured `device_name` |
| `device_id` | string | authenticated principal, `""` if none |
| `permissions` | string[] | currently granted |
| `operations` | object[] | `{id, permission, risk, requires_approval, description, timeout, max_output_bytes, params}` usable by this device |
| `reason` | string | present when `available` is false |

### `remote.request` (phone → Linux)

| Field | Rule |
|---|---|
| `v` | integer `1` exactly |
| `request_id` | `^[A-Za-z0-9_-]{8,64}$`, unique per device for 10 min |
| `operation` | `^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$` |
| `params` | JSON object, ≤ 4 KiB canonical, depth ≤ 3, finite numbers |
| `ts` | sender Unix time (s); must be within ±300 s of the Linux clock |
| `device_id` | optional; if present must equal the authenticated device |

No other fields are allowed. The whole frame must be ≤ 16 KiB.

### `remote.event` (Linux → phone)

`{request_id, state, ts}` with `state` ∈ `accepted`, `awaiting_approval`, `running`.
Informational; the phone ignores out-of-order states.

### `remote.approval_required` / `remote.approval_result` (Linux → phone)

`{request_id, approval_id, operation, expires_at, approve_on:"linux"}` and
`{request_id, approval_id, outcome}` with `outcome` ∈ `approved`, `denied`, `expired`.
Approval is performed on Linux; the phone cannot approve.

### `remote.response` (Linux → phone, exactly one per request)

| Field | Notes |
|---|---|
| `status` | `completed`, `failed`, `rejected`, `expired` |
| `result` | only when `completed`; bounded by the operation output cap |
| `error` | `{code, message}` otherwise; message ≤ 256 chars, no internals |
| `truncated` | `true` if a list result was cut to fit its cap |
| `started_at`, `finished_at` | Unix seconds (`started_at` null if never executed) |

## Error codes

| Code | Status | Meaning |
|---|---|---|
| `not_authorized` | rejected | legacy token session |
| `invalid_request` | rejected | schema violation, wrong `type`, `device_id` mismatch |
| `unsupported_version` | rejected | `v` ≠ 1 |
| `payload_too_large` | rejected | frame > 16 KiB or params > 4 KiB |
| `expired` | expired | `ts` outside ±300 s |
| `duplicate_request` | rejected | `request_id` replayed |
| `rate_limited` | rejected | > 20 requests / 10 s per device |
| `unknown_operation` / `operation_disabled` | rejected | not in the registry |
| `device_revoked` | rejected | revoked or rotated mid-session (socket then closed, code 4001) |
| `permission_denied` | rejected | grant missing |
| `invalid_params` | rejected | operation schema violation |
| `busy` | rejected | > 4 in flight per device or > 8 globally |
| `approval_denied` / `approval_expired` | rejected | Linux-side approval outcome |
| `timeout` | failed | operation exceeded its timeout |
| `output_too_large` | failed | result exceeded the cap |
| `not_found`, `permission_denied`, `process_changed`, `unavailable` | failed | operation-level errors |
| `operation_error` | failed | unexpected exception (details only in the Linux log) |
| `remote_disabled` | rejected | `remote_enabled = false` |

## WebSocket close codes

`4001` device revoked/rotated · `4002` session exceeded `remote_session_max_age`.

## Versioning

A breaking change bumps `v`. Both sides reject unknown versions explicitly
(`unsupported_version`); the phone shows "update the app or the agent".
