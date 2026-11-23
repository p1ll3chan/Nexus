# Nexus Regression Matrix (Day 5, updated Day 26)

Maps each feature to its automated test, the command to run it, and the evidence. "Day 1"
is the baseline from `NEXUS_BASELINE.md` (24 Python tests, APK build OK).

Run everything:

```bash
cd phone-bridge && python3 -m unittest discover -s tests -t .
cd android && ./gradlew :app:testDebugUnitTest :app:assembleDebug
```

## Existing PhoneBridge features

| Feature | Automated test | Day 1 | Day 26 | Manual check (Day 26) |
|---|---|---|---|---|
| Notification forwarding + allow-list filter | `test_tracker_and_notifications.NotificationAllowlistTests` (3), `ServerAndWebIntegrationTests` | pass | pass | `simulate`: WhatsApp/Gmail shown, ShoppingApp filtered |
| Notifications under device auth | `HappyPathTests.test_legacy_messages_still_route_with_device_auth` | — | pass | — |
| Call events (ringing/offhook/idle) | `test_protocol.test_call_state_mapping`, `test_call_accepts_legacy_event_field` | pass | pass | `simulate` + `call ringing`: desktop alerts shown |
| GPS location ingest + validation | `test_location_round_trip_and_validation`, `test_location_rejects_out_of_range_coordinates` | pass | pass | `location 12.97 77.59` stored |
| Battery telemetry | `ServerAndWebIntegrationTests` (battery 87) | pass | pass | `where` shows battery |
| Locate request | `test_locate_round_trip` | pass | pass | `POST /api/locate` → `{"ok":true,...}`; `GET` → 404 (intentional) |
| SQLite persistence / query / export | `LocationStoreTests.test_record_query_and_export` | pass | pass | GPX 6 `trkpt`, GeoJSON 7 features |
| Retention | `storage.record(retention_days=…)` exercised by integration test | pass | pass | — |
| CLI (`where`, `send`, `call`, `location`, `ping`, `status`, `token`, `init-config`) | `test_config_auth` (9) | pass | pass | all commands run; `ping` 0.3 ms |
| Map dashboard + `/api/status`, `/api/locations` | `ServerAndWebIntegrationTests` | pass | pass | values match stored fixes |
| Live SSE stream | — (manual) | not covered | manual pass | `/api/events` emitted a `location` event |
| Auth: token compare + rate limit | `AuthTests` (3) | pass | pass | — |
| Config precedence (file/env/CLI) | `ConfigTests` (6) | pass | pass | — |
| Android build | `./gradlew :app:assembleDebug` | pass | pass | APK 6.3 MB |

## New Nexus Remote features

| Feature | Tests | Count |
|---|---|---|
| Protocol schemas + shared fixtures | `test_remote_protocol` + Kotlin `RemoteProtocolFixturesTest` | 14 + 5 |
| Registry + governance | `test_remote_ops.RegistryTests` | 6 |
| `system.status` / `process.list` / `process.inspect` | `test_remote_ops` | 18 |
| nexus.db, identity, grants, approvals, audit | `test_remote_store` | 22 |
| End-to-end + security | `test_remote_server` | 33 |
| Config additions, placeholder token | `test_nexus_config` | 6 |
| Android tracker / runtime / hello / formatting | `RemoteRequestTrackerTest`, `RemoteRuntimeTest`, `MessageProtocolTest` | 21 |

**Totals:** Python 24 → 124 tests; Android 0 → 26 unit tests; Lint 0 errors.
No legacy test was removed or weakened.
