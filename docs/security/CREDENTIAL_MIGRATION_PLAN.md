# Credential Migration Plan: shared token → per-device identity (Day 20)

**Status:** Approved and implemented (Day 21). Tests: `tests/test_remote_store.py`,
`tests/test_remote_server.py` (`AuthenticationSecurityTests`, `LegacyDisabledTests`,
`SessionAgeTests`), Android `MessageProtocolTest`.

## Starting point (Day 3 audit)

One shared `token` in `config.toml` authenticates every phone; `hello.device` is a
self-asserted display name; the shipped example token was `change-me`; the phone stored the
token in plain SharedPreferences.

## Target

| Property | Design |
|---|---|
| Identity | `dev_<16 hex>` principal created on Linux by `phone-bridge remote pair`; display name is metadata only |
| Secret | 256-bit `secrets.token_urlsafe(32)`, shown once as `dev_<id>.<secret>` |
| Linux storage | SHA-256(secret) in `nexus.db` (0600, dir 0700); constant-time compare; dummy compare for unknown IDs |
| Android storage | AES-256-GCM ciphertext in SharedPreferences, key non-exportable in Android Keystore; `allowBackup=false` |
| Transport | Inside Tailscale (WireGuard). The secret is sent once per connection in `hello`; no TLS on the WebSocket in 0.1 (documented limitation) |
| Session binding | Session stores the authenticated secret hash; every remote request and a 5 s sweep re-check it against the store |
| Session lifetime | `remote_session_max_age` (12 h) then close 4002 → phone reconnects |
| Rotation | `remote rotate <id>`: new secret, old invalid immediately, live session closed (4001), pending approvals cancelled. No grace period: the operator re-enters the new credential on the phone |
| Revocation | `remote revoke <id>` / `revoke-all`: grants deleted, approvals cancelled, sessions closed ≤ 5 s, future auth rejected |
| Re-pairing | Always a new principal; grants are never inherited |
| Failed-auth throttling | Existing per-source limiter (10 failures / 60 s) also covers device credentials |

## Compatibility behaviour

1. **Existing installs keep working.** Phones with only the shared token still deliver
   notifications, calls and GPS. `hello_ack.auth = "token"`.
2. **Legacy sessions can never use Remote.** `remote.capabilities.available = false`;
   every `remote.request` → `not_authorized`. No operator action can grant `remote.*` to a
   token session (there is no principal to grant to).
3. **No downgrade.** If `device_id` is present and wrong, the connection is rejected even if
   a valid token is also present.
4. **Time-limited legacy path.** `legacy_token_expires = "YYYY-MM-DD"` or
   `legacy_token_auth = false` turns the shared token off (`legacy_auth_disabled`).
5. **Placeholder fix.** `token = "change-me"` (and variants) now fails at startup with a
   clear message; the installer always generates a random token.

## Rollout for an existing user

1. Update the agent (`./scripts/install.sh`), update the app.
2. `phone-bridge remote pair <phone> --grant remote.read`; paste the credential in the app.
3. Confirm `phone-bridge remote devices` shows a recent `last_seen`.
4. Set `legacy_token_auth = false`, restart: `systemctl --user restart phone-bridge`.

## Secret hygiene

- Credentials are printed once to the operator's terminal; they are never logged by the
  agent, never stored in plaintext on either side, and redacted in Kotlin `toString()`.
- Raw secrets are not migrated anywhere: the legacy token is unrelated to device secrets.
- `config.toml` is written 0600; the installer tightens an existing config to 0600.

## Deferred

- Public-key device credentials (sign a server nonce) to avoid sending a bearer secret.
- QR-code pairing.
- TLS/certificate pinning on the WebSocket for use outside Tailscale.
