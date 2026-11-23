# Nexus Remote 0.1 — Security Test Report (Day 25)

**Date:** 2026-11-23 · **Branch:** `nexus-remote-0.1` · **Host:** Manjaro Linux, kernel 6.12, Python 3.14.7, OpenJDK 17
**Method:** automated tests over a real WebSocket server with an in-memory `nexus.db` and a
fake `/proc` tree. No destructive commands were run on the host. Test-only operations
(`test.slow`, `test.big`, `test.crash`, `test.approve`) exist only in the test registry.

## Commands and results

```bash
cd phone-bridge
python3 -m unittest discover -s tests -t .
# Ran 124 tests in ~9.2s  -> OK

cd android
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
# 26 tests, 0 failures; BUILD SUCCESSFUL; lint: 0 errors
```

The same 124 Python tests also passed inside a clean `archlinux:latest` container during
`makepkg` (`check()` step). See `docs/PACKAGING.md`.

## Coverage matrix

| # | Threat / requirement | Test(s) | Result |
|---|---|---|---|
| 1 | Malformed JSON does not kill the session | `InputSecurityTests.test_malformed_versions_and_fields` | pass |
| 2 | Unknown protocol version rejected | same; `test_rejects_unknown_and_missing_version`; Kotlin `server frames parse into typed models` | pass |
| 3 | Unknown/extra fields rejected (e.g. `"cmd"`) | `test_malformed_versions_and_fields`, fixture `request_unknown_field` | pass |
| 4 | Oversized frame / params | `test_oversized_frame`, `test_rejects_oversized_and_deep_payloads` | pass |
| 5 | Unknown operation fails closed (`shell.exec`, `process.kill`) | `test_unknown_operation_and_injection_attempts`, `test_unknown_operation_fails_closed` | pass |
| 6 | Parameter injection (`"memory; rm -rf ~"`, `"1 \|\| id"`, extra `command`) | `test_unknown_operation_and_injection_attempts`, `test_param_validation` | pass |
| 7 | Registry cannot admit a generic wrapper (free strings, lists, R4, read-perm mutations) | `test_governance_rules` | pass |
| 8 | No process is spawned by any operation | `test_no_subprocess_is_spawned` | pass |
| 9 | No cmdline/env/usernames/paths leak | `test_never_exposes_cmdline_or_environment`, `test_no_sensitive_fields` | pass |
| 10 | Legacy token session can never use remote | `test_legacy_token_session_never_gets_remote` | pass |
| 11 | Bad device credential never falls back to token | `test_bad_device_credential_never_falls_back_to_token` | pass |
| 12 | Unknown device rejected | `test_unknown_device_rejected` | pass |
| 13 | Brute force throttled (also locks correct credential from that source) | `test_auth_failures_are_rate_limited` | pass |
| 14 | Revoked device: in-flight request refused, socket closed, reconnect refused | `test_revoked_device_cannot_connect_and_live_session_is_closed` | pass |
| 15 | Revoked idle session closed by sweep | `test_idle_revoked_session_closed_by_sweep` | pass |
| 16 | Rotation invalidates old secret and session | `test_rotation_invalidates_old_credential_and_session`, `test_rotate_invalidates_old_secret` | pass |
| 17 | Expired session (max age) | `SessionAgeTests.test_session_closed_after_max_age` | pass |
| 18 | Legacy token time limit | `LegacyDisabledTests`, `test_legacy_token_expiry` | pass |
| 19 | Default deny; exact permission strings; scripts not grantable | `test_default_deny_without_grant`, `test_default_deny_and_exact_strings`, `test_scripts_permission_is_deferred` | pass |
| 20 | `remote.read` cannot invoke a `remote.execute` op | `test_read_grant_cannot_invoke_execute_operation` | pass |
| 21 | Permission removal effective mid-session | `test_permission_revoked_mid_session_takes_effect_immediately` | pass |
| 22 | `device_id` spoofing | `test_device_id_spoofing_rejected` | pass |
| 23 | Replayed request / stale or future timestamp | `test_replay_and_stale_requests` | pass |
| 24 | Approval: approved runs once; denied; expiry fails closed | `ApprovalTests.*` | pass |
| 25 | Approval bound to request + params; cannot be reused | `test_approval_is_bound_to_its_own_request_and_params`, `test_replayed_approval_cannot_be_reused`, store `test_binding_mismatch_fails` | pass |
| 26 | Rotation/revocation cancels pending approvals | `test_revoke_and_rotate_cancel_approvals` | pass |
| 27 | Timeout | `test_timeout_and_output_cap` | pass |
| 28 | Output cap | `test_timeout_and_output_cap`, `test_output_cap_truncates` | pass |
| 29 | Concurrency exhaustion; slots held until worker thread ends | `test_concurrency_exhaustion` | pass |
| 30 | Rate limit | `test_rate_limit` | pass |
| 31 | Disconnect cancels in-flight work and releases slots | `test_disconnect_cancels_and_releases` | pass |
| 32 | Slow remote request does not block heartbeat/legacy routing | `test_slow_request_does_not_block_heartbeat_or_legacy` | pass |
| 33 | Errors do not leak internals | `test_errors_do_not_leak_internal_details` | pass |
| 34 | Audit stores metadata only | `test_audit_has_metadata_only` | pass |
| 35 | Secrets stored hashed; files 0600/0700 | `test_pair_returns_credential_and_stores_only_hash`, `test_new_file_is_private_and_migrated`, `test_written_config_round_trips_and_is_private` | pass |
| 36 | Dashboard: no remote routes; `/api/locate` POST-only; device name escaped (XSS) | `DashboardIsolationTests` | pass |
| 37 | Placeholder token `change-me` rejected | `test_placeholder_token_rejected`, `test_example_config_no_longer_loads_with_placeholder` | pass |
| 38 | PID reuse detected | `test_pid_reuse_detected_by_start_ticks`, `test_pid_reuse_during_inspection` | pass |
| 39 | Android: no offline queueing, no automatic re-send, disconnect fails in-flight | `RemoteRuntimeTest` (6 tests) | pass |
| 40 | Android: out-of-order / duplicate / foreign responses ignored | `RemoteRequestTrackerTest` (10 tests) | pass |
| 41 | Android: device hello omits shared token; credential redacted in logs | `MessageProtocolTest` | pass |

## Manual verification (real CLI + server, scratch data dir)

`phone-bridge serve` on 127.0.0.1 with a fresh config, then a paired client:
`system.status` / `process.list` / `process.inspect` → `completed`; `shell.exec` →
`rejected/unknown_operation`; legacy token → `rejected/not_authorized`; after
`remote revoke` → reconnect refused (`authentication failed`); `remote audit` shows five
metadata rows; `nexus.db` and `config.toml` mode `600`.

Hardening options from the systemd unit were exercised with
`systemd-run --user -p NoNewPrivileges=yes -p RestrictNamespaces=yes …` and the agent ran normally.

## Open items (none critical)

| Severity | Item | Disposition |
|---|---|---|
| Medium | WebSocket is not TLS; confidentiality relies on Tailscale | Documented limitation; TLS/pinning on roadmap |
| Medium | Device secret is a bearer secret sent per connection | Planned: public-key challenge (see credential plan) |
| Low | Operation timeout cannot kill the worker thread (Python) | Slot is held until the thread ends, so concurrency stays bounded; all 0.1 ops are short `/proc` reads |
| Low | Filesystem sandbox options (`ProtectSystem`, `PrivateTmp`) not enabled by default in the user unit | Not available to all unprivileged user managers; documented, opt-in |
| Low | No instrumented (on-device) Android UI tests | JVM unit tests + Lint only; tester guide covers manual checks |
| Info | Map dashboard is still unauthenticated on 127.0.0.1 (other local users could read GPS) | Pre-existing; out of 0.1 scope; single-user host assumption documented |

No unresolved critical issue.
