# Scripts Policy for Nexus Remote 0.1 (Day 24)

**Decision: user scripts are DEFERRED. `remote.scripts` is disabled in 0.1.**

## Why

The Day 24 plan allows scripts only if every control below can be implemented reliably.
For this release several cannot:

| Required control | 0.1 status |
|---|---|
| Admin-created script IDs, trusted path, owner, content hash, permitted params | Not built |
| Re-approval when content/hash changes | Not built |
| Execute as logged-in user, minimal env, no shell interpolation | Possible, not built |
| Timeout + output cap | Exists in the dispatcher |
| Sandbox / network-egress restriction for the child process (Day 4 G10) | **No mechanism chosen.** Unprivileged systemd user managers often cannot provide mount/network namespaces (verified: `PrivateTmp`/`ProtectSystem` are left commented in the unit) |
| Per-run Linux-side approval | Exists (approval engine, tested) |

Without a sandbox, an approved script could exfiltrate user data over the network, which
the threat model treats as unacceptable. Shipping half of these controls would create the
"generic wrapper" the Day 4 review warned against.

## What is enforced in code

- `remote.scripts` is a known permission but **not grantable**:
  `RemoteStore.grant()` / `pair --grant remote.scripts` raise
  `remote.scripts is deferred in Nexus Remote 0.1` (`test_scripts_permission_is_deferred`).
- The operation registry rejects any free-form string parameter, list parameter, R4 risk,
  or mutating operation under `remote.read` (`test_governance_rules`), so a script runner
  cannot be added without changing those checks under review.
- No operation accepts a script body, executable, path or command line; there is no
  generic command field anywhere in the protocol (`test_unknown_operation_and_injection_attempts`).

## Prerequisites to revisit (proposed for the next 30 days)

1. Choose the sandbox: `systemd-run --user --scope` with `IPAddressDeny=any`,
   `PrivateNetwork`, `ProtectSystem=strict` where available, or bubblewrap; fail closed if
   unavailable.
2. Script store in `nexus.db`: id, absolute path under a 0700 directory owned by the user,
   SHA-256, enumerated parameters, timeout, output cap, approval required = always.
3. Execute with `subprocess.run([...], shell=False, env=minimal, cwd=fixed)`; re-hash
   immediately before exec (TOCTOU) and refuse on mismatch.
4. Tests: path escape, symlink swap, changed hash, timeout, output truncation, network denial.
