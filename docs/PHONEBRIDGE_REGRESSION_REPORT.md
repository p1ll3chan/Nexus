# PhoneBridge Regression Report (Day 26)

**Date:** 2026-11-23 · **Branch:** `nexus-remote-0.1` · compared with `NEXUS_BASELINE.md` (Day 1).

## Result

All original PhoneBridge flows work after the Nexus Remote changes. No legacy test was
removed or weakened. Two behaviour changes are intentional security fixes (below).

## Automated

| Suite | Day 1 | Day 26 |
|---|---|---|
| `python3 -m unittest discover -s tests -t .` | 24 tests, OK | 124 tests, OK (the original 24 unchanged and passing) |
| `./gradlew :app:assembleDebug` | BUILD SUCCESSFUL | BUILD SUCCESSFUL |
| `./gradlew :app:testDebugUnitTest` | no tests | 26 tests, 0 failures |
| `./gradlew :app:lintDebug` | not run | 0 errors, 20 warnings (pre-existing/cosmetic) |

## Manual (real CLI + server; scratch config, `notify_backend = "console"`, 127.0.0.1)

| Flow | Command | Observed |
|---|---|---|
| Server start | `phone-bridge --config … serve` | listening on WS + map; logs "legacy shared-token login enabled" |
| Scripted phone | `simulate --device pixel-9` | 13 acks; WhatsApp + Gmail shown; ShoppingApp suppressed; call ringing/answered/ended shown |
| Latest fix | `where --history 3` | device, coordinates, ±4.5 m, battery 89%, OSM link |
| One-off events | `send`, `call ringing`, `location 12.97 77.59` | all sent; shown/stored |
| RTT | `ping` | `pong in 0.3 ms` |
| Map status | `GET /api/status` | `total_points 6`, device `pixel-9` |
| History | `GET /api/locations?device=pixel-9&hours=0` | count 6, latest 12.97, battery 88 |
| Exports | `GET /api/export.gpx`, `.geojson` | 6 `trkpt`; 7 features |
| Locate | `POST /api/locate` | `{"ok": true, "requested": 0}` (no phone connected) |
| Live stream | `GET /api/events` + new location | `data: {"event":"location",…}` received |

## Intentional behaviour changes

1. `token = "change-me"` (shipped placeholder) now stops the agent at startup with a clear
   message (Day 3/4 finding, High). The installer always generates a random token. The
   author's own config was checked and is unaffected.
2. `GET /api/locate` now returns 404; only `POST` triggers a locate (prevents a malicious web
   page triggering location requests via `<img src>`). The dashboard button already used POST.

Other hardening with no functional change: device names are HTML-escaped in the dashboard;
config files are written 0600.

## Not verified

- A physical Android phone was not connected in this session. The Android changes were
  verified by build, Lint and JVM unit tests; the wire behaviour of the phone was verified
  with the Python reference client, which speaks the same protocol as the shared fixtures.
  See the tester guide for the on-device checklist.
