# Nexus Remote 0.1 Alpha — Release Checklist (Day 29)

**Decision: release candidate is READY FOR OWNER REVIEW. Not tagged, not published.**
Publishing (tag, GitHub release, APK upload) needs the owner's explicit approval.

| Gate | Evidence | Status |
|---|---|---|
| Licence | `LICENSE` (Apache-2.0, canonical SPDX text), `NOTICE`, `pyproject` `license = "Apache-2.0"`, PKGBUILD `license=('Apache-2.0')` | ✅ |
| Third-party licences | All runtime deps permissive (BSD-3, MIT, Apache-2.0, BSD-2); listed in `NOTICE` | ✅ |
| Secret scan | Pattern scan of all tracked + new files: no keys, tokens, device credentials; only `10.0.0.1` in tests | ✅ |
| No artifacts committed | No `.apk/.aab/.db/.jks/.keystore/config.toml/local.properties` tracked; `.gitignore` covers them | ✅ |
| Versions | Agent `0.3.0a1`; Android `0.3.0-alpha1` / versionCode 3; PKGBUILD `0.3.0a1` | ✅ |
| Changelog | `CHANGELOG.md` | ✅ |
| Python tests | 124 / 124 OK (host and Arch container) | ✅ |
| Android tests/build/lint | 26 / 26; `assembleDebug` OK; Lint 0 errors | ✅ |
| Security tests | `docs/security/NEXUS_SECURITY_TEST_REPORT.md`; no unresolved critical | ✅ |
| Legacy regression | `docs/PHONEBRIDGE_REGRESSION_REPORT.md` | ✅ |
| Packaging | Ubuntu 24.04 + Arch containers pass (`docs/PACKAGING.md`) | ✅ |
| Docs | README (install, pair, permissions, revoke, privacy, uninstall, troubleshooting, limitations) | ✅ |
| Definition of done (plan) | see below | ✅ with notes |
| On-device Android test | Not performed in this session | ⚠️ **owner must run the tester checklist on a real phone before tagging** |
| Signed release APK | No release keystore in repo (correct). Release build must be signed locally by the owner | ⚠️ owner action |
| PKGBUILD checksum | `sha256sums=('SKIP')` until the `v0.3.0a1` tag tarball exists | ⚠️ owner action at tag time |

## Definition of done (from the plan)

| Requirement | Met | Where |
|---|---|---|
| Android can query Linux `system.status` remotely | ✅ | `RemoteActivity`, `test_paired_device_gets_capabilities_and_status` |
| Android can list and inspect processes safely | ✅ | `process.list` / `process.inspect`, PID-reuse guard |
| Operation registry allow-listed and schema validated | ✅ | `remote_registry.py`, governance tests |
| Per-device, revocable authentication | ✅ | `remote_store.py`, revoke/rotate tests |
| Permissions enforced by the server | ✅ | dispatcher step 7, authorization tests |
| No root execution or unrestricted remote shell | ✅ | no exec path exists; `NoNewPrivileges`; `test_no_subprocess_is_spawned` |
| Request IDs, expiry, timeouts, output caps | ✅ | dispatcher steps 3, 11, 12 |
| Audit metadata without secrets/sensitive payloads | ✅ | `test_audit_has_metadata_only` |
| PhoneBridge notification/call/GPS/battery/map pass regression | ✅ | regression report |
| Arch/Ubuntu install documented and tested where available | ✅ | `docs/PACKAGING.md` |

## Release steps (owner)

1. Run `docs/alpha/TESTER_GUIDE.md` §"Owner smoke test" on a real phone.
2. Build and sign the release APK with your own keystore (never commit it):
   `cd android && ./gradlew :app:assembleRelease` (configure signing locally).
3. Merge `nexus-remote-0.1`, tag `v0.3.0a1`, push the tag.
4. Put the tag tarball's SHA-256 into `packaging/arch/PKGBUILD`.
5. Create the GitHub pre-release with `docs/release/RELEASE_NOTES_0.3.0-alpha1.md` and the signed APK.
