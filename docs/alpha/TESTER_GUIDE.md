# Nexus Alpha — Tester Guide (Day 30)

Thank you for testing! Plan for ~30 minutes. You need a Linux computer (Arch/Manjaro or
Ubuntu 24.04), an Android 8+ phone, and Tailscale on both.

## 1. Install (Linux)
Follow the README "Install" section. Afterwards `systemctl --user status phone-bridge` should
show **active (running)**.

## 2. Install the app
Install the APK you were given (allow "install unknown apps" for your file manager).

## 3. Pair
```bash
phone-bridge remote pair my-phone --grant remote.read
```
In the app: server address = your computer's Tailscale IP (`tailscale ip -4`), paste the
credential into **Nexus device credential**, tap **Save & connect**.

## 4. Test checklist
Tick each item; note anything surprising in the feedback form.

**Connection**
- [ ] Status shows "Connected to <your computer>".
- [ ] `phone-bridge remote devices` shows your phone with a recent `last_seen`.

**Remote screen** (tap *Open Nexus Remote*)
- [ ] Header shows your device id and `remote.read`.
- [ ] System status fills in within ~5 s; "Updated just now" refreshes.
- [ ] CPU/memory roughly match `htop`/`free -h`.
- [ ] Process list loads; sorting and "Only my processes" work; "Load more" works.
- [ ] Tapping a process shows details.
- [ ] Nowhere in the app can you run, stop or change anything.

**Failure handling**
- [ ] Turn on airplane mode: status goes "Stale"/error, no crash. Turn it off: recovers by itself.
- [ ] `systemctl --user restart phone-bridge`: the app reconnects and Remote resumes.
- [ ] `phone-bridge remote ungrant <id> remote.read`: Remote shows the "no permission" message.
- [ ] `phone-bridge remote revoke <id>`: app disconnects within ~5 s and cannot reconnect.
- [ ] Pair again: works, and you must grant `remote.read` again.

**Existing features still work**
- [ ] A WhatsApp/Telegram/Gmail notification appears on the desktop.
- [ ] An incoming call shows a desktop alert.
- [ ] With GPS enabled, the map at http://127.0.0.1:8766 shows your location.

**Accessibility (optional)**
- [ ] With TalkBack on, the Remote screen headings, status and process rows are read sensibly.

## Owner smoke test (before tagging)
Run sections 3–4 above on a real phone, plus: `phone-bridge remote audit` lists your requests
and contains no process names or coordinates.

## Collect logs (only if asked)
```bash
journalctl --user -u phone-bridge --since "30 min ago" > nexus-log.txt
```
Review the file first: it can contain notification text. Remove anything private.

## Feedback
Use `docs/alpha/FEEDBACK_TEMPLATE.md` or open a GitHub issue (bug template).
