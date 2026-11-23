# Nexus — 30-Day Sprint Retrospective (Day 30)

**Scope note:** Days 1–4 were completed on separate days. Days 5–30 were compressed into a
single session on 2026-11-23 to meet the FOSS release deadline. This retrospective uses only
evidence from the repository; there is no external user feedback yet, and none is invented.

## Plan vs actual

| Phase | Planned result | Actual | Evidence |
|---|---|---|---|
| 1. Audit & boundaries (1–5) | Map, data flow, threat model, baseline, design | Done | `NEXUS_BASELINE.md`, message flow, trust boundaries, threat + permission model, `NEXUS_REMOTE_DESIGN.md`, regression matrix |
| 2. Remote backend (6–12) | Protocol, registry, status/process APIs, dispatcher, CLI, audit | Done | `remote_*.py`, 14+24+22+33 tests |
| 3. Android integration (13–19) | Remote screen, request flow, safe UX | Done, **not exercised on a physical phone** | `remote/*.kt`, 26 JVM tests, Lint 0 errors |
| 4. Identity & policy (20–24) | Per-device creds, permissions, approvals, revocation; scripts safe or deferred | Done; scripts **deferred** by design | `CREDENTIAL_MIGRATION_PLAN.md`, `SCRIPTS_POLICY.md` |
| 5. Regression & alpha (25–30) | Security/regression evidence, packaging, docs, release candidate, handoff | Done; release **not published** (needs owner approval) | security report, regression report, `PACKAGING.md`, checklist, tester kit |

Definition of done: all 10 items met (see `docs/release/ALPHA_CHECKLIST.md`).

## Delivered

- Read-only Nexus Remote: 3 operations, no process spawning, closed field set.
- Per-device identity with hashed secrets, Keystore-encrypted phone storage, revoke/rotate/revoke-all,
  session max age, revocation sweep ≤ 5 s.
- Server-side default-deny permissions; anti-wrapper registry governance.
- Approval engine (bound, single-use, fail-closed) ready for future mutating operations.
- Metadata-only audit with retention; `nexus.db` separate from GPS history.
- Fixed 3 pre-existing security findings from Days 3–4: placeholder token, dashboard XSS,
  GET-triggerable locate.
- Packaging: installer/uninstaller (Arch, Ubuntu), Arch PKGBUILD, hardened user unit.
- Tests: Python 24 → 124, Android 0 → 26.

## Issues found during the sprint (and fixed)

| Found by | Issue | Fix |
|---|---|---|
| Legacy integration test | Server opened `nexus.db` under the real `$HOME` during tests | Tests now run with a throwaway `HOME` |
| Fixture test | JSON integer vs float `ts` encoding difference | Compare numerically (both valid) |
| Ubuntu container | `uninstall.sh` not executable | `chmod +x` |
| Ubuntu 22.04 container | README claimed 22.04 support; it ships Python 3.10 | Installer refuses clearly; README documents `PYTHON=python3.11` |
| Arch container | `package()` failed: duplicate `nexus` link | Rely on the wheel's entry point |
| Manual test | `tr` range bug would abort the installer under `pipefail` | Fixed and re-tested |
| Review | Possible missed response in `RemoteRuntime.call` race | Subscribe before sending; test added |

## Known issues / deferred work

- No on-device Android testing yet (highest priority before tagging).
- WebSocket without TLS; bearer device secret.
- User scripts and mutating operations deferred.
- Map dashboard unauthenticated on loopback (pre-existing, single-user assumption).
- Python cannot hard-kill a timed-out worker thread (bounded by concurrency slots).

## Proposed next 30 days (evidence-based)

| Week | Goal | Why (evidence) |
|---|---|---|
| 1 | On-device test pass + instrumented UI tests (Espresso) for the Remote screen; signed APK; tag `v0.3.0a1` | Only unverified area in this release |
| 1 | QR-code pairing | Manual credential copy is the most error-prone step in the tester guide |
| 2 | Public-key device credentials (challenge-response); optional TLS with pinning | Top two Medium items in the security report |
| 2 | Dashboard authentication or Unix-socket binding | Pre-existing Info item; multi-user hosts |
| 3 | Choose and test a sandbox (systemd-run scope / bubblewrap with network deny) | Prerequisite for any execute/script operation (`SCRIPTS_POLICY.md`) |
| 3–4 | First reviewed R2 operation (e.g. send SIGTERM to own process) behind Linux approval | Approval engine exists and is tested; needs a sandbox-free but reversible first use |
| 4 | Triage alpha tester feedback; adjust roadmap | No user data yet; do not over-plan |
