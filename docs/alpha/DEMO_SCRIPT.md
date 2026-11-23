# Nexus — 5-minute Demo Script

**Setup:** agent running, phone paired with `remote.read`, screen-mirror the phone.

1. **The problem (30 s).** "My phone and my Linux laptop don't talk. I miss notifications, and
   when I'm away I can't tell if a long build or download is still running."
2. **Notifications & calls (45 s).** Send a WhatsApp message to the phone → it pops up on the
   desktop. Call the phone → "Incoming call" on the desktop.
3. **Map (30 s).** Open http://127.0.0.1:8766: live location trail, battery, export GPX.
   "All local. Location sharing is opt-in on the phone."
4. **Nexus Remote (90 s).** On the phone: Open Nexus Remote → CPU/memory/disk live. Start
   `stress --cpu 4` (or a build) on the laptop → CPU climbs on the phone. Sort processes by
   CPU time, tap the build process → details.
5. **Safety (60 s).** "It's read-only: there is no kill button and no shell." On Linux:
   `phone-bridge remote audit` (metadata only), then `phone-bridge remote revoke <id>`: the
   phone drops within seconds. "Each phone has its own credential; permissions are checked on
   the laptop for every request."
6. **Close (15 s).** "Open source under Apache-2.0, runs on Arch and Ubuntu, alpha testers welcome."
